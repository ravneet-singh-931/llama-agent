"""Small helpers that had accumulated in api.py.

These were moved out of the 3,460-line `api.py` because none of them depends
on the app, a request, or any other part of the API — they are plain
functions over plain values. Moving them is what makes the next extractions
possible: a route module can import from here without importing `api`, so no
import cycle can form.

Nothing was rewritten in the move. Each function is byte-identical to the one
that was in `api.py`, and `api.py` still re-exports every name, so
`from api import heap_top_k` keeps working.

Why each one lives here:

* `heap_top_k`   — the top-k ranking used by retrieval; `evals/rag_eval.py`
                   imports it by name and measures it.
* `chunk_score`  — best available relevance signal across the two RAG
                   implementations.
* `escape_sse`   — SSE framing (the Next.js client reverses this in one pass).
* `is_db_unavailable`
                 — "can't reach the database" vs "a bug", which decides 503
                   vs 500 on every auth and document route.
* `aiter_in_thread`
                 — consume a blocking iterator without stalling the event
                   loop; carries contextvars to the worker.
"""

import asyncio
import contextvars
import heapq
import time
from collections import OrderedDict
from typing import Any, Dict


def heap_top_k(items, key=lambda x: x, k: int = 5):
    """Return the k items with the largest ``key`` value in O(n log k).

    Uses a min-heap of size k, so it scales far better than a full sort when
    n >> k (e.g. ranking hundreds of retrieved chunks down to the top few).
    """
    if k <= 0:
        return []
    heap = []
    # (value, -seq, item): the sequence number breaks ties so the items
    # themselves (often dicts, which are not orderable) are never compared,
    # and among equal scores the EARLIER item wins (stable, deterministic).
    for seq, it in enumerate(items):
        v = key(it)
        entry = (v, -seq, it)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif (v, -seq) > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    # Min-heap -> return largest-first.
    heap.sort(key=lambda e: (e[0], e[1]), reverse=True)
    return [it for _, _, it in heap]


def chunk_score(chunk: Dict[str, Any]) -> float:
    """Best available relevance signal for a retrieved chunk.

    SimpleRAG emits ``relevance_score``; OptimizedRAG emits ``rerank_score``
    (cross-encoder), ``similarity`` (vector) and ``bm25_score``.
    """
    for field in ("relevance_score", "rerank_score", "similarity", "bm25_score"):
        val = chunk.get(field)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def escape_sse(text: str) -> str:
    """Escape text for SSE.

    SSE data lines must not contain raw CR/LF (they would break the
    line framing). Escape both so multi-line tokens stream safely, and
    escape backslashes first so a literal backslash-n in model output
    (e.g. ``print("a\\nb")`` in code) is not turned into a real newline.
    The Next.js client reverses this in one pass (frontend/src/lib/api.ts).
    """
    return (text.replace("\\", "\\\\")
            .replace("\r", "\\r").replace("\n", "\\n"))


def is_db_unavailable(e: BaseException) -> bool:
    """True for "can't reach/auth to the database" errors (DNS, refused,
    timeout, bad password) as opposed to bugs. Walks the cause chain because
    SQLAlchemy wraps the driver error."""
    seen = 0
    while e is not None and seen < 5:
        name = type(e).__name__
        if isinstance(e, (OSError, ConnectionError, asyncio.TimeoutError)) or name in {
            "OperationalError", "InterfaceError", "InvalidPasswordError",
            "InvalidAuthorizationSpecificationError", "CannotConnectNowError",
            "ConnectionDoesNotExistError", "TooManyConnectionsError",
        }:
            return True
        e = e.__cause__ or e.__context__
        seen += 1
    return False


class StreamFailure:
    """Carries an exception raised on the worker thread back to the consumer."""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException):
        self.exc = exc


_STREAM_END = object()


async def aiter_in_thread(make_iter):
    """Consume a *blocking* iterator (Groq token streams, agent tool loops)
    on a worker thread and yield its items asynchronously.

    Iterating a sync generator directly inside an async SSE generator blocks
    the event loop for every network read, stalling all other users' requests
    and streams. The worker carries the caller's contextvars (tenant/owner).
    If the consumer stops early (client disconnect, timeout) the worker is
    told to stop at the next item; the queue is unbounded so it never blocks.
    """
    import threading

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    ctx = contextvars.copy_context()

    def _put(item):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # loop closed
            stop.set()

    def _worker():
        try:
            for item in make_iter():
                if stop.is_set():
                    break
                _put(item)
        except BaseException as e:  # surfaced to the consumer
            _put(StreamFailure(e))
        finally:
            _put(_STREAM_END)

    loop.run_in_executor(None, ctx.run, _worker)
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                return
            if isinstance(item, StreamFailure):
                raise item.exc
            yield item
    finally:
        stop.set()


class BoundedLRUCache:
    """Bounded LRU cache with optional TTL.

    Evicts the least-recently-used entry when full and treats entries older
    than ``ttl`` seconds as misses. Used to cache repeated document answers so
    identical questions skip the LLM call without growing memory unbounded.
    """

    def __init__(self, maxsize: int = 256, ttl: float = 600.0):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: "OrderedDict[str, tuple]" = OrderedDict()

    def get(self, key: str):
        if key not in self._store:
            return None
        val, ts = self._store[key]
        if self.ttl and (time.monotonic() - ts) > self.ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return val

    def put(self, key: str, val) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (val, time.monotonic())
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

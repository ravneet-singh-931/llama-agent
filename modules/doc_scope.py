"""
Per-owner document scoping.

Every document store (SimpleRAG, VectorStore, KeywordStore, OptimizedRAG)
implements one common interface where each method accepts an optional
``owner`` keyword:

    add_document(path, owner=None)
    search(query, top_k, owner=None)
    get_context(query, top_k, owner=None)
    get_context_for_document(query, filename, top_k, owner=None)
    get_document_chunks(filename, owner=None)
    list_documents(owner=None)
    delete_document(filename, owner=None)
    has_document(filename, owner=None)
    get_cached_answer(query, owner=None)
    cache_answer(query, answer, owner=None)
    invalidate_cache(owner=None)

``owner=None`` means "no filter" (admin / maintenance use only). Request
handlers never call the raw store directly - they wrap it with
:func:`scoped` so that one tenant can never read, search, cache-hit or
overwrite another tenant's documents.
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

_OWNER_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def owner_dir_name(owner: Optional[str]) -> str:
    """Filesystem-safe directory name for an owner id."""
    if not owner:
        return "_shared"
    cleaned = _OWNER_SAFE.sub("_", str(owner))[:80]
    return cleaned or "_shared"


def resolve_filename(filename: str, available: Iterable[str]) -> Optional[str]:
    """Lenient filename match: exact, basename, or basename w/o extension."""
    if not filename:
        return None
    want = filename.strip()
    want_base = os.path.basename(want)
    for name in available:
        base = os.path.basename(name)
        if want in (name, base) or want_base == base or want == os.path.splitext(base)[0]:
            return name
    return None


def _query_tokens(text: str) -> set:
    from modules.rag import STOPWORDS

    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def rank_document_chunks(query: str, docs: List[Dict], top_k: int = 4) -> List[Dict]:
    """Pick the chunks of ONE document that best cover ``query``.

    Frequency-based scoring (topic density beats incidental mentions) plus
    the neighbours of matched chunks, returned in document order.
    """
    if not docs:
        return []
    docs = sorted(docs, key=lambda d: d.get("chunk_index", 0))
    query_words = _query_tokens(query)
    if not query_words:
        return docs[:top_k]

    scored = []
    for doc in docs:
        content_lower = doc.get("content", "").lower()
        freq = 0
        matched = 0
        for w in query_words:
            c = content_lower.count(w)
            if c:
                freq += c
                matched += 1
        scored.append((freq + matched, doc))
    scored.sort(key=lambda x: (-x[0], x[1].get("chunk_index", 0)))
    best = [d for s, d in scored if s > 0][:top_k]
    if not best:
        return docs[:top_k]

    picked = list(best)
    picked_idx = {d.get("chunk_index", 0) for d in picked}
    for _s, d in scored:
        if len(picked) >= top_k + 2:
            break
        idx = d.get("chunk_index", 0)
        if idx in picked_idx:
            continue
        if idx - 1 in picked_idx or idx + 1 in picked_idx:
            picked.append(d)
            picked_idx.add(idx)
    picked.sort(key=lambda x: x.get("chunk_index", 0))
    return picked


def format_chunks(chunks: List[Dict]) -> str:
    return "\n\n".join(
        f"--- Source: {d.get('file', 'unknown')} (chunk {d.get('chunk_index', 0)}) ---\n"
        f"{d.get('content', '')}"
        for d in chunks
    )


def _accepts_owner(fn: Callable) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "owner" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def call_with_owner(fn: Callable, *args, owner: Optional[str] = None, **kwargs):
    """Call ``fn`` passing ``owner`` only if it is set AND supported.

    Lets the optimized pipeline wrap third-party / test stores that predate
    the owner-aware interface.
    """
    if owner is not None and _accepts_owner(fn):
        return fn(*args, owner=owner, **kwargs)
    return fn(*args, **kwargs)


class ScopedDocumentStore:
    """A per-request view of a document store bound to a single owner."""

    def __init__(self, store: Any, owner: str):
        if not owner:
            raise ValueError("ScopedDocumentStore requires an owner")
        self._store = store
        self.owner = str(owner)

    # -- passthrough helpers -------------------------------------------------
    @property
    def raw(self) -> Any:
        return self._store

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ScopedDocumentStore({type(self._store).__name__}, owner={self.owner!r})"

    def _call(self, name: str, *args, default=None, **kwargs):
        fn = getattr(self._store, name, None)
        if fn is None:
            return default
        return call_with_owner(fn, *args, owner=self.owner, **kwargs)

    # -- interface -----------------------------------------------------------
    def add_document(self, file_path: str) -> str:
        return self._call("add_document", file_path)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        return self._call("search", query, top_k, default=[]) or []

    def get_context(self, query: str, top_k: int = 5) -> str:
        return self._call("get_context", query, top_k, default="No documents have been uploaded.")

    def get_context_for_document(self, query: str, filename: str, top_k: int = 4) -> str:
        return self._call("get_context_for_document", query, filename, top_k, default="") or ""

    def get_document_chunks(self, filename: str) -> List[Dict]:
        return self._call("get_document_chunks", filename, default=[]) or []

    def list_documents(self) -> List[str]:
        return list(self._call("list_documents", default=[]) or [])

    def has_document(self, filename: str) -> bool:
        if hasattr(self._store, "has_document"):
            return bool(self._call("has_document", filename))
        return resolve_filename(filename, self.list_documents()) is not None

    def delete_document(self, filename: str) -> int:
        return self._call("delete_document", filename, default=0) or 0

    def get_cached_answer(self, query: str) -> Optional[str]:
        return self._call("get_cached_answer", query)

    def cache_answer(self, query: str, answer: str) -> None:
        self._call("cache_answer", query, answer)

    def invalidate_cache(self) -> None:
        self._call("invalidate_cache")

    def retrieve(self, query: str, history: str = "", top_k: int = 5) -> List[Dict]:
        """History-aware retrieval when the store supports it, else search."""
        if hasattr(self._store, "retrieve"):
            return self._call("retrieve", query, history=history, top_k=top_k, default=[]) or []
        return self.search(query, top_k)

    def verify_answer(self, answer: str, context: str) -> bool:
        fn = getattr(self._store, "verify_answer", None)
        return True if fn is None else bool(fn(answer, context))

    def get_stats(self) -> Dict:
        docs = self.list_documents()
        return {"total_documents": len(docs), "documents": docs, "owner": self.owner}


def scoped(store: Any, owner: Optional[str]) -> Any:
    """Wrap ``store`` for ``owner``. Returns ``store`` unchanged when either is None."""
    if store is None or not owner:
        return store
    if isinstance(store, ScopedDocumentStore):
        if store.owner == str(owner):
            return store
        store = store.raw
    return ScopedDocumentStore(store, owner)

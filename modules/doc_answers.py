"""Document questions: routing, retrieval, answering, and citation checking.

Extracted from `api.py` (3,349 lines, 39 routes) as step 2 of splitting it by
concern. This is the largest single cluster in that file — 23 functions and 19
constants, 658 lines — and it was the most tangled: the routing predicates
(`is_document_question`, `is_doc_follow_up`), the answering path
(`answer_from_documents`) and the citation checking all lived between unrelated
route handlers.

**Nothing was rewritten in the move.** Every function and constant here is
byte-identical to the version in `api.py`; only the dependencies are new:

* `BoundedLRUCache` is imported instead of coming from `api`'s namespace.
* `_load_shared_dict` / `_save_shared_dict` are **injected** by `api.py` at
  import (`bind_shared_dict_store`). They are Redis-backed helpers that live in
  the API's persistence layer, and importing them from here would be a cycle.
  The default is an empty in-memory dict, so this module is usable on its own
  (and importable in a test) without Redis.

One behaviour was removed, and it was dead code: `_get_doc_followup` and
`_set_doc_followup` each did `app.state.doc_followup = shared`. That attribute
was written in three places and **read in none** — verified repo-wide, including
the frontend. The comment above the old helper claimed it was "kept in app.state
for speed", but the only reads went through `_load_shared_dict`, i.e. Redis. The
assignments were removed with the extraction; the Redis round-trip, which is what
actually carries the state, is unchanged.
"""

import logging
import os
import re
from typing import Dict

from modules.api_utils import BoundedLRUCache, chunk_score, heap_top_k
from modules.llm_guard import friendly_llm_error
from modules.model_tiers import resolve_model, should_fallback
from modules.silent import degrade

_log = logging.getLogger(__name__)

# The cache and the lazy Groq client are module state, as they were in api.py.
_groq_client = None

_doc_answer_cache = BoundedLRUCache(maxsize=256, ttl=600)


class DocAnswerUnavailable(Exception):
    """The model could not answer (rate limit, outage). ``str(e)`` is a
    user-facing message; such results are never cached."""


# owner -> generation counter; bumped on upload so exact-match answer-cache
# entries for that owner are never served after their documents change.
_doc_generation: Dict[str, int] = {}


def bump_doc_generation(owner: str) -> None:
    """Invalidate an owner's cached document answers.

    Called when that owner's documents change. The counter is part of every
    cache key (`_doc_cache_key`), so bumping it orphans the old entries instead
    of trying to find and delete them.
    """
    _doc_generation[owner] = _doc_generation.get(owner, 0) + 1

_DOC_QUESTION_PHRASES = [
    "summarize the uploaded", "summarize uploaded", "summary of the uploaded",
    "summarize the file", "summarize the document", "summarize file",
    "sumarize the uploaded", "sumarize uploaded", "sumarize the file",
    "sumarize the document", "sumarize file",
    "about the uploaded", "about the document", "about the file",
    "from the document", "from the file", "in the document", "in the file",
    "from the uploaded", "document says", "file says", "uploaded file",
    "uploaded document", "what does the file", "what does the document",
    "tell me about the file", "tell me about the document",
]

_DOC_INVENTORY_PHRASES = (
    "what documents", "which documents", "what files", "which files",
    "list my documents", "list the documents", "list documents",
    "list my files", "list the files", "list files",
    "show my documents", "show the documents", "show documents",
    "show my files", "show the files", "show files",
    "uploaded documents", "uploaded files",
    "what have i uploaded", "what did i upload",
    "documents have i uploaded", "files have i uploaded",
    "how many documents", "how many files",
    "what is uploaded", "what are the documents", "what are the files",
    "what documents are", "what files are", "see my documents", "see my files",
)

_DOC_REF_RE = re.compile(r"\b(files?|documents?|docs?|pdfs?|spreadsheets?|sheets?|uploads?|uploaded)\b")

_CREATE_REQUEST_RE = re.compile(
    r"^\s*(please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(write|create|generate|build|make|code|implement|draft|design)\b")

_GENERIC_NAME_TOKENS = frozenset(
    "the and for with file files doc docs document documents copy final new untitled".split())

_DOC_QUESTION_REF_WORDS = (
    "file", "document", "uploaded", "upload", "doc", "docs",
    ".txt", ".csv", ".docx", ".pdf", ".py", ".md", ".json",
)

_DOC_QUESTION_INTENTS = (
    "summarize", "summary", "summarise", "sumarize", "describe",
    "what is", "what's", "tell me about", "explain", "show me",
    "read", "give me", "info about", "information about",
)

_DOC_FOLLOWUP_PHRASES = [
    "what about", "how about", "and the", "and what", "tell me more",
    "more about", "what else", "anything else", "explain", "regarding",
    "about that", "that file", "this document", "the document", "the file",
    "does it", "what does it", "it says", "in it", "in the doc",
    "from the doc", "in that document", "show me", "can you show",
    "any details",
]

_THIN_ANSWER_PHRASES = (
    "doesn't contain", "does not contain", "doesn't mention", "does not mention",
    "doesn't include", "does not include", "don't have", "do not have",
    "not found", "no information", "cannot find", "can't find", "could not find",
    "couldn't find", "not available in", "not in the document", "not in the excerpts",
    "not covered", "doesn't say", "does not say", "no mention", "no details",
    "no such", "unable to find", "not included in",
)

_WHOLE_DOC_RE = re.compile(
    r"\b(summar\w*|sumar\w*|overview|outline|describe|"
    r"what\s+(is|'s)\s+(this|that|the|my)\s+(\w+\s+)?(file|document|doc)\b|"
    r"what\s+(are|is)\s+(these|those|the|my)\s+(\w+\s+)?(files|documents|docs)\b|"
    r"what\s+(does|do)\s+(it|they|this|the\s+\S+\s+(file|document|doc))\s+(contain|cover)|"
    r"tell\s+me\s+about\s+(this|the|my)\s+(\w+\s+)?(file|document|doc))",
    re.I,
)

_DOC_CUT_NOTE = "_(The answer was cut off at the length limit. Ask a narrower question for the rest.)_"

_DOC_BAD_CITATION_NOTE = (
    "_(Citation check: this answer cites {bad}, which is not among the "
    "excerpts it was given. Treat that citation as unverified.)_")

_DOC_UNSOURCED_NOTE = (
    "_(Citation check: this answer makes claims without citing a document. "
    "Treat it as unverified.)_")

_DOC_UNVERIFIED_NOTE = ("_(Some statements in this answer could not be verified "
                        "against the document excerpts - please double-check them.)_")

_LEGACY_DOC_ERRORS = ("Could not summarize the documents right now",
                      "Could not compare the documents right now")

_COMPARE_SIGNALS = (
    "compare", "comparison", "versus", " vs ", "vs.", "contrast",
    "difference between", "differences between",
)

_COMPARE_NOISE_WORDS = frozenset(
    "compare comparison contrast versus vs differences difference between "
    "doc docs document documents file files both each says say tell what "
    "about and the of in for on with their they".split()
)

# --------------------------------------------------------------------------
# Injected persistence hook (api.py binds the real Redis-backed helpers).
# Defaults to in-memory so this module stands alone and stays importable.
# --------------------------------------------------------------------------

def _default_load_shared(key, default):
    return dict(default)


def _default_save_shared(key, value):
    return None


_load_shared_dict = _default_load_shared
_save_shared_dict = _default_save_shared
_DOC_FOLLOWUP_KEY = "llama:doc_followup"


def bind_shared_dict_store(load, save, followup_key):
    """Called by api.py at import so this module uses the real Redis helpers."""
    global _load_shared_dict, _save_shared_dict, _DOC_FOLLOWUP_KEY
    _load_shared_dict, _save_shared_dict = load, save
    _DOC_FOLLOWUP_KEY = followup_key


def is_document_question(message: str, rag=None) -> bool:
    """True if the user is asking about their uploaded documents/files.

    When a RAG store is supplied, a multi-document comparison ("compare X
    and Y", both uploaded files) also counts as a document question so it
    routes to the RAG answerer instead of a generic agent.
    """
    m = message.lower()
    if any(p in m for p in _DOC_QUESTION_PHRASES):
        return True
    if _is_document_inventory_question(m):
        return True
    # Generic pattern: a document-intent phrase + a reference to a file.
    # Catches forms the exact phrase list misses, e.g. "summarize the CRM
    # file" or "what is the Captainassistant.py file?".
    if (
        any(w in m for w in _DOC_QUESTION_REF_WORDS)
        and any(i in m for i in _DOC_QUESTION_INTENTS)
    ):
        return True
    if rag is not None and _detect_compare_documents(message, rag)[0]:
        return True
    # A question that names one of the user's uploaded files ("what does the
    # crm file say about X?", "according to the CRM-3 document, ...").
    if rag is not None and not _CREATE_REQUEST_RE.match(m) and _names_uploaded_document(m, rag):
        return True
    return False

def _names_uploaded_document(message_lower: str, rag) -> bool:
    """True if the message names one of the uploaded files: its full file name,
    or a distinctive word of its name within 3 words of a file/document word
    ("the crm file", "the CRM-3 document", "the sales sheet") - not just both
    words somewhere ("what file format suits sales data?"). Pure helper."""
    tokens = re.findall(r"[a-z0-9]+", message_lower)
    ref_pos = [i for i, t in enumerate(tokens) if _DOC_REF_RE.fullmatch(t)]
    try:
        docs = rag.list_documents() or []
    except Exception:
        return False
    for path in docs:
        base = os.path.basename(str(path)).lower()
        if base and base in message_lower:
            return True
        if not ref_pos:
            continue
        stem = os.path.splitext(base)[0]
        name_words = {t for t in re.findall(r"[a-z0-9]+", stem)
                      if len(t) >= 3 and not t.isdigit() and t not in _GENERIC_NAME_TOKENS}
        if any(t in name_words and any(abs(i - r) <= 3 for r in ref_pos)
               for i, t in enumerate(tokens)):
            return True
    return False

def _detect_compare_documents(query: str, rag) -> tuple:
    """Detect a request to compare two uploaded documents.

    Returns (True, [doc1, doc2]) with resolved store paths, or (False, []).

    Works for ANY pair of uploaded files (nothing is hardcoded): it only
    inspects the query text and the store's document list. Pure helper
    (testable offline).
    """
    m = (query or "").lower()
    if not any(sig in m for sig in _COMPARE_SIGNALS):
        return False, []
    available = rag.list_documents()
    if len(available) < 2:
        return False, []
    scored = [(path, _doc_match_score(m, path)) for path in available]
    scored = [(p, s) for p, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])
    if len(scored) < 2:
        return False, []
    return True, [p for p, _s in scored[:2]]

def _is_document_inventory_question(message: str) -> bool:
    """True if the message asks to LIST the user's uploaded documents/files
    (e.g. "what documents have I uploaded?") instead of asking about the
    content of one specific file."""
    m = (message or "").lower()
    return any(p in m for p in _DOC_INVENTORY_PHRASES)

def is_doc_follow_up(message: str, last_doc_query: str) -> bool:
    """True if `message` continues a previous document conversation.

    Pure helper (testable offline): `last_doc_query` is the last message in
    this conversation that asked about an uploaded document ("" if none).
    """
    if not message or not last_doc_query:
        return False
    m = message.lower()
    return any(p in m for p in _DOC_FOLLOWUP_PHRASES)

async def _doc_followup_query(conversation_id: str, message: str) -> str:
    """RAG query for a follow-up message, or "" if it is not a follow-up.

    Combines the original document question with the new message so the
    retriever still finds the right chunks ("summarize the CRM file
    what about the pricing column?").
    """
    last = (await _get_doc_followup()).get(conversation_id, "")
    if is_doc_follow_up(message, last):
        return f"{last} {message}".strip()
    return ""

async def _get_doc_followup() -> Dict[str, str]:
    """Lazily-loaded, replica-shared per-conversation doc-follow-up anchors."""
    shared = await _load_shared_dict(_DOC_FOLLOWUP_KEY, {})
    return shared

async def _set_doc_followup(conversation_id: str, message: str) -> None:
    """Remember that this conversation last asked a document question.

    Mirrored to Redis so follow-ups route correctly on any replica (P0 #2).
    """
    shared = await _load_shared_dict(_DOC_FOLLOWUP_KEY, {})
    shared[conversation_id] = message
    await _save_shared_dict(_DOC_FOLLOWUP_KEY, shared)

def _doc_completion_text(resp) -> str:
    """The answer text; a truncated answer with almost nothing written is
    returned as '' (thin -> the caller's second pass retries), a long one
    keeps what was written plus a visible note."""
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    if getattr(choice, "finish_reason", None) == "length":
        if len(text) < 200:
            return ""
        text += "\n\n" + _DOC_CUT_NOTE
    return text

def _doc_answer_tokens() -> int:
    """Answer budget for document questions (DOC_ANSWER_MAX_TOKENS). The fast
    model is a reasoning model: its hidden thinking counts against this
    budget, and at 1024 a tricky question could end mid-sentence."""
    try:
        return max(512, int(os.getenv("DOC_ANSWER_MAX_TOKENS", "2048")))
    except ValueError:
        return 2048

def is_thin_answer(text: str, min_chars: int = 60) -> bool:
    """True if a document answer is too thin to trust (no real content).

    Pure helper (testable offline). An answer is thin when it is missing,
    extremely short, or explicitly says the documents don't contain the
    information.
    """
    if not text:
        return True
    lowered = text.lower()
    if len(lowered.strip()) < min_chars:
        return True
    return any(p in lowered for p in _THIN_ANSWER_PHRASES)

def _doc_cache_key(rag, query: str) -> str:
    owner = getattr(rag, "owner", None) or "_global"
    return f"{owner}|{_doc_generation.get(owner, 0)}|{query}"

def _complete_with_fallback(client, messages, max_tokens):
    """Run a chat completion on the STRONG tier; on rate-limit/availability
    errors retry once on the FAST tier so strong-model quota limits never
    break one-shot answers."""
    model = resolve_model("strong")
    try:
        return client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
    except Exception as e:
        if should_fallback(e, "strong"):
            return client.chat.completions.create(
                model=resolve_model("fast"), messages=messages, max_tokens=max_tokens
            )
        raise

def _get_groq_client(api_key: str):
    global _groq_client
    if _groq_client is None:
        from multi_agent import InstrumentedGroqClient
        _groq_client = InstrumentedGroqClient(api_key=api_key, component="doc_answer")
    return _groq_client

def _retrieve_context(rag, query: str, top_k: int = 6, history: str = "") -> str:
    """Retrieve the top-k most relevant chunks for a query, ranked with a heap.

    Uses ``rag.retrieve`` (OptimizedRAG: history-aware rewrite + optional
    decomposition + hybrid/reranked search) or ``rag.search`` (scored
    chunks), then ``heap_top_k`` for O(n log k) ranking on the best
    available score (see ``chunk_score``). Falls back to ``rag.get_context``
    (which returns all documents when nothing matches) if retrieval is
    unavailable or empty.
    """
    try:
        retrieve = getattr(rag, "retrieve", None)
        if callable(retrieve):
            chunks = retrieve(query, history=history, top_k=top_k * 3)
        else:
            chunks = rag.search(query, top_k * 3)
    except Exception:
        chunks = []
    if not chunks:
        try:
            return rag.get_context(query, top_k=top_k)
        except Exception:
            return ""
    top = heap_top_k(chunks, key=chunk_score, k=top_k)
    context = "Relevant document excerpts:\n\n"
    for c in top:
        context += (
            f"--- Source: {c.get('file', '?')} (chunk {c.get('chunk_index', '?')}) ---\n"
            f"{c.get('content', '')}\n\n"
        )
    return context

def _reformulate_doc_query(query: str, context: str, client) -> str:
    """Ask the fast model to rephrase a document question into a more
    specific search query for the second retrieval pass. Returns "" if no
    usable reformulation is produced (then the second pass is skipped)."""
    prompt = (
        "You are helping search the user's uploaded documents.\n\n"
        f"Document excerpts:\n{context[:3000]}\n\n"
        f"The user asked: {query}\n\n"
        "The first retrieval did not answer this well. Rephrase the user's "
        "question into ONE more specific search query that would match the "
        "details they are after (include the exact topic terms from the "
        "question). Reply with only the query text, nothing else."
    )
    try:
        resp = client.chat.completions.create(
            model=resolve_model("fast"),
            messages=[
                {"role": "system", "content": "You reformulate search queries. Reply with one query only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )
        q = (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""
    # Keep only plausible queries (a bare "the" is useless).
    if len(q) < 6:
        return ""
    # Strip a leading bullet/number/dash/quote if the model added one.
    q = q.lstrip("-*•0123456789).\"'").strip()
    return q

def _usable_doc_answer(text) -> bool:
    """True for a real answer that may be cached or served from the cache:
    not empty, not cut off, not an error message."""
    if not isinstance(text, str) or not text.strip():
        return False
    t = text.strip()
    return (_DOC_CUT_NOTE not in t and _DOC_UNVERIFIED_NOTE not in t
            and not t.startswith("⚠️")
            and not t.startswith(_LEGACY_DOC_ERRORS))

def _check_doc_citations(answer: str, context: str) -> str:
    """Validate the answer's citations against the excerpts it was given.

    Detector, not blocker: an invented citation is appended to the answer as a
    visible note and counted in a metric. Silently stripping or rewording the
    citation would hide the model's mistake; showing it lets the reader judge
    and makes the failure countable.

    Returns ``answer`` unchanged when the citations hold up.
    """
    try:
        from modules.citations import check_citations
        report = check_citations(answer, context)
    except Exception:
        return answer                      # never break an answer over this
    if report["ok"]:
        return answer
    try:
        from modules.prometheus_metrics import record_citation_failure
        if report["invented_files"]:
            record_citation_failure("invented_file")
        elif report["invalid"]:
            record_citation_failure("wrong_chunk")
        elif report["unsourced"]:
            record_citation_failure("unsourced")
    except Exception as _e:
        degrade("citation quality metric not recorded for this answer", _e)
    if report["unsourced"]:
        return answer + "\n\n" + _DOC_UNSOURCED_NOTE
    # Spell the citation the way the prompt asks the model to write it, so the
    # note reads as a quote of what the answer did rather than a new format.
    bad = ", ".join(f"{f} (chunk {c})"
                    for f, c in (report["invented_files"] or report["invalid"]))
    return answer + "\n\n" + _DOC_BAD_CITATION_NOTE.format(bad=bad)

def _ask_doc_question(client, query: str, context: str) -> str:
    """One LLM answer about uploaded documents from the retrieved context.
    Raises DocAnswerUnavailable (friendly message) when the model can't answer."""
    if _WHOLE_DOC_RE.search(query or ""):
        opening = (
            "First, in one short line, state exactly what this document IS: its "
            "type (e.g. Python application, CSV dataset, Word/Markdown/PDF "
            "document), its purpose, and the main technology or content it "
            "involves. Use the file names and excerpt content shown above - do "
            "not guess beyond what the excerpts show.\n"
            "Then answer"
        )
    else:
        opening = ("Answer the question directly - do not start by describing "
                   "the document. Answer")
    prompt = (
        "You are answering a question about the user's uploaded documents.\n\n"
        f"Document excerpts:\n{context}\n\n"
        f"Question: {query}\n\n"
        f"{opening} the question using ONLY the excerpts above. Cite the "
        "source for EVERY claim by appending the exact file name and chunk "
        "from the 'Source:' labels in parentheses, e.g. (CRM-3.docx, chunk "
        "2) - every sentence that makes a factual statement must end with "
        "such a citation. Never cite a file that is not in the excerpts, "
        "and never invent claims the excerpts do not support. Be concise "
        "and clear, using short paragraphs or bullets where helpful. Never "
        "dump raw excerpts. If the excerpts don't contain the answer, say "
        "so plainly."
    )
    try:
        resp = _complete_with_fallback(
            client,
            [
                {"role": "system", "content": "You are a helpful assistant that answers questions about uploaded documents."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=_doc_answer_tokens(),
        )
    except Exception as e:
        logging.getLogger(__name__).warning("document answer failed: %s", e)
        raise DocAnswerUnavailable(friendly_llm_error(e)) from e
    return _doc_completion_text(resp)

def _compare_documents(query: str, files, rag, client) -> str:
    """Answer a multi-document comparison using per-document labeled context."""
    parts = []
    # Retrieve each document on the TOPIC (file names + comparison framing
    # stripped) so retrieval is not diluted by the request's own words.
    topic = _distill_compare_query(query, files)
    for f in files:
        ctx = rag.get_context_for_document(topic or query, f, top_k=4)
        if ctx:
            parts.append(ctx)
    if not parts:
        return "I could not find the documents to compare. Upload them on the Documents page first."
    combined = "\n\n".join(parts)
    prompt = (
        "You are comparing two uploaded documents.\n\n"
        f"Document excerpts (each labeled with its exact source file and "
        f"chunk number):\n{combined}\n\n"
        f"Comparison request: {query}\n\n"
        "Answer in this structure:\n"
        "1. One line about what each document IS (type, purpose) - label "
        "each with its exact file name from the 'Source:' labels.\n"
        "2. For EACH document, a short subsection headed by its exact file "
        "name, stating what THAT document says about the topic. Quote the "
        "relevant text word-for-word from the excerpt, then cite the exact "
        "chunk number in parentheses, e.g. (CRM-3.docx, chunk 2).\n"
        "3. Key similarities and differences, each cited the same way.\n"
        "CRITICAL: a (file, chunk) citation may ONLY be attached to a claim "
        "taken word-for-word from that exact labeled excerpt. Never attach "
        "one document's chunk to a claim about the other document - if a "
        "document does not discuss the topic, say so plainly instead of "
        "guessing. Never invent facts, file names, or chunk numbers.\n"
        "Use ONLY the excerpts above, and never dump raw excerpts into the "
        "answer."
    )
    try:
        resp = _complete_with_fallback(
            client,
            [
                {"role": "system", "content": "You are a careful document analyst."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=_doc_answer_tokens(),
        )
    except Exception as e:
        logging.getLogger(__name__).warning("document comparison failed: %s", e)
        return friendly_llm_error(e)
    return (_doc_completion_text(resp)
            or "⚠️ The comparison couldn't be completed. Please ask again.")

def _distill_compare_query(query: str, files) -> str:
    """Extract the retrieval TOPIC from a comparison request.

    Drops the file names being compared and the comparison framing words, so
    per-document retrieval targets the actual subject instead of matching
    generic words:

    'Compare what the CRM doc and sample.txt say about pricing'
        -> 'pricing'
    'differences between the budget and the sales forecast' (files
    financials.xlsx / other.xlsx) -> 'budget sales forecast'

    Pure helper (testable offline).
    """
    m = (query or "").lower()
    drop = set()
    for p in files or []:
        base = os.path.basename(p).lower()
        stem = os.path.splitext(base)[0]
        drop.add(base)
        drop.add(stem)
        # Each alphanumeric piece of the stem too (e.g. "crm-3" -> "crm").
        drop.update(re.findall(r"[a-z0-9]+", stem))
        # The file extension token (txt/csv/pdf/docx) is a generic type tag.
        ext = os.path.splitext(base)[1].lstrip(".")
        if ext:
            drop.add(ext)
    toks = [
        t for t in re.findall(r"[a-z0-9]+", m)
        if t not in drop and t not in _COMPARE_NOISE_WORDS
    ]
    return " ".join(toks)

def _doc_match_score(query_lower: str, path: str) -> int:
    """How strongly a query references an uploaded document path.

    Direct references (the full path or its file name appears in the query)
    score 10; additionally, each query token that appears in the file name's
    stem scores 1 (e.g. "crm" matches "CRM-3.docx", "sample" matches
    "sample.txt"). Pure helper (testable offline).
    """
    base = os.path.basename(path).lower()
    stem = os.path.splitext(base)[0]
    score = 0
    if path.lower() in query_lower or base in query_lower:
        score += 10
    stem_tokens = set(re.findall(r"[a-z0-9]+", stem))
    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    score += len(stem_tokens & query_tokens)
    return score

def answer_from_documents(query: str, rag) -> str:
    """Answer a question about uploaded documents with a CLEAN, LLM-written
    answer (instead of dumping raw chunk JSON / excerpts).

    Handles multi-document comparisons ("compare X and Y") with per-document
    retrieval, then a two-pass retrieval for regular questions: if the first
    answer is "thin" (too short, or the model says the documents don't
    contain the information), the question is reformulated into a more
    specific query and the documents are searched once more before giving up.
    """
    # Inventory question ("what documents have I uploaded?") -> list the
    # actual uploads straight from the index; no retrieval needed.
    if _is_document_inventory_question(query):
        try:
            files = sorted(set(rag.list_documents()))
        except Exception:
            files = []
        if not files:
            return ("No documents have been uploaded yet. "
                    "Upload a file on the Documents page first, then ask me about it.")
        listing = "\n".join(f"- {os.path.basename(f) or f}" for f in files)
        label = "document" if len(files) == 1 else "documents"
        return f"You have {len(files)} uploaded {label}:\n{listing}"

    # Fast in-memory LRU (exact-match, TTL-bounded) - checked before the LLM.
    # Keyed by owner + that owner's upload generation: never cross-tenant,
    # never stale after a re-upload.
    cache_key = _doc_cache_key(rag, query)
    cached = _doc_answer_cache.get(cache_key)
    if _usable_doc_answer(cached):
        return cached

    # Semantic Cache (owner-scoped): serve a cached answer if one exists
    # (an error cached by an older version is ignored -> answered fresh).
    get_cached = getattr(rag, "get_cached_answer", None)
    cached = get_cached(query) if callable(get_cached) else None
    if _usable_doc_answer(cached):
        _doc_answer_cache.put(cache_key, cached)
        return cached

    def _remember(answer: str) -> None:
        # Never cache an empty, cut-off or error answer: asking again must retry.
        if not _usable_doc_answer(answer):
            return
        cache = getattr(rag, "cache_answer", None)
        if callable(cache):
            try:
                cache(query, answer)
            except Exception as _e:
                degrade("answer cache write failed (answer still returned)", _e)
        _doc_answer_cache.put(cache_key, answer)

    try:
        context = _retrieve_context(rag, query)
    except Exception:
        context = ""
    if not context or context == "No documents have been uploaded.":
        return "No documents have been uploaded yet. Upload a file on the Documents page first, then ask me about it."
    if "No documents" in context:
        return "No documents have been uploaded yet. Upload a file on the Documents page first, then ask me about it."

    from modules.secrets import get_secret
    api_key = os.environ.get("GROQ_API_KEY", "") or get_secret("GROQ_API_KEY", default="")
    if not api_key:
        return context
    client = _get_groq_client(api_key)

    # Multi-document comparison: retrieve per document and answer with a
    # comparison instead of a single mixed context.
    is_compare, compare_files = _detect_compare_documents(query, rag)
    if is_compare:
        return _compare_documents(query, compare_files, rag, client)

    try:
        first = _ask_doc_question(client, query, context)
    except DocAnswerUnavailable as e:
        return str(e)  # friendly message; not cached, so asking again retries
    retry_error = ""  # set if the second pass hits a rate limit/outage

    # Two-pass RAG: retry with a reformulated search when the first answer is
    # thin, or when Corrective RAG (OptimizedRAG.verify_answer, RAG_CRAG=1)
    # finds claims the excerpts don't support. Thin answers skip the check.
    verify = getattr(rag, "verify_answer", None)
    thin = is_thin_answer(first)
    unsupported = bool(not thin and callable(verify) and not verify(first, context))

    if unsupported or thin:
        reformulated = _reformulate_doc_query(query, context, client)
        if reformulated and reformulated.lower() != query.strip().lower():
            try:
                context2 = _retrieve_context(rag, reformulated)
            except Exception:
                context2 = ""
            if context2 and context2 != context and "No documents" not in context2:
                # Combine both retrievals (skip if the second is just a
                # substring of the first - i.e. nothing new was found).
                combined = context if context2 in context else context + "\n\n" + context2
                try:
                    # The reformulation is a SEARCH query; the answer is still
                    # to the user's own question.
                    answer = _ask_doc_question(client, query, combined)
                except DocAnswerUnavailable as e:
                    answer = ""
                    retry_error = str(e)
                if answer and unsupported and not verify(answer, combined):
                    return _check_doc_citations(answer, combined) + "\n\n" + _DOC_UNVERIFIED_NOTE
                if answer:
                    answer = _check_doc_citations(answer, combined)
                    _remember(answer)
                    return answer

    if not first:
        return retry_error or (
            "⚠️ The answer couldn't be completed (the model ran out of room). "
            "Please ask again, or ask a narrower question.")
    if unsupported:
        return _check_doc_citations(first, context) + "\n\n" + _DOC_UNVERIFIED_NOTE
    first = _check_doc_citations(first, context)
    _remember(first)
    return first
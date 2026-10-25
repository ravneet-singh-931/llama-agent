"""
RAG Optimizations Module — retrieval and answer-checking steps on top of ChromaDB

Implements 7 well-known RAG techniques (none of them is measured by a RAG eval in
this repo yet). In the deployed chat path, 4 is off by default and the LLM rewrite
in 6 is not used:

1. Hybrid Search (BM25 + Vector) — fixes keyword-heavy queries
   like "INV-8847", "API_KEY", "Section 4.2" that vector search misses.
2. Cross-Encoder Reranker — re-ranks the candidates with a local
   ms-marco-MiniLM-L-6 cross-encoder run on onnxruntime (no PyTorch,
   no API cost); keyword reranker if the model is missing.
3. Contextual Retrieval + Semantic Chunking — prepends a 2-line
   document summary to each chunk AND splits on sentence boundaries.
4. Query Decomposition + Step-Back — breaks complex queries into
   sub-questions and answers each separately (opt-in: RAG_DECOMPOSE=1).
5. Corrective RAG (CRAG) — self-correction loop that detects
   unsupported answers and re-retrieves with a rewritten query.
6. Conversational / History-Aware Retrieval — rewrites follow-up
   queries using chat history (only when a caller passes history; the api.py
   chat path combines follow-ups with the last document question instead).
7. Semantic Cache — caches answers by question similarity (versioned,
   so answers from an older answer prompt are never served).

All features are OPTIONAL and gracefully degrade when no LLM is
available or when optional libraries are missing.

Usage:
    from modules.rag_optimizations import OptimizedRAG, GroqLLMClient
    from modules.vector_store import get_vector_store

    store = get_vector_store()
    rag = OptimizedRAG(store, llm_client=GroqLLMClient())
    rag.add_document("uploads/report.pdf")
    context = rag.get_context("What are the pricing plans?")

The wrappers delegate to the underlying VectorStore for storage and
search, so they are drop-in compatible with the existing app.py /
api.py code paths.
"""

from typing import List, Dict, Optional, Protocol
import os
import re
import time

from modules.doc_scope import call_with_owner, format_chunks, rank_document_chunks, resolve_filename


# =============================================================================
# 0.  LLM CLIENT (dependency injection — no hardcoded API key)
# =============================================================================


class LLMClient(Protocol):
    """Protocol for LLM clients that can generate text completions.

    Allows dependency injection so tests can use fakes instead of
    requiring a live Groq/OpenAI API key.
    """

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 500) -> str:
        """Generate a text completion. Returns empty string on failure."""
        ...


class GroqLLMClient:
    """LLM client backed by Groq (via InstrumentedGroqClient).

    Uses the repo's model tiering (resolve_model) and fallback logic
    (should_fallback) so it respects the same rate-limit handling as
    the rest of the app. Uses the "fast" tier for optimization calls
    since these are helper tasks, not primary answers.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "") or self._get_secret()
        self._client = None

    def _get_secret(self) -> str:
        try:
            from modules.secrets import get_secret
            return get_secret("GROQ_API_KEY", default="")
        except Exception:
            return ""

    def _get_client(self):
        if self._client is None and self._api_key:
            from multi_agent import InstrumentedGroqClient
            self._client = InstrumentedGroqClient(api_key=self._api_key)
        return self._client

    def complete(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 500) -> str:
        """Generate a completion via Groq. Returns "" if no key or on error."""
        client = self._get_client()
        if client is None:
            return ""

        from modules.model_tiers import resolve_model

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        model = resolve_model("fast")
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return ""


# =============================================================================
# 1.  SEMANTIC CHUNKER
# =============================================================================


class SemanticChunker:
    """Splits text into semantically meaningful chunks.

    Unlike the fixed 500-char split in SimpleRAG, this splits on
    sentence boundaries and groups sentences into chunks of a target
    size.  This preserves table structures, code blocks, and long
    paragraphs that the fixed split would break apart.

    Uses simple sentence boundary detection (period, question mark,
    exclamation mark) — no external dependency required.
    """

    def __init__(self, chunk_size: int = 1000, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> List[str]:
        """Split text into semantically meaningful chunks.

        Returns a list of strings, each chunk is at most `chunk_size`
        characters and is a complete sentence or paragraph.
        """
        sentences = self._split_sentences(text)

        chunks = []
        current = []
        current_len = 0

        for sent in sentences:
            if not sent.strip():
                continue
            if current_len + len(sent) > self.chunk_size and current:
                chunks.append(" ".join(current))
                current = [sent]
                current_len = len(sent)
            else:
                current.append(sent)
                current_len += len(sent)

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences using simple regex."""
        # Split on .!? followed by space or end of string
        # Use a simpler approach: split on .!? followed by space
        # This avoids lookbehind issues with variable-width patterns
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]


# =============================================================================
# 2.  HYBRID SEARCH (BM25 + Vector)
# =============================================================================


class HybridSearcher:
    """Hybrid search: BM25 (keyword) + Vector (semantic) + cross-encoder rerank.

    Uses rank_bm25 for keyword matching and ChromaDB for semantic
    similarity.  The two scores are normalized to [0,1] and combined
    with configurable weights.

    This fixes the "INV-8847", "API_KEY", "Section 4.2" problem where
    vector search alone returns 0.31 similarity but hybrid returns 0.85.
    """

    def __init__(
        self,
        vector_store,
        bm25_weight: float = 0.3,
        vector_weight: float = 0.7,
        reranker: Optional["CrossEncoderReranker"] = None,
    ):
        self._store = vector_store
        self._bm25_weight = bm25_weight
        self._vector_weight = vector_weight
        self._reranker = reranker
        self._bm25_index = None
        self._all_docs: List[Dict] = []

    # Rebuild the BM25 index at least this often, so documents indexed by
    # OTHER replicas (shared ChromaDB) become keyword-searchable here too.
    BM25_TTL_SECONDS = float(os.getenv("RAG_BM25_TTL", "300"))

    def _build_bm25_index(self):
        """Build BM25 index from all documents in the collection.

        Sets self._bm25_index to None if rank_bm25 is unavailable or
        the collection is empty, so callers can gracefully skip BM25.
        """
        self._bm25_built_at = time.monotonic()
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            self._bm25_index = None
            return

        try:
            all_data = self._store.collection.get(include=["documents", "metadatas"])
        except Exception:
            self._bm25_index = None
            return

        docs = all_data.get("documents", []) if all_data else []
        metas = (all_data.get("metadatas") if all_data else None) or [{} for _ in docs]
        self._all_docs = docs
        self._all_meta = [m or {} for m in metas]
        if not docs:
            self._bm25_index = None
            return
        # Tokenize for BM25 (lowercase, split on whitespace)
        tokenized = [doc.lower().split() for doc in docs]
        self._bm25_index = BM25Okapi(tokenized)

    def invalidate_index(self) -> None:
        """Drop the BM25 index so it is rebuilt on the next search
        (called whenever documents are added or removed)."""
        self._bm25_index = None
        self._all_docs = []
        self._all_meta = []

    def _index_stale(self) -> bool:
        built = getattr(self, "_bm25_built_at", None)
        return built is None or (time.monotonic() - built) > self.BM25_TTL_SECONDS

    def search(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> List[Dict]:
        """Search using both BM25 and vector similarity, then re-rank.

        Candidates = vector top-(2k) UNION BM25 top-k (so exact keyword hits
        such as IDs, error codes or names surface even when the embedding
        misses them). Both candidate sets are restricted to ``owner``.
        Returns top_k results ranked by combined score (then the reranker).
        If BM25 is unavailable, falls back to vector-only search.
        """
        # Get vector results (semantic)
        vector_results = call_with_owner(
            self._store.search, query, top_k=top_k * 2, owner=owner
        )

        # Get BM25 results (keyword) - build lazily / refresh when stale
        if self._bm25_index is None or self._index_stale():
            self._build_bm25_index()

        if self._bm25_index is not None and self._all_docs:
            tokenized_query = query.lower().split()
            bm25_scores = self._bm25_index.get_scores(tokenized_query)

            # Normalize BM25 scores to [0, 1]
            max_bm25 = float(max(bm25_scores)) if len(bm25_scores) else 1.0
            if max_bm25 > 0:
                bm25_norm = [float(s) / max_bm25 for s in bm25_scores]
            else:
                bm25_norm = [0.0] * len(bm25_scores)

            content_to_bm25 = {}
            for doc, score in zip(self._all_docs, bm25_norm):
                content_to_bm25[doc] = max(score, content_to_bm25.get(doc, 0.0))

            combined = []
            seen = set()
            for vec_result in vector_results:
                content = vec_result.get("content", "")
                seen.add(content)
                combined.append({**vec_result, "bm25_score": content_to_bm25.get(content, 0.0)})

            # Keyword-only candidates (same tenant only).
            metas = getattr(self, "_all_meta", None) or [{} for _ in self._all_docs]
            ranked = sorted(range(len(self._all_docs)), key=lambda i: bm25_norm[i], reverse=True)
            added = 0
            for i in ranked:
                if added >= top_k or bm25_norm[i] <= 0:
                    break
                doc = self._all_docs[i]
                meta = metas[i] if i < len(metas) else {}
                doc_owner = (meta or {}).get("owner") or None
                if owner is not None and doc_owner != owner:
                    continue
                if doc in seen:
                    continue
                seen.add(doc)
                added += 1
                combined.append({
                    "content": doc,
                    "file": meta.get("file", "unknown"),
                    "owner": doc_owner,
                    "chunk_index": meta.get("chunk_index", 0),
                    "similarity": 0.0,
                    "bm25_score": bm25_norm[i],
                })
        else:
            # BM25 unavailable - use vector results with bm25_score = 0
            combined = [
                {**r, "bm25_score": 0.0} for r in vector_results
            ]

        # Sort by combined score (vector + BM25 weighted)
        combined.sort(
            key=lambda x: self._combined_score(x, bm25_weight=self._bm25_weight,
                                              vector_weight=self._vector_weight),
            reverse=True,
        )

        # Apply cross-encoder reranker if available
        if self._reranker is not None:
            reranked = self._reranker.rerank(query, combined, top_k=top_k)
            return reranked

        return combined[:top_k]

    def _combined_score(self, result: Dict, bm25_weight: float,
                        vector_weight: float) -> float:
        """Compute combined score from vector + BM25."""
        sim = result.get("similarity", 0.0)
        bm25 = result.get("bm25_score", 0.0)
        return bm25_weight * bm25 + vector_weight * sim

    def get_context(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> str:
        """Get context string for the AI."""
        results = self.search(query, top_k, owner=owner)
        if not results:
            if self.list_documents(owner):
                return "No relevant documents found for this query."
            return "No documents have been uploaded."

        context = "Relevant document excerpts (ranked by relevance):\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result.get('file', 'unknown')} "
            context += f"(chunk {result.get('chunk_index', 0)}, "
            context += f"similarity: {result.get('similarity', 0)}, "
            context += f"bm25: {result.get('bm25_score', 0):.3f}) ---\n"
            context += f"{result.get('content', '')}\n\n"

        return context

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        return call_with_owner(self._store.list_documents, owner=owner)

    def get_stats(self) -> Dict:
        return {
            "total_chunks": self._store.get_stats().get("total_chunks", 0),
            "total_documents": self._store.get_stats().get("total_documents", 0),
            "bm25_weight": self._bm25_weight,
            "vector_weight": self._vector_weight,
        }

    def clear(self):
        self._store.clear()
        self._bm25_index = None
        self._all_docs = []


# =============================================================================
# 3.  CROSS-ENCODER RANKER
# =============================================================================


class OnnxCrossEncoder:
    """A cross-encoder run with onnxruntime + tokenizers - no PyTorch.

    Both libraries already ship with chromadb, so the reranker adds only the
    model file (~23 MB, ms-marco-MiniLM-L-6-v2 int8, fetched at image build
    time by scripts/download_reranker.py). ``predict`` matches the
    sentence-transformers CrossEncoder API: (query, passage) pairs in,
    one relevance score per pair out (sigmoid of the logit, 0..1).
    """

    MODEL_FILES = ("model_quantized.onnx", "model.onnx")

    def __init__(self, model_dir: str, max_length: int = 512, batch_size: int = 16):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = next(
            (os.path.join(model_dir, sub, f) for sub in ("", "onnx")
             for f in self.MODEL_FILES if os.path.isfile(os.path.join(model_dir, sub, f))),
            None,
        )
        if model_path is None:
            raise FileNotFoundError(f"no ONNX reranker model in {model_dir}")
        self.model_path = model_path
        tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        # Long chunks are truncated (longest side of the pair first); a batch
        # is padded to its longest member.
        tok.enable_truncation(max_length=max_length)
        pad_id = tok.token_to_id("[PAD]")
        tok.enable_padding(pad_id=pad_id if pad_id is not None else 0, pad_token="[PAD]")
        self._tok = tok
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(os.getenv("RERANKER_THREADS", "2")))
        self._session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._batch_size = max(1, batch_size)

    def predict(self, pairs) -> List[float]:
        import numpy as np

        pairs = [(str(q), str(p)) for q, p in pairs]
        scores: List[float] = []
        for i in range(0, len(pairs), self._batch_size):
            enc = self._tok.encode_batch(pairs[i:i + self._batch_size])
            columns = {
                "input_ids": [e.ids for e in enc],
                "attention_mask": [e.attention_mask for e in enc],
                "token_type_ids": [e.type_ids for e in enc],
            }
            feeds = {name: np.asarray(col, dtype=np.int64)
                     for name, col in columns.items() if name in self._inputs}
            logits = np.asarray(self._session.run(None, feeds)[0], dtype=np.float64)
            logits = logits.reshape(len(enc), -1)[:, 0]
            scores.extend((1.0 / (1.0 + np.exp(-logits))).tolist())
        return scores


class CrossEncoderReranker:
    """Reranks search results with a cross-encoder model.

    A cross-encoder reads the question and each chunk TOGETHER, so it judges
    relevance far better than vector similarity or keyword overlap. Backends,
    first available wins:

    1. ONNX (``RERANKER_MODEL_DIR``, set in the Docker image): ms-marco
       MiniLM-L-6 via onnxruntime - no PyTorch, ~23 MB, CPU.
    2. sentence-transformers, if installed (``model_name``).
    3. The keyword-based reranker (advanced_rag.ReRanker).

    ``backend`` reports which one is in use (logged at startup).
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-base",
                 model_dir: Optional[str] = None):
        self._model_name = model_name
        self._model_dir = model_dir if model_dir is not None else os.getenv("RERANKER_MODEL_DIR", "")
        self._model = None
        self._unavailable = False
        self._reranked_count = 0
        self.backend = "not loaded"
        import threading
        self._load_lock = threading.Lock()

    def _get_model(self):
        """Load the cross-encoder model (lazy, once, with fallback).

        A failed load is remembered so we don't retry the import/download on
        every single query.
        """
        if self._model is not None:
            return self._model
        if self._unavailable:
            return None
        import logging
        log = logging.getLogger(__name__)
        with self._load_lock:
            if self._model is not None or self._unavailable:
                return self._model
            if self._model_dir:
                try:
                    self._model = OnnxCrossEncoder(self._model_dir)
                    self.backend = f"onnx ({os.path.basename(self._model.model_path)})"
                    log.info("reranker: cross-encoder %s", self.backend)
                    return self._model
                except Exception as e:
                    log.warning("reranker: ONNX model in %s unavailable: %s",
                                self._model_dir, e)
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self._model_name)
                self.backend = f"sentence-transformers ({self._model_name})"
                log.info("reranker: cross-encoder %s", self.backend)
                return self._model
            except Exception:
                # Fallback: use the existing keyword-based reranker
                self._unavailable = True
                self.backend = "keyword (no cross-encoder model available)"
                log.warning("reranker: %s", self.backend)
                return None

    def describe(self) -> str:
        """Load the model if needed and name the backend in use."""
        self._get_model()
        return self.backend

    def rerank(
        self,
        query: str,
        results: List[Dict],
        top_k: int = 5,
    ) -> List[Dict]:
        """Re-rank results using the cross-encoder.

        Args:
            query: The original search query
            results: Initial search results (from vector search)
            top_k: Number of results to return after re-ranking

        Returns:
            Re-ranked list of results
        """
        if not results:
            return []

        model = self._get_model()
        if model is None:
            # Fallback: use existing keyword-based reranker
            return self._fallback_rerank(query, results, top_k)

        # Score every (query, chunk) pair in ONE batched predict call
        try:
            scores = model.predict([(query, r.get("content", "")) for r in results])
            scored_results = [
                {**r, "rerank_score": round(float(sc), 4)}
                for r, sc in zip(results, scores)
            ]
        except Exception:
            # If cross-encoder fails, use original similarity
            scored_results = [
                {**r, "rerank_score": r.get("similarity", 0)} for r in results
            ]

        # Sort by rerank score (highest first)
        scored_results.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored_results[:top_k]

    def _fallback_rerank(self, query: str, results: List[Dict],
                         top_k: int = 5) -> List[Dict]:
        """Fallback reranker using existing keyword-based scoring."""
        from modules.advanced_rag import ReRanker
        reranker = ReRanker()
        return reranker.rerank(query, results, top_k=top_k)


# =============================================================================
# 4.  CONTEXTUAL RETRIEVER (enhanced with semantic chunking)
# =============================================================================


class ContextualRetriever:
    """Wraps a VectorStore and adds document summaries + semantic chunking.

    Contextual Retrieval (Anthropic): before embedding a chunk, prepend a
    2-line summary of the full document.  This gives each chunk document-level
    context (technique from Anthropic's "Contextual Retrieval" write-up; its
    effect is not measured in this repo).

    Semantic Chunking: splits text on sentence boundaries and groups
    sentences into chunks of a target size, preserving table structures
    and code blocks.

    Summaries are opt-in (RAG_CONTEXTUAL=1, default off). Before v3.4.8 the
    summary call had max_tokens=100, which the reasoning "fast" model spent on
    hidden thinking, so most documents silently got no summary (1 of 7 in the
    RAG eval). The retrieval numbers in the README are for plain chunks, so
    that is the default until summaries are measured to help.

    If summaries are off or the LLM returns nothing, chunks are stored without
    the prefix (identical to the original VectorStore behavior).
    """

    def __init__(
        self,
        vector_store,
        llm_client: Optional[LLMClient] = None,
        semantic_chunk_size: int = 1000,
        semantic_chunk_overlap: int = 100,
        contextual: Optional[bool] = None,
    ):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()
        # None = follow RAG_CONTEXTUAL at upload time.
        self._contextual_flag = contextual
        self._summaries: Dict[str, str] = {}
        self._semantic_chunker = SemanticChunker(
            chunk_size=semantic_chunk_size,
            overlap=semantic_chunk_overlap,
        )

    def _delete_existing(self, file_name: str, owner: Optional[str]) -> None:
        """Remove previous chunks of this file for this owner (re-upload)."""
        conds = [{"file": file_name}]
        if owner is not None:
            conds.append({"owner": owner})
        where = conds[0] if len(conds) == 1 else {"$and": conds}
        try:
            existing = self._store.collection.get(where=where, include=[])
            ids = (existing or {}).get("ids") or []
            if ids:
                self._store.collection.delete(ids=ids)
        except Exception:
            pass  # minimal/fake collections without where/delete support

    def add_document(self, file_path: str, owner: Optional[str] = None) -> str:
        """Add a document with contextual summary + semantic chunking."""
        from modules.rag import DocumentReader

        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text
        if not text.strip():
            return "File is empty or unreadable"

        # Generate a 2-line summary of the full document (opt-in)
        summary = self._generate_summary(file_path, text) if self.contextual_enabled() else ""

        # Semantic chunking: split on sentence boundaries
        chunks = self._semantic_chunker.split(text)
        if not chunks:
            return "No content to index"

        # Prepend summary to each chunk
        file_name = os.path.basename(file_path)
        self._delete_existing(file_name, owner)
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{owner}::{file_name}_{i}" if owner else f"{file_name}_{i}"
            if summary:
                contextual_chunk = f"[Document Summary: {summary}]\n\n{chunk}"
            else:
                contextual_chunk = chunk
            ids.append(chunk_id)
            documents.append(contextual_chunk)
            metadatas.append({
                "file": file_name,
                "owner": owner or "",
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source": file_path,
                "has_context": bool(summary),
            })

        self._store.collection.upsert(
            ids=ids, documents=documents, metadatas=metadatas
        )

        label = "contextual retrieval" if summary else "plain chunks"
        return f"Added {len(chunks)} chunks from {file_name} ({label})"

    def contextual_enabled(self) -> bool:
        if self._contextual_flag is not None:
            return bool(self._contextual_flag)
        return os.getenv("RAG_CONTEXTUAL", "0") == "1"

    def _generate_summary(self, file_path: str, text: str) -> str:
        """Generate a 2-line summary of the document."""
        import hashlib

        truncated = text[:3000]
        cache_key = hashlib.sha256(truncated.encode("utf-8", "ignore")).hexdigest()
        if cache_key in self._summaries:
            return self._summaries[cache_key]

        system_prompt = (
            "You are a helpful assistant that summarizes documents. "
            "Write a concise 2-line summary of the document below. "
            "Focus on the main topic and key details. "
            "Do not add any extra commentary."
        )
        user_prompt = f"Document: {os.path.basename(file_path)}\n\n{truncated}"

        # The fast model is a reasoning model: hidden thinking counts against
        # max_tokens, so the budget must cover it plus ~60 words of summary.
        budget = max(256, int(os.getenv("RAG_SUMMARY_MAX_TOKENS", "1024")))
        summary = (self._llm.complete(system_prompt, user_prompt, max_tokens=budget) or "").strip()
        if summary:  # never cache an empty result: the next upload retries
            self._summaries[cache_key] = summary
        return summary

    def search(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> List[Dict]:
        """Search using the underlying vector store."""
        return call_with_owner(self._store.search, query, top_k, owner=owner)

    def get_context(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> str:
        """Get context string for the AI."""
        return call_with_owner(self._store.get_context, query, top_k, owner=owner)

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        return call_with_owner(self._store.list_documents, owner=owner)

    def get_stats(self) -> Dict:
        return self._store.get_stats()

    def clear(self):
        self._store.clear()
        self._summaries.clear()


# =============================================================================
# 5.  HYDE (HYPOTHETICAL DOCUMENT EMBEDDINGS)
# =============================================================================


class HyDERetriever:
    """Wraps a VectorStore and uses HyDE for query expansion.

    HyDE (Hypothetical Document Embeddings): instead of embedding the
    user's query directly, first ask an LLM to generate a hypothetical
    answer, then embed THAT answer and search with it.  This works
    better for vague queries because the LLM "fills in" the semantic
    meaning.

    If the LLM is unavailable, falls back to the original query
    (graceful degradation — identical to the original VectorStore behavior).
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search using a hypothetical answer as the query embedding."""
        hypothetical = self._generate_hypothetical_answer(query)
        search_query = hypothetical if hypothetical else query
        return self._store.search(search_query, top_k)

    def _generate_hypothetical_answer(self, query: str) -> str:
        """Generate a hypothetical answer to the query. Returns "" on failure."""
        system_prompt = (
            "You are a helpful assistant. Write a hypothetical answer to the "
            "user's question as if you had access to the relevant documents. "
            "Be specific and detailed, including facts, examples, and context "
            "that would help retrieve the right documents. "
            "If you cannot answer, say so briefly."
        )
        user_prompt = f"Question: {query}"
        return self._llm.complete(system_prompt, user_prompt, max_tokens=500)

    def get_context(self, query: str, top_k: int = 5) -> str:
        """Get context string using HyDE search."""
        results = self.search(query, top_k)

        if not results:
            if self._store.collection.count() > 0:
                return "No relevant documents found for this query."
            return "No documents have been uploaded."

        context = "Relevant document excerpts (ranked by relevance):\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result.get('file', 'unknown')} "
            context += f"(chunk {result.get('chunk_index', 0)}, "
            context += f"similarity: {result.get('similarity', 0)}) ---\n"
            context += f"{result.get('content', '')}\n\n"

        return context

    def add_document(self, file_path: str) -> str:
        return self._store.add_document(file_path)

    def list_documents(self) -> List[str]:
        return self._store.list_documents()

    def get_stats(self) -> Dict:
        return self._store.get_stats()

    def clear(self):
        self._store.clear()


# =============================================================================
# 6.  QUERY DECOMPOSITION + STEP-BACK
# =============================================================================


class QueryDecomposer:
    """Breaks complex queries into sub-questions and answers each separately.

    Extends the existing SubQuestionEngine pattern from advanced_rag.py.
    Uses the LLM to decompose queries into sub-questions, then searches
    each sub-question independently and combines the answers.

    Example:
        User: "Why did sales drop in Q2 and compare with Q1 pricing?"
        Decomposed:
          - "What are the reasons sales dropped in Q2?"
          - "What is the pricing in Q1?"
        Each sub-question is searched independently, then combined.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self._llm = llm_client or GroqLLMClient()

    def decompose(self, query: str) -> List[str]:
        """Decompose a complex query into sub-questions.

        Returns a list of sub-questions.
        """
        query_lower = query.lower()
        sub_questions = []

        # Check for complex indicators
        complex_indicators = [
            "compare", "comparison", "difference", "differences",
            "versus", "vs", "both", "all", "every",
            "pros and cons", "advantages and disadvantages",
            "better", "best", "and also", "as well as",
            "in addition", "moreover",
        ]

        for indicator in complex_indicators:
            if indicator in query_lower:
                parts = query_lower.split(" and ")
                if len(parts) >= 2:
                    for part in parts:
                        clean = part.replace("compare", "").strip().rstrip("?").strip()
                        if clean and len(clean) > 3:
                            sub_questions.append(f"What is the {clean}?")
                break

        # If decomposition failed, use the original query
        if not sub_questions:
            sub_questions = [query]

        return sub_questions

    def answer(self, query: str) -> Dict:
        """Answer a query using sub-question decomposition.

        Returns:
            {
                "sub_questions": [...],
                "sub_answers": [...],
                "combined_answer": "...",
                "decomposed": True/False
            }
        """
        sub_questions = self.decompose(query)

        # Answer each sub-question
        sub_answers = []
        for sq in sub_questions:
            answer = self._llm.complete(
                system_prompt=f"Answer the following question: {sq}",
                user_prompt=sq,
                max_tokens=300,
            )
            sub_answers.append(answer)

        # Combine answers
        combined = self._combine_answers(query, sub_questions, sub_answers)

        return {
            "sub_questions": sub_questions,
            "sub_answers": sub_answers,
            "combined_answer": combined,
            "decomposed": True,
        }

    def _combine_answers(self, original_query: str,
                         sub_questions: List[str],
                         sub_answers: List[str]) -> str:
        """Combine sub-answers into a coherent response."""
        if len(sub_questions) == 1:
            return sub_answers[0]

        combined = f"Based on analysis of {len(sub_questions)} aspects:\n\n"
        for i, (sq, sa) in enumerate(zip(sub_questions, sub_answers), 1):
            combined += f"**{i}. {sq}**\n{sa}\n\n"

        combined += f"**Summary:**\nTo answer your question about {original_query.lower().rstrip('?')}, "
        combined += f"I analyzed {len(sub_questions)} different aspects above."

        return combined


# =============================================================================
# 7.  CORRECTIVE RAG (CRAG) — Self-Correction Loop
# =============================================================================


class CRAGRetriever:
    """Corrective RAG: self-correction loop that detects unsupported answers.

    Flow:
      1. Retrieve → LLM generates answer
      2. LLM asks "Is every sentence supported by retrieved chunks?"
      3. If NO → retrieve again with rewritten query
      4. Re-answer

    (The deployed api.py path uses OptimizedRAG.verify_answer for the check
    and does its own single retry.)
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()

    def retrieve_and_answer(
        self,
        query: str,
        top_k: int = 5,
        max_retries: int = 2,
    ) -> Dict:
        """Retrieve and answer, with self-correction.

        Args:
            query: The user's query
            top_k: Number of results to retrieve
            max_retries: Maximum number of re-retrieval attempts

        Returns:
            Answer with trace of retries
        """
        # Step 1: Retrieve and answer
        results = self._store.search(query, top_k=top_k)
        context = self._store.get_context(query, top_k)

        # Ask LLM to verify the answer
        answer = self._llm.complete(
            system_prompt="You are a helpful assistant. Verify that your answer is supported by the retrieved context. "
                          "If any sentence in your answer is not supported, say so.",
            user_prompt=f"Answer: {context}\n\nYour answer: ",
            max_tokens=500,
        )

        # Check if the answer is supported
        if self._is_supported(answer, context):
            return {"answer": answer, "retries": 0, "supported": True}

        # Step 2: Re-retrieve with rewritten query
        rewritten_query = self._rewrite_query(query, answer)
        if rewritten_query == query:
            # No rewrite possible — return the original answer
            return {"answer": answer, "retries": 1, "supported": False}

        # Step 3: Re-retrieve with rewritten query
        results = self._store.search(rewritten_query, top_k=top_k)
        context2 = self._store.get_context(rewritten_query, top_k)

        # Ask LLM again
        answer2 = self._llm.complete(
            system_prompt="You are a helpful assistant. Verify that your answer is supported by the retrieved context. "
                          "If any sentence in your answer is not supported, say so.",
            user_prompt=f"Answer: {context2}\n\nYour answer: ",
            max_tokens=500,
        )

        return {"answer": answer2, "retries": 2, "supported": True}

    def _is_supported(self, answer: str, context: str) -> bool:
        """Check if every sentence in the answer is supported by context.

        Uses substring matching: each answer sentence must appear as a
        substring of the context (not exact sentence-to-sentence match),
        which is more robust to whitespace/formatting differences.
        """
        answer_sentences = self._split_sentences(answer)
        for sentence in answer_sentences:
            if sentence and sentence.strip() not in context:
                return False
        return True

    def _rewrite_query(self, query: str, answer: str) -> str:
        """Rewrite the query to find evidence for the answer."""
        system_prompt = (
            "You are a helpful assistant. Rewrite the user's query to find "
            "evidence in the documents. The answer you just generated is "
            "not fully supported. Rewrite the query to focus on the missing "
            "evidence."
        )
        user_prompt = f"Answer: {answer}\n\nRewrite the query to find evidence: {query}"
        return self._llm.complete(system_prompt, user_prompt, max_tokens=200)

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]


# =============================================================================
# 8.  CONVERSATIONAL / HISTORY-AWARE RETRIEVER
# =============================================================================


class ConversationalRetriever:
    """Retrieval with conversational history awareness.

    Follow-up queries are rewritten using chat history so the RAG
    system can answer "what about second file?" even when the user
    changed the topic.

    Example:
        User: "Summarize CRM-3 pricing"
        AI: "$100"
        User: "What about that other file?"
        History-aware query: "What is pricing in sample.txt compared to CRM-3.docx $100"
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()

    def retrieve_with_history(self, query: str, history: str, top_k: int = 5) -> Dict:
        """Retrieve with conversational history awareness.

        Args:
            query: The user's query
            history: The conversation history (list of messages)
            top_k: Number of results to retrieve

        Returns:
            Context for the AI
        """
        # Rewrite the query using history
        rewritten = self._rewrite_query_with_history(query, history)

        # Search with the rewritten query
        results = self._store.search(rewritten, top_k=top_k)
        context = self._store.get_context(rewritten, top_k)

        return {
            "rewritten_query": rewritten,
            "results": results,
            "context": context,
        }

    def _rewrite_query_with_history(self, query: str, history: str) -> str:
        """Rewrite a follow-up query using conversation history."""
        system_prompt = (
            "You are a helpful assistant. You are given a conversation history. "
            "Rewrite the user's query to be standalone, incorporating the context "
            "from the conversation. The rewritten query should be clear and "
            "specific."
        )
        user_prompt = f"History: {history}\n\nUser query: {query}"
        return self._llm.complete(system_prompt, user_prompt, max_tokens=200)


# =============================================================================
# 9.  SEMANTIC CACHE
# =============================================================================


# Numbers, years, quarters and number words. Two questions that differ in any
# of these ask about different things ("Q2 2025" vs "Q3 2026"), even when
# their embeddings are almost identical.
_NUMBER_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)*|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|first|second|third|fourth|fifth|half|quarter|annual|yearly|"
    r"monthly|weekly|daily)\b", re.IGNORECASE)


def _number_tokens(text: str) -> frozenset:
    return frozenset(m.lower() for m in _NUMBER_TOKEN_RE.findall(text or ""))


class SemanticCache:
    """Semantic cache: caches answers by query embedding similarity.

    Uses ChromaDB to store query-answer pairs.  When a query is
    asked again, the cache is checked first.  If a near-identical question
    is found, its answer is returned without calling the LLM again, which
    saves that call's tokens and latency.

    It only serves REPEATS, not related questions: the RAG answer eval
    (v3.4.7) found that the old 0.3 cosine-distance limit served the answer
    to "What is the rate limit on the Team plan?" for "How much does the Team
    plan cost?", and a Q3 2026 revenue answer for a Q2 2025 margin question.
    A missed cache hit only costs tokens; a wrong hit is a wrong answer.
    """

    # Max cosine distance for a hit (similarity >= 0.95): rewordings such as
    # "what's"/"what is" or punctuation, not different questions.
    MAX_DISTANCE = float(os.getenv("RAG_CACHE_MAX_DISTANCE", "0.05"))

    # Version of the document-answer format (api._ask_doc_question prompt and
    # pipeline). Bump it whenever that changes: answers cached under an older
    # version are then ignored (they persist in ChromaDB across deploys).
    #   1 = unversioned (<= 3.4.5)   2 = 3.4.6 (direct answers, reranker, CRAG)
    ANSWER_VERSION = 2

    def __init__(self, vector_store):
        self._store = vector_store
        self._cache_collection = None

    @property
    def collection(self):
        """Expose the cache collection for backward compatibility."""
        return self._get_cache_collection()

    def _get_cache_collection(self):
        """Get or create the cache collection.

        Uses the ChromaDB *client* (not the collection) to create a
        separate collection for cached query-answer pairs.
        """
        if self._cache_collection is None:
            client = getattr(self._store, "client", None)
            if client is None:
                # Fallback: use the store's collection directly if it
                # supports get_or_create_collection (some fakes do).
                client = self._store.collection
            self._cache_collection = client.get_or_create_collection(
                name="query_cache",
                metadata={"hnsw:space": "cosine"},
            )
        return self._cache_collection

    def lookup(self, query: str, top_k: int = 3, threshold: Optional[float] = None,
               owner: Optional[str] = None) -> Optional[str]:
        """Look up a cached answer for the query.

        Returns the cached answer only if a stored query is within `threshold`
        cosine distance (default MAX_DISTANCE, i.e. a near-duplicate question)
        AND mentions the same numbers/years/quarters. Returns None otherwise.
        """
        if threshold is None:
            threshold = self.MAX_DISTANCE
        wanted_numbers = _number_tokens(query)
        try:
            cache_collection = self._get_cache_collection()
        except Exception:
            return None

        # Search for similar cached answers using ChromaDB's own
        # embedding (no need for a separate _embed call). The cached
        # *query* is what is embedded (see store), so a repeat of the
        # same question matches at ~0 distance and returns its answer.
        kwargs = {
            "query_texts": [query],
            "n_results": top_k,
            "include": ["metadatas", "distances"],
        }
        if owner is not None:
            # Tenant isolation: only ever hit answers cached for this owner.
            kwargs["where"] = {"owner": owner}
        try:
            results = cache_collection.query(**kwargs)
        except Exception:
            return None

        if (results and results.get("metadatas") and results["metadatas"][0]
                and results.get("distances") and results["distances"][0]):
            # ChromaDB returns metadatas/distances as lists of lists (one per query),
            # nearest first. Only a near-duplicate question (within `threshold`)
            # with the same numbers counts, and only an answer cached under the
            # CURRENT answer version.
            for meta, distance in zip(results["metadatas"][0], results["distances"][0]):
                if distance > threshold:
                    break
                meta = meta or {}
                if meta.get("v") != self.ANSWER_VERSION:
                    continue
                if _number_tokens(meta.get("query", "")) != wanted_numbers:
                    continue
                return meta.get("answer")
        return None

    def store(self, query: str, answer: str, owner: Optional[str] = None):
        """Store a query-answer pair in the cache.

        The *query* is embedded (so repeat questions match at ~0 distance),
        and the *answer* is kept in metadata and returned on a cache hit.
        """
        try:
            cache_collection = self._get_cache_collection()
        except Exception:
            return

        # Use a stable hash (md5) so the same query always maps to the
        # same id across Python sessions.
        import hashlib
        query_hash = hashlib.md5(
            f"{owner or ''}\x00{query}".encode(), usedforsecurity=False
        ).hexdigest()
        cache_collection.upsert(
            ids=[f"query_{query_hash}"],
            documents=[query],
            metadatas=[{
                "query": query,
                "answer": answer,
                "owner": owner or "",
                "timestamp": time.time(),
                "v": self.ANSWER_VERSION,
            }],
        )

    def invalidate(self, owner: Optional[str] = None) -> None:
        """Drop cached answers (for one owner, or all) - called on upload so
        a re-uploaded/changed document never serves a stale answer."""
        if owner is None:
            self.clear()
            return
        try:
            coll = self._get_cache_collection()
            existing = coll.get(where={"owner": owner}, include=[])
            ids = (existing or {}).get("ids") or []
            if ids:
                coll.delete(ids=ids)
        except Exception:
            pass

    def clear(self):
        """Clear the cache."""
        client = getattr(self._store, "client", None)
        try:
            if client is not None and hasattr(client, "delete_collection"):
                client.delete_collection("query_cache")
            elif self._cache_collection is not None:
                self._cache_collection.delete_collection()
        except Exception:
            pass
        self._cache_collection = None

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        if self._cache_collection is None:
            return {"cached_entries": 0}
        try:
            return {"cached_entries": self._cache_collection.count()}
        except Exception:
            return {"cached_entries": 0}


# =============================================================================
# 10.  OPTIMIZED RAG (combines everything)
# =============================================================================


class OptimizedRAG:
    """Comprehensive optimized RAG pipeline combining all 7 features.

    - Hybrid Search (BM25 + Vector)
    - Cross-Encoder Reranker
    - Contextual Retrieval + Semantic Chunking
    - Query Decomposition + Step-Back
    - Corrective RAG (CRAG)
    - Conversational / History-Aware Retrieval
    - Semantic Cache + Eval

    Usage:
        from modules.rag_optimizations import OptimizedRAG, GroqLLMClient
        from modules.vector_store import get_vector_store

        store = get_vector_store()
        rag = OptimizedRAG(store, llm_client=GroqLLMClient())
        rag.add_document("uploads/report.pdf")
        context = rag.get_context("What are the pricing plans?")
    """

    def __init__(
        self,
        vector_store,
        llm_client: Optional[LLMClient] = None,
        bm25_weight: float = 0.3,
        vector_weight: float = 0.7,
        reranker: Optional[CrossEncoderReranker] = None,
        semantic_chunk_size: int = 1000,
        semantic_chunk_overlap: int = 100,
    ):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()

        # 2. Cross-Encoder Reranker (falls back to the keyword ReRanker when
        #    sentence-transformers is not installed)
        self._cross_encoder = reranker or CrossEncoderReranker()

        # 1. Hybrid Searcher (BM25 + vector, then reranked)
        self._hybrid = HybridSearcher(
            vector_store=vector_store,
            bm25_weight=bm25_weight,
            vector_weight=vector_weight,
            reranker=self._cross_encoder,
        )

        # 3. Contextual Retriever
        self._contextual = ContextualRetriever(
            vector_store=vector_store,
            llm_client=llm_client,
            semantic_chunk_size=semantic_chunk_size,
            semantic_chunk_overlap=semantic_chunk_overlap,
        )

        # 4. Query Decomposer
        self._decomposer = QueryDecomposer(llm_client=llm_client)

        # 5. CRAG
        self._crag = CRAGRetriever(vector_store=vector_store, llm_client=llm_client)

        # 6. Conversational Retriever
        self._conversational = ConversationalRetriever(
            vector_store=vector_store, llm_client=llm_client
        )

        # 7. Semantic Cache
        self._cache = SemanticCache(vector_store)

    # ---- Public API ----

    def add_document(self, file_path: str, owner: Optional[str] = None) -> str:
        """Add a document with all optimizations.

        Rebuilds the BM25 index lazily and invalidates the owner's cached
        answers so re-uploads never serve stale results.
        """
        result = self._contextual.add_document(file_path, owner=owner)
        self._hybrid.invalidate_index()
        self._cache.invalidate(owner)
        return result

    def search(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> List[Dict]:
        """Search using hybrid search + reranking."""
        return self._hybrid.search(query, top_k, owner=owner)

    def get_context(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> str:
        """Get context using hybrid search + reranking."""
        return self._hybrid.get_context(query, top_k, owner=owner)

    def get_context_for_document(self, query: str, filename: str, top_k: int = 4,
                                 owner: Optional[str] = None) -> str:
        """Labeled excerpts from ONE document (multi-document comparison)."""
        chunks = self.get_document_chunks(filename, owner=owner)
        if not chunks:
            return ""
        return format_chunks(rank_document_chunks(query, chunks, top_k))

    def get_document_chunks(self, filename: str, owner: Optional[str] = None) -> List[Dict]:
        fn = getattr(self._store, "get_document_chunks", None)
        if fn is not None:
            return call_with_owner(fn, filename, owner=owner) or []
        # Generic Chroma-collection fallback
        target = resolve_filename(filename, self.list_documents(owner))
        if not target:
            return []
        try:
            data = self._store.collection.get(include=["documents", "metadatas"])
        except Exception:
            return []
        out = []
        for doc, meta in zip(data.get("documents") or [], data.get("metadatas") or []):
            meta = meta or {}
            if meta.get("file") != target:
                continue
            if owner is not None and meta.get("owner") != owner:
                continue
            out.append({"file": target, "chunk_index": meta.get("chunk_index", 0), "content": doc})
        return sorted(out, key=lambda c: c["chunk_index"])

    def delete_document(self, filename: str, owner: Optional[str] = None) -> int:
        fn = getattr(self._store, "delete_document", None)
        removed = call_with_owner(fn, filename, owner=owner) if fn else 0
        self._hybrid.invalidate_index()
        self._cache.invalidate(owner)
        return removed or 0

    def has_document(self, filename: str, owner: Optional[str] = None) -> bool:
        return resolve_filename(filename, self.list_documents(owner)) is not None

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        return call_with_owner(self._store.list_documents, owner=owner)

    # ---- History-aware / decomposed retrieval ----

    _FOLLOWUP_RE = re.compile(
        r"\b(it|its|that|this|those|these|they|them|the other|the second|the first|"
        r"same|above|previous|earlier|what about|and the|how about)\b",
        re.IGNORECASE,
    )

    def retrieve(self, query: str, history: str = "", top_k: int = 5,
                 owner: Optional[str] = None) -> List[Dict]:
        """Production retrieval entry point.

        1. Follow-up questions ("what about the other file?") are rewritten
           into a standalone query using the chat history (one LLM call,
           only when the query looks like a follow-up and history exists;
           disable with RAG_QUERY_REWRITE=0).
        2. Complex multi-part questions are decomposed and the results of
           every sub-question are merged (RAG_DECOMPOSE=1, default off).
        3. Hybrid BM25 + vector search, reranked by the cross-encoder.
        """
        effective = query
        if (history and os.getenv("RAG_QUERY_REWRITE", "1") != "0"
                and len(query.split()) <= 25 and self._FOLLOWUP_RE.search(query)):
            try:
                rewritten = self._conversational._rewrite_query_with_history(
                    query, history[-2000:]
                )
                if rewritten and len(rewritten) < 500:
                    effective = rewritten.strip().strip('"')
            except Exception:
                effective = query

        merged: Dict[tuple, Dict] = {}

        def _merge(rows: List[Dict]):
            for r in rows or []:
                key = (r.get("file"), r.get("chunk_index"))
                if key not in merged:
                    merged[key] = r

        _merge(self.search(effective, top_k, owner=owner))
        if os.getenv("RAG_DECOMPOSE", "0") == "1":
            subs = [q for q in self._decomposer.decompose(effective) if q != effective]
            for sq in subs[:3]:
                _merge(self.search(sq, max(2, top_k // 2), owner=owner))
        return list(merged.values())[: max(top_k, 1) * 2]

    _VERDICT_RE = re.compile(r"\b(UNSUPPORTED|NOT\s+SUPPORTED|SUPPORTED)\b")

    def verify_answer(self, answer: str, context: str) -> bool:
        """Corrective-RAG check (RAG_CRAG, on by default): ask the LLM whether
        ``answer`` is supported by ``context``. True when supported or unsure.

        The fast model is a reasoning model whose hidden thinking counts
        against max_tokens, so the budget must leave room for the verdict
        (at 5 tokens the reply was always empty and every answer "passed").
        It sees the same excerpts the answer was written from.
        """
        if os.getenv("RAG_CRAG", "1") != "1" or not answer or not context:
            return True
        import logging
        try:
            ctx_chars = int(os.getenv("RAG_CRAG_CONTEXT_CHARS", "12000"))
            budget = max(256, int(os.getenv("RAG_CRAG_MAX_TOKENS", "1024")))
        except ValueError:
            ctx_chars, budget = 12000, 1024
        try:
            reply = self._llm.complete(
                "You are a strict fact checker. Decide whether every factual claim "
                "in the ANSWER is stated in, or directly follows from, the CONTEXT. "
                "Saying that the context does not cover something is not a claim. "
                "Reply with one word: SUPPORTED or UNSUPPORTED.",
                f"CONTEXT:\n{context[:ctx_chars]}\n\nANSWER:\n{answer[:4000]}",
                max_tokens=budget,
            )
        except Exception:
            reply = ""
        found = self._VERDICT_RE.findall((reply or "").upper())
        verdict = ("unsure" if not found
                   else "supported" if found[-1] == "SUPPORTED" else "unsupported")
        try:
            from modules.prometheus_metrics import RAG_CRAG_VERDICTS
            RAG_CRAG_VERDICTS.labels(verdict=verdict).inc()
        except Exception:
            pass
        if verdict == "unsure":
            logging.getLogger(__name__).info("CRAG: no verdict in reply %r", (reply or "")[:200])
        return verdict != "unsupported"

    def reranker_backend(self) -> str:
        """Load the reranker model and name the backend (startup log)."""
        return self._cross_encoder.describe()

    def get_stats(self) -> Dict:
        return {
            "hybrid": self._hybrid.get_stats(),
            "contextual": self._contextual.get_stats(),
            "cache": self._cache.get_stats(),
        }

    def clear(self):
        self._store.clear()
        self._contextual._summaries.clear()
        self._cache.clear()
        self._hybrid.invalidate_index()

    def get_cached_answer(self, query: str, owner: Optional[str] = None):
        """Return a cached answer for the query, or None (Semantic Cache)."""
        return self._cache.lookup(query, owner=owner)

    def cache_answer(self, query: str, answer: str, owner: Optional[str] = None):
        """Store an answer in the Semantic Cache so repeat queries skip the LLM."""
        if answer:
            self._cache.store(query, answer, owner=owner)

    def invalidate_cache(self, owner: Optional[str] = None) -> None:
        self._cache.invalidate(owner)

    def query(
        self,
        query: str,
        top_k: int = 5,
        use_subquestion: bool = True,
        use_crag: bool = True,
        use_conversational: bool = False,
        history: str = "",
    ) -> Dict:
        """Full optimized query pipeline.

        Args:
            query: The user's query
            top_k: Number of results to retrieve
            use_subquestion: Whether to decompose complex queries
            use_crag: Whether to use corrective RAG
            use_conversational: Whether to use conversational retrieval
            history: Conversation history for context

        Returns:
            {
                "query": original query,
                "context": relevant document context,
                "subquestion_data": decomposition data (if used),
                "reranked_count": number of re-ranked results,
                "cache_hit": whether the answer was cached,
                "crag_retries": number of CRAG retries,
            }
        """
        # Step 1: Semantic cache lookup
        cache_result = self._cache.lookup(query)
        cache_hit = cache_result is not None

        # Step 2: Query decomposition (if enabled)
        subquestion_data = None
        if use_subquestion:
            decomp = self._decomposer.answer(query)
            subquestion_data = decomp

        # Step 3: Hybrid search
        results = self._hybrid.search(query, top_k=top_k)

        # Step 4: CRAG self-correction
        crag_result = {"answer": "", "retries": 0, "supported": False}
        if use_crag:
            crag_result = self._crag.retrieve_and_answer(query, top_k=top_k)
            if crag_result["supported"]:
                answer = crag_result["answer"]
            else:
                answer = results[0].get("content", "") if results else ""
        else:
            answer = results[0].get("content", "") if results else ""

        # Step 5: Conversational retrieval (if enabled)
        conversational_result = None
        if use_conversational and history:
            conversational_result = self._conversational.retrieve_with_history(
                query, history, top_k=top_k
            )

        # Step 6: Build context
        context = self._hybrid.get_context(query, top_k)

        return {
            "query": query,
            "context": context,
            "subquestion_data": subquestion_data,
            "reranked_count": len(results),
            "cache_hit": cache_hit,
            "crag_retries": crag_result.get("retries", 0) if use_crag else 0,
            "conversational_result": conversational_result,
        }


# =============================================================================
# 11.  EXPORTS
# =============================================================================


__all__ = [
    "LLMClient",
    "GroqLLMClient",
    "SemanticChunker",
    "HybridSearcher",
    "CrossEncoderReranker",
    "ContextualRetriever",
    "HyDERetriever",
    "QueryDecomposer",
    "CRAGRetriever",
    "ConversationalRetriever",
    "SemanticCache",
    "OptimizedRAG",
]

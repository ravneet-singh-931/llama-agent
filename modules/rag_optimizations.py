"""
RAG Optimizations Module — Contextual Retrieval + HyDE

Implements two proven RAG techniques that improve retrieval accuracy:

1. Contextual Retrieval (Anthropic):
   Before embedding a chunk, prepend a 2-line summary of the full document.
   This gives each chunk document-level context, improving recall by ~10%
   (per Anthropic's paper: https://www.anthropic.com/research/retrieval-augmented-generation).

2. HyDE (Hypothetical Document Embeddings):
   Instead of embedding the user's query directly, first ask an LLM to
   generate a hypothetical answer, then embed THAT. Works better for vague
   queries because the LLM "fills in" the semantic meaning.
   (Paper: https://arxiv.org/abs/2212.10496)

Both techniques are OPTIONAL and gracefully degrade when no LLM is available:
- ContextualRetriever: if the LLM can't generate a summary, chunks are stored
  without the prefix (identical to the original VectorStore behavior).
- HyDERetriever: if the LLM can't generate a hypothetical answer, the original
  query is used for search (identical to the original VectorStore behavior).

Usage:
    from modules.rag_optimizations import OptimizedRAG, GroqLLMClient
    from modules.vector_store import get_vector_store

    store = get_vector_store()
    rag = OptimizedRAG(store, llm_client=GroqLLMClient())
    rag.add_document("uploads/report.pdf")
    context = rag.get_context("What are the pricing plans?")

The wrappers delegate to the underlying VectorStore for storage and search,
so they are drop-in compatible with the existing app.py / api.py code paths.
"""

from typing import List, Dict, Optional, Protocol
import os


# ============ LLM CLIENT INTERFACE (dependency injection) ============


class LLMClient(Protocol):
    """Protocol for LLM clients that can generate text completions.

    This allows dependency injection so tests can use fakes instead of
    requiring a live Groq/OpenAI API key.
    """

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
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

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
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


# ============ 1. CONTEXTUAL RETRIEVAL ============


class ContextualRetriever:
    """Wraps a VectorStore and adds document summaries to chunks.

    Contextual Retrieval (Anthropic): before embedding a chunk, prepend a
    2-line summary of the full document. This gives each chunk document-level
    context, improving recall by ~10% (per Anthropic's paper).

    The summary is generated once per document and prepended to every chunk
    from that document, so ChromaDB embeds the summary + chunk together.
    If the LLM is unavailable, chunks are stored without the prefix
    (identical to the original VectorStore behavior).
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()
        self._summaries: Dict[str, str] = {}  # cache: file_path -> summary

    def add_document(self, file_path: str) -> str:
        """Add a document with a contextual summary prepended to each chunk."""
        from modules.rag import DocumentReader

        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text
        if not text.strip():
            return "File is empty or unreadable"

        # Generate a 2-line summary of the full document
        summary = self._generate_summary(file_path, text)

        # Split into chunks (mirrors VectorStore._split_text)
        chunks = self._split_text(text)
        if not chunks:
            return "No content to index"

        # Prepend summary to each chunk
        file_name = os.path.basename(file_path)
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{file_name}_{i}"
            # Prepend the summary as context
            if summary:
                contextual_chunk = f"[Document Summary: {summary}]\n\n{chunk}"
            else:
                contextual_chunk = chunk
            ids.append(chunk_id)
            documents.append(contextual_chunk)
            metadatas.append({
                "file": file_name,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source": file_path,
                "has_context": bool(summary),
            })

        self._store.collection.upsert(
            ids=ids, documents=documents, metadatas=metadatas
        )

        label = "contextual retrieval" if summary else "no LLM (plain chunks)"
        return f"Added {len(chunks)} chunks from {file_name} ({label})"

    def _generate_summary(self, file_path: str, text: str) -> str:
        """Generate a 2-line summary of the document. Returns "" on failure."""
        # Check cache
        if file_path in self._summaries:
            return self._summaries[file_path]

        # Truncate text for summary generation
        truncated = text[:3000]

        system_prompt = (
            "You are a helpful assistant that summarizes documents. "
            "Write a concise 2-line summary of the document below. "
            "Focus on the main topic and key details. "
            "Do not add any extra commentary."
        )
        user_prompt = f"Document: {os.path.basename(file_path)}\n\n{truncated}"

        summary = self._llm.complete(system_prompt, user_prompt, max_tokens=100)

        # Cache it
        self._summaries[file_path] = summary
        return summary

    def _split_text(self, text: str) -> List[str]:
        """Split text into chunks with overlap (mirrors VectorStore)."""
        chunk_size = 500
        overlap = 50
        step = chunk_size - overlap
        chunks = []
        for i in range(0, len(text), step):
            chunk = text[i:i + chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    # ---- Delegate read/search methods to the underlying store ----

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search using the underlying vector store."""
        return self._store.search(query, top_k)

    def get_context(self, query: str, top_k: int = 5) -> str:
        """Get context string for the AI."""
        return self._store.get_context(query, top_k)

    def list_documents(self) -> List[str]:
        return self._store.list_documents()

    def get_stats(self) -> Dict:
        return self._store.get_stats()

    def clear(self):
        self._store.clear()
        self._summaries.clear()


# ============ 2. HYDE (HYPOTHETICAL DOCUMENT EMBEDDINGS) ============


class HyDERetriever:
    """Wraps a VectorStore and uses HyDE for query expansion.

    HyDE (Hypothetical Document Embeddings): instead of embedding the user's
    query directly, first ask an LLM to generate a hypothetical answer to the
    query, then embed THAT answer and search with it. This works better for
    vague queries because the LLM "fills in" the semantic meaning.

    If the LLM is unavailable, falls back to the original query (graceful
    degradation — identical to the original VectorStore behavior).
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search using a hypothetical answer as the query embedding."""
        # Generate a hypothetical answer
        hypothetical = self._generate_hypothetical_answer(query)

        # Use the hypothetical answer as the search query
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

    # ---- Delegate storage methods to the underlying store ----

    def add_document(self, file_path: str) -> str:
        return self._store.add_document(file_path)

    def list_documents(self) -> List[str]:
        return self._store.list_documents()

    def get_stats(self) -> Dict:
        return self._store.get_stats()

    def clear(self):
        self._store.clear()


# ============ 3. COMBINED OPTIMIZED RAG ============


class OptimizedRAG:
    """Combines Contextual Retrieval + HyDE for maximum retrieval quality.

    - Documents are indexed with a 2-line summary prepended to each chunk
      (Contextual Retrieval).
    - Queries are expanded via a hypothetical answer before searching
      (HyDE).

    Usage:
        from modules.rag_optimizations import OptimizedRAG, GroqLLMClient
        from modules.vector_store import get_vector_store

        store = get_vector_store()
        rag = OptimizedRAG(store, llm_client=GroqLLMClient())
        rag.add_document("uploads/report.pdf")
        context = rag.get_context("What are the pricing plans?")
    """

    def __init__(self, vector_store, llm_client: Optional[LLMClient] = None):
        self._store = vector_store
        self._llm = llm_client or GroqLLMClient()
        self._contextual = ContextualRetriever(vector_store, self._llm)
        self._hyde = HyDERetriever(vector_store, self._llm)

    def add_document(self, file_path: str) -> str:
        """Add document with contextual summary (Contextual Retrieval)."""
        return self._contextual.add_document(file_path)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search using HyDE (hypothetical answer as query)."""
        return self._hyde.search(query, top_k)

    def get_context(self, query: str, top_k: int = 5) -> str:
        """Full optimized pipeline: HyDE search + context formatting."""
        return self._hyde.get_context(query, top_k)

    def list_documents(self) -> List[str]:
        return self._store.list_documents()

    def get_stats(self) -> Dict:
        return self._store.get_stats()

    def clear(self):
        self._store.clear()
        self._contextual._summaries.clear()

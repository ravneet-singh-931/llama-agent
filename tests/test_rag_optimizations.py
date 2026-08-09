"""
Tests for RAG Optimizations (Contextual Retrieval + HyDE).

All tests are OFFLINE-SAFE: they use a FakeVectorStore and a FakeLLMClient
so no real ChromaDB or Groq API key is required.

Run with: pytest tests/test_rag_optimizations.py -v
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============ FAKES ============


class FakeLLMClient:
    """Deterministic fake LLM client for testing.

    Returns canned responses based on the prompt content so tests can
    assert exactly what was generated.
    """

    def __init__(self, summary="This document covers pricing plans and features.",
                 hypothetical="The pricing plans include a free tier at $0, a pro tier at $29/month, and an enterprise tier with custom pricing. All plans include basic support, while paid tiers add priority support and advanced analytics."):
        self._summary = summary
        self._hypothetical = hypothetical
        self.summary_calls = []
        self.hyde_calls = []

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        # Detect which kind of call this is by inspecting the system prompt
        if "summarizes documents" in system_prompt.lower():
            self.summary_calls.append({"system": system_prompt, "user": user_prompt})
            return self._summary
        elif "hypothetical answer" in system_prompt.lower():
            self.hyde_calls.append({"system": system_prompt, "user": user_prompt})
            return self._hypothetical
        return ""


class FakeCollection:
    """Mimics the ChromaDB collection interface used by VectorStore."""

    def __init__(self):
        self.stored = []  # list of (ids, documents, metadatas)
        self._count = 0

    def upsert(self, ids, documents, metadatas):
        self.stored.append({"ids": ids, "documents": documents, "metadatas": metadatas})
        self._count += len(ids)

    def query(self, query_texts, n_results, include):
        # Return canned results based on the query text
        query = query_texts[0] if query_texts else ""
        results = {
            "documents": [[f"Result for: {query}"]],
            "metadatas": [[{"file": "test.txt", "chunk_index": 0}]],
            "distances": [[0.1]],
        }
        return results

    def count(self):
        return self._count

    def get(self, include):
        return {"metadatas": []}


class FakeVectorStore:
    """Mimics the VectorStore interface for testing."""

    def __init__(self):
        self.collection = FakeCollection()
        self.add_document_calls = []
        self.search_calls = []

    def add_document(self, file_path: str) -> str:
        self.add_document_calls.append(file_path)
        return f"Added document from {file_path}"

    def search(self, query: str, top_k: int = 5) -> list:
        self.search_calls.append({"query": query, "top_k": top_k})
        return [
            {
                "content": f"Result for: {query}",
                "file": "test.txt",
                "chunk_index": 0,
                "similarity": 0.9,
            }
        ]

    def get_context(self, query: str, top_k: int = 5) -> str:
        results = self.search(query, top_k)
        if not results:
            return "No documents have been uploaded."
        context = "Relevant document excerpts:\n\n"
        for r in results:
            context += f"--- Source: {r['file']} ---\n{r['content']}\n\n"
        return context

    def list_documents(self) -> list:
        return ["test.txt"]

    def get_stats(self) -> dict:
        return {"total_chunks": 5, "total_documents": 1, "documents": ["test.txt"]}

    def clear(self):
        self.collection = FakeCollection()


# ============ CONTEXTUAL RETRIEVAL TESTS ============


class TestContextualRetrieval:
    """Tests for ContextualRetriever — summary prepended to chunks."""

    def test_summary_prepended_to_each_chunk(self):
        """Each chunk stored in ChromaDB should have the summary prefix."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="This is a 2-line summary of the document.")
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("A" * 2000)  # long enough to produce multiple chunks
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
        finally:
            os.unlink(file_path)

        # Verify the LLM was called to generate a summary
        assert len(llm.summary_calls) == 1, "LLM should be called once for summary"

        # Verify chunks were stored with the summary prefix
        assert len(store.collection.stored) == 1, "One upsert call expected"
        upsert = store.collection.stored[0]
        for doc in upsert["documents"]:
            assert doc.startswith("[Document Summary: This is a 2-line summary of the document.]"), \
                f"Chunk missing summary prefix: {doc[:80]}..."

    def test_summary_cached_across_chunks(self):
        """The summary should be generated once per document, not per chunk."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Cached summary.")
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("B" * 2000)
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
        finally:
            os.unlink(file_path)

        assert len(llm.summary_calls) == 1, "Summary should be cached (called once)"

    def test_no_llm_falls_back_to_plain_chunks(self):
        """When LLM returns empty, chunks should be stored without prefix."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="")  # LLM unavailable
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Some content here.")
            f.flush()
            file_path = f.name

        try:
            result = retriever.add_document(file_path)
        finally:
            os.unlink(file_path)

        assert "no LLM" in result, f"Expected 'no LLM' in result: {result}"
        # Chunks should NOT have the summary prefix
        upsert = store.collection.stored[0]
        for doc in upsert["documents"]:
            assert not doc.startswith("[Document Summary:"), \
                "Chunk should not have summary prefix when LLM is unavailable"

    def test_metadata_has_context_flag(self):
        """Metadata should record whether a summary was prepended."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary text.")
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
        finally:
            os.unlink(file_path)

        upsert = store.collection.stored[0]
        for meta in upsert["metadatas"]:
            assert meta["has_context"] is True

    def test_search_delegates_to_store(self):
        """Search should pass through to the underlying vector store unchanged."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        retriever = ContextualRetriever(store, llm_client=llm)

        results = retriever.search("test query", top_k=3)
        assert len(results) == 1
        assert results[0]["content"] == "Result for: test query"

    def test_clear_resets_summary_cache(self):
        """clear() should reset both the store and the summary cache."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary.")
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
            assert len(retriever._summaries) == 1
            retriever.clear()
            assert len(retriever._summaries) == 0
        finally:
            os.unlink(file_path)


# ============ HYDE TESTS ============


class TestHyDE:
    """Tests for HyDERetriever — hypothetical answer used as query."""

    def test_hypothetical_answer_used_as_query(self):
        """The search should use the LLM-generated hypothetical answer, not the raw query."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        fake_answer = "The pricing includes a free tier, pro at $29, and enterprise with custom pricing."
        llm = FakeLLMClient(hypothetical=fake_answer)
        retriever = HyDERetriever(store, llm_client=llm)

        retriever.search("pricing", top_k=5)

        # The store should have been called with the hypothetical answer
        assert len(store.search_calls) == 1
        assert store.search_calls[0]["query"] == fake_answer, \
            f"Expected hypothetical answer as query, got: {store.search_calls[0]['query']}"

    def test_hyde_calls_llm_once_per_query(self):
        """Each search should trigger exactly one LLM call for the hypothetical answer."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        retriever = HyDERetriever(store, llm_client=llm)

        retriever.search("What is the refund policy?")

        assert len(llm.hyde_calls) == 1, "LLM should be called once for HyDE"
        assert "What is the refund policy?" in llm.hyde_calls[0]["user"]

    def test_no_llm_falls_back_to_original_query(self):
        """When LLM returns empty, the original query should be used."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="")  # LLM unavailable
        retriever = HyDERetriever(store, llm_client=llm)

        retriever.search("original query", top_k=5)

        assert store.search_calls[0]["query"] == "original query", \
            "Should fall back to original query when LLM is unavailable"

    def test_get_context_uses_hyde_search(self):
        """get_context should use the HyDE-expanded query."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="Hypothetical answer about pricing.")
        retriever = HyDERetriever(store, llm_client=llm)

        context = retriever.get_context("pricing plans")

        assert "Hypothetical answer about pricing." in context or "Result for" in context
        assert len(llm.hyde_calls) == 1

    def test_get_context_empty_results(self):
        """get_context should handle empty results gracefully."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        # Simulate that documents exist in the store (count > 0) but
        # search returns no matches for this particular query.
        store.collection._count = 5
        store.search = lambda q, top_k=5: []  # override to return empty
        llm = FakeLLMClient(hypothetical="Some answer.")
        retriever = HyDERetriever(store, llm_client=llm)

        context = retriever.get_context("query")
        assert "No relevant documents found" in context

    def test_add_document_delegates_to_store(self):
        """add_document should pass through to the underlying store."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        retriever = HyDERetriever(store, llm_client=llm)

        result = retriever.add_document("test.txt")
        assert "test.txt" in result
        assert store.add_document_calls == ["test.txt"]


# ============ OPTIMIZED RAG (COMBINED) TESTS ============


class TestOptimizedRAG:
    """Tests for OptimizedRAG — combines Contextual Retrieval + HyDE."""

    def test_add_document_uses_contextual_retrieval(self):
        """add_document should prepend summaries (Contextual Retrieval)."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Document summary.")
        rag = OptimizedRAG(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("X" * 1500)
            f.flush()
            file_path = f.name

        try:
            rag.add_document(file_path)
        finally:
            os.unlink(file_path)

        # Summary should have been generated
        assert len(llm.summary_calls) == 1
        # Chunks should have the summary prefix
        upsert = store.collection.stored[0]
        for doc in upsert["documents"]:
            assert doc.startswith("[Document Summary:")

    def test_search_uses_hyde(self):
        """search should use HyDE (hypothetical answer as query)."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="Hypothetical answer.")
        rag = OptimizedRAG(store, llm_client=llm)

        rag.search("user query", top_k=5)

        assert len(llm.hyde_calls) == 1
        assert store.search_calls[0]["query"] == "Hypothetical answer."

    def test_get_context_full_pipeline(self):
        """get_context should run HyDE search and format results."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary.", hypothetical="Hypothetical answer.")
        rag = OptimizedRAG(store, llm_client=llm)

        context = rag.get_context("What is the pricing?")

        assert "Relevant document excerpts" in context
        assert len(llm.hyde_calls) == 1

    def test_clear_resets_everything(self):
        """clear should reset the store and summary cache."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary.")
        rag = OptimizedRAG(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            rag.add_document(file_path)
            assert len(rag._contextual._summaries) == 1
            rag.clear()
            assert len(rag._contextual._summaries) == 0
        finally:
            os.unlink(file_path)


# ============ GRACEFUL DEGRADATION TESTS ============


class TestGracefulDegradation:
    """Tests that optimizations degrade gracefully when LLM is unavailable."""

    def test_contextual_retrieval_without_llm(self):
        """ContextualRetriever should work (plain chunks) without an LLM."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="")  # LLM returns nothing
        retriever = ContextualRetriever(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Some content.")
            f.flush()
            file_path = f.name

        try:
            result = retriever.add_document(file_path)
            assert "Added" in result
            # Chunks stored without summary prefix
            upsert = store.collection.stored[0]
            for doc in upsert["documents"]:
                assert not doc.startswith("[Document Summary:")
        finally:
            os.unlink(file_path)

    def test_hyde_without_llm(self):
        """HyDERetriever should fall back to original query without an LLM."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="")  # LLM returns nothing
        retriever = HyDERetriever(store, llm_client=llm)

        results = retriever.search("fallback query")
        assert len(results) == 1
        assert store.search_calls[0]["query"] == "fallback query"

    def test_optimized_rag_without_llm(self):
        """OptimizedRAG should work (plain) without an LLM."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="", hypothetical="")
        rag = OptimizedRAG(store, llm_client=llm)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            # add_document should work (plain chunks)
            result = rag.add_document(file_path)
            assert "Added" in result

            # search should fall back to original query
            rag.search("test query")
            assert store.search_calls[0]["query"] == "test query"
        finally:
            os.unlink(file_path)


# ============ INTEGRATION: REAL VectorStore INTERFACE ============


class TestVectorStoreCompatibility:
    """Verify the wrappers are compatible with the real VectorStore interface."""

    def test_contextual_retriever_has_vector_store_methods(self):
        """ContextualRetriever should expose the same interface as VectorStore."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        retriever = ContextualRetriever(store, llm_client=FakeLLMClient())

        # These methods should exist and delegate to the store
        assert callable(retriever.search)
        assert callable(retriever.get_context)
        assert callable(retriever.list_documents)
        assert callable(retriever.get_stats)
        assert callable(retriever.clear)
        assert callable(retriever.add_document)

    def test_hyde_retriever_has_vector_store_methods(self):
        """HyDERetriever should expose the same interface as VectorStore."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        retriever = HyDERetriever(store, llm_client=FakeLLMClient())

        assert callable(retriever.search)
        assert callable(retriever.get_context)
        assert callable(retriever.list_documents)
        assert callable(retriever.get_stats)
        assert callable(retriever.clear)
        assert callable(retriever.add_document)

    def test_optimized_rag_has_vector_store_methods(self):
        """OptimizedRAG should expose the same interface as VectorStore."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        assert callable(rag.search)
        assert callable(rag.get_context)
        assert callable(rag.list_documents)
        assert callable(rag.get_stats)
        assert callable(rag.clear)
        assert callable(rag.add_document)

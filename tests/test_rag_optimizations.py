"""
Tests for RAG Optimizations Module.

All tests are OFFLINE-SAFE: they use fakes and mock LLM calls.
No real ChromaDB, Groq, or LLM API calls are required.

Run with: pytest tests/test_rag_optimizations.py -v
"""

import os
import sys
import tempfile
import hashlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# =============================================================================
# 0.  FAKES
# =============================================================================


class FakeLLMClient:
    """Deterministic fake LLM client for testing.

    Returns canned responses based on the prompt content.
    """

    def __init__(self, summary="This document covers pricing plans and features.",
                 hypothetical="The pricing plans include a free tier at $0, pro at $29/month, and enterprise tier with custom pricing. All plans include basic support, while paid tiers add priority support and advanced analytics."):
        self._summary = summary
        self._hypothetical = hypothetical
        self.summary_calls = []
        self.hyde_calls = []
        self.verify_calls = []
        self.rewrite_calls = []
        self.history_calls = []

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        sp = system_prompt.lower()
        if "summarizes documents" in sp:
            self.summary_calls.append({"system": system_prompt, "user": user_prompt})
            return self._summary
        elif "hypothetical answer" in sp:
            self.hyde_calls.append({"system": system_prompt, "user": user_prompt})
            return self._hypothetical
        elif "verify" in sp:
            self.verify_calls.append({"system": system_prompt, "user": user_prompt})
            # Return a sentence that IS in the FakeVectorStore context
            # so _is_supported returns True (no retry needed)
            return "Result for: test query"
        elif "rewrite" in sp:
            self.rewrite_calls.append({"system": system_prompt, "user": user_prompt})
            return "Rewrite the query to find evidence for the missing evidence."
        elif "history" in sp:
            self.history_calls.append({"system": system_prompt, "user": user_prompt})
            return "Based on the conversation history, the user is asking about pricing comparison."
        return ""


class FakeChromaCollection:
    """Minimal fake ChromaDB collection with proper document support."""

    def __init__(self):
        self._ids = []
        self._documents = []
        self._metadatas = []
        self._data = []  # Track upsert calls for test inspection

    def upsert(self, ids, documents, metadatas):
        self._data.append({"ids": ids, "documents": documents, "metadatas": metadatas})
        self._ids.extend(ids)
        self._documents.extend(documents)
        self._metadatas.extend(metadatas)

    def query(self, query_texts, n_results, include):
        if not self._documents:
            return {
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }
        return {
            "documents": [list(self._documents[:n_results])],
            "metadatas": [list(self._metadatas[:n_results])],
            # Like ChromaDB: an identical text is at distance ~0.
            "distances": [[0.0 if d == query_texts[0] else 0.1
                           for d in self._documents[:n_results]]],
        }

    def count(self):
        return len(self._documents)

    def get(self, include):
        result = {}
        if "documents" in include:
            result["documents"] = list(self._documents)
        if "metadatas" in include:
            result["metadatas"] = list(self._metadatas)
        if "ids" in include:
            result["ids"] = list(self._ids)
        return result

    def delete_collection(self):
        self._ids = []
        self._documents = []
        self._metadatas = []
        self._data = []


class FakeChromaClient:
    """Minimal fake ChromaDB client."""

    def __init__(self):
        self._collections = {}

    def get_or_create_collection(self, name, metadata=None):
        if name not in self._collections:
            self._collections[name] = FakeChromaCollection()
        return self._collections[name]

    def delete_collection(self, name):
        if name in self._collections:
            del self._collections[name]


class FakeVectorStore:
    """Mimics the VectorStore interface for testing."""

    def __init__(self):
        self.client = FakeChromaClient()
        self.collection = self.client.get_or_create_collection(name="documents")
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
        if self.collection.count() == 0:
            return []
        all_data = self.collection.get(include=["metadatas"])
        files = set()
        for meta in all_data.get("metadatas", []):
            files.add(meta.get("file", "unknown"))
        return list(files)

    def get_stats(self) -> dict:
        docs = self.list_documents()
        return {
            "total_chunks": self.collection.count(),
            "total_documents": len(docs),
            "documents": docs,
        }

    def clear(self):
        self.client.delete_collection("documents")
        self.collection = self.client.get_or_create_collection(name="documents")
        self.add_document_calls = []
        self.search_calls = []


# =============================================================================
# 1.  SEMANTIC CHUNKER
# =============================================================================


class TestSemanticChunker:
    """Tests for SemanticChunker."""

    def test_split_preserves_sentences(self):
        """SemanticChunker should split on sentence boundaries."""
        from modules.rag_optimizations import SemanticChunker

        chunker = SemanticChunker(chunk_size=30, overlap=10)
        text = "This is the first sentence. This is the second sentence. And the third."
        chunks = chunker.split(text)
        assert len(chunks) == 3
        assert chunks[0].startswith("This is the first sentence.")
        assert chunks[1].startswith("This is the second sentence.")
        assert chunks[2].startswith("And the third.")

    def test_split_with_code_block(self):
        """Chunks should preserve code blocks."""
        from modules.rag_optimizations import SemanticChunker

        chunker = SemanticChunker(chunk_size=1000, overlap=100)
        text = (
            "Here is some code:\n\n"
            "def hello():\n"
            "    return 'world'\n\n"
            "This is a paragraph after the code."
        )
        chunks = chunker.split(text)
        # Code block should be in one chunk
        assert any("def hello" in c for c in chunks)

    def test_split_empty(self):
        """Empty text should return no chunks."""
        from modules.rag_optimizations import SemanticChunker

        chunker = SemanticChunker(chunk_size=1000, overlap=100)
        assert chunker.split("") == []

    def test_split_single_sentence(self):
        """Single sentence should be one chunk."""
        from modules.rag_optimizations import SemanticChunker

        chunker = SemanticChunker(chunk_size=1000, overlap=100)
        assert chunker.split("Hello world.") == ["Hello world."]


# =============================================================================
# 2.  HYBRID SEARCH
# =============================================================================


class TestHybridSearcher:
    """Tests for HybridSearcher (BM25 + Vector)."""

    def test_hybrid_search_uses_bm25(self):
        """Hybrid search should use BM25 for keyword matching."""
        from modules.rag_optimizations import HybridSearcher

        store = FakeVectorStore()
        # Seed the collection with documents so BM25 can build an index
        store.collection.upsert(
            ids=["doc_0"],
            documents=["INV-8847 is a critical bug in the API_KEY module. Section 4.2 describes the fix."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        searcher = HybridSearcher(vector_store=store, bm25_weight=0.3, vector_weight=0.7)

        results = searcher.search("INV-8847", top_k=5)
        assert len(results) > 0
        # BM25 should contribute to the score
        assert any("bm25_score" in r for r in results)

    def test_hybrid_search_uses_vector(self):
        """Hybrid search should use vector similarity."""
        from modules.rag_optimizations import HybridSearcher

        store = FakeVectorStore()
        store.collection.upsert(
            ids=["doc_0"],
            documents=["Pricing plans include free, pro, and enterprise tiers."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        searcher = HybridSearcher(vector_store=store, bm25_weight=0.3, vector_weight=0.7)

        results = searcher.search("pricing", top_k=5)
        assert len(results) > 0
        assert any("similarity" in r for r in results)

    def test_hybrid_search_combines_scores(self):
        """Hybrid search should combine BM25 and vector scores."""
        from modules.rag_optimizations import HybridSearcher

        store = FakeVectorStore()
        store.collection.upsert(
            ids=["doc_0"],
            documents=["Test document content for hybrid search testing."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        searcher = HybridSearcher(vector_store=store, bm25_weight=0.3, vector_weight=0.7)

        results = searcher.search("test query", top_k=5)
        assert len(results) > 0
        # Each result should have both similarity and bm25_score
        for r in results:
            assert "similarity" in r
            assert "bm25_score" in r

    def test_hybrid_search_fallback_when_bm25_unavailable(self):
        """When BM25 index is not built, should still work."""
        from modules.rag_optimizations import HybridSearcher

        store = FakeVectorStore()
        searcher = HybridSearcher(vector_store=store, bm25_weight=0.3, vector_weight=0.7)

        # Force BM25 index to be None (simulates rank_bm25 not installed)
        searcher._bm25_index = None

        results = searcher.search("test", top_k=5)
        assert len(results) > 0

    def test_hybrid_search_reranking(self):
        """Hybrid search should re-rank results with cross-encoder."""
        from modules.rag_optimizations import HybridSearcher, CrossEncoderReranker

        store = FakeVectorStore()
        store.collection.upsert(
            ids=["doc_0"],
            documents=["Test document content for hybrid search testing."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        reranker = CrossEncoderReranker()
        # Force model to None so it uses the fallback reranker
        reranker._model = None
        searcher = HybridSearcher(
            vector_store=store,
            bm25_weight=0.3,
            vector_weight=0.7,
            reranker=reranker,
        )

        results = searcher.search("test query", top_k=5)
        assert len(results) > 0


# =============================================================================
# 3.  CROSS-ENCODER RANKER
# =============================================================================


class TestCrossEncoderReranker:
    """Tests for CrossEncoderReranker."""

    def test_rerank_uses_cross_encoder(self):
        """CrossEncoderReranker should use sentence-transformers."""
        from modules.rag_optimizations import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        # Mock the model to avoid downloading
        class FakeModel:
            def predict(self, pairs):
                results = []
                for query, doc in pairs:
                    query_words = set(query.lower().split())
                    doc_words = set(doc.lower().split())
                    overlap = len(query_words & doc_words) / max(len(query_words), 1)
                    results.append(overlap)
                return results
        reranker._model = FakeModel()

        results = [
            {"content": "Refund policy: 30 days", "similarity": 0.7},
            {"content": "Pricing plans start at $9", "similarity": 0.8},
            {"content": "Contact support for help", "similarity": 0.6},
        ]
        reranked = reranker.rerank("What is the refund policy?", results, top_k=2)
        assert len(reranked) == 2
        # The most relevant result should be first
        assert reranked[0]["content"].startswith("Refund")

    def test_rerank_fallback_when_model_unavailable(self):
        """When sentence-transformers is unavailable, should fall back."""
        from modules.rag_optimizations import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        # Force model to be None (unavailable)
        reranker._model = None

        results = [
            {"content": "Refund policy: 30 days", "similarity": 0.7},
            {"content": "Pricing plans start at $9", "similarity": 0.8},
        ]
        reranked = reranker.rerank("What is the refund policy?", results, top_k=2)
        # Should still return results (fallback to keyword reranker)
        assert len(reranked) == 2


# =============================================================================
# 4.  CONTEXTUAL RETRIEVER
# =============================================================================


class TestContextualRetriever:
    """Tests for ContextualRetriever (summary + semantic chunking)."""

    def test_summary_prepended_to_chunk(self):
        """Each chunk should have the document summary prepended."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary of the document.")
        retriever = ContextualRetriever(store, llm_client=llm, contextual=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("A" * 2000)
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
            # Verify the summary was prepended
            assert len(store.collection._data) == 1
            upsert = store.collection._data[0]
            for doc in upsert["documents"]:
                assert doc.startswith("[Document Summary: Summary of the document.]")
        finally:
            os.unlink(file_path)

    def test_summary_cached(self):
        """Summary should be cached per document."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Cached summary.")
        retriever = ContextualRetriever(store, llm_client=llm, contextual=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("B" * 2000)
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
            assert len(llm.summary_calls) == 1
        finally:
            os.unlink(file_path)

    def test_no_llm_falls_back_to_plain_chunks(self):
        """When LLM is unavailable, chunks should be stored without prefix."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="")  # LLM returns nothing
        retriever = ContextualRetriever(store, llm_client=llm, contextual=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Some content here.")
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
            # Chunks should NOT have the summary prefix
            upsert = store.collection._data[0]
            for doc in upsert["documents"]:
                assert not doc.startswith("[Document Summary:")
        finally:
            os.unlink(file_path)

    def test_metadata_has_context_flag(self):
        """Metadata should record whether a summary was prepended."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(summary="Summary text.")
        retriever = ContextualRetriever(store, llm_client=llm, contextual=True)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            retriever.add_document(file_path)
            upsert = store.collection._data[0]
            for meta in upsert["metadatas"]:
                assert meta["has_context"] is True
        finally:
            os.unlink(file_path)


    # v3.4.8: summaries are opt-in, and the budget covers the reasoning model.
    def _add(self, retriever):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            path = f.name
        try:
            return retriever.add_document(path)
        finally:
            os.unlink(path)

    def test_summaries_are_off_by_default(self, monkeypatch):
        from modules.rag_optimizations import ContextualRetriever
        monkeypatch.delenv("RAG_CONTEXTUAL", raising=False)
        store, llm = FakeVectorStore(), FakeLLMClient(summary="Summary text.")
        result = self._add(ContextualRetriever(store, llm_client=llm))
        assert llm.summary_calls == []
        assert "plain chunks" in result
        assert all(m["has_context"] is False for m in store.collection._data[0]["metadatas"])

    def test_env_flag_turns_summaries_on(self, monkeypatch):
        from modules.rag_optimizations import ContextualRetriever
        monkeypatch.setenv("RAG_CONTEXTUAL", "1")
        store, llm = FakeVectorStore(), FakeLLMClient(summary="Summary text.")
        assert "contextual retrieval" in self._add(ContextualRetriever(store, llm_client=llm))
        assert len(llm.summary_calls) == 1

    def test_summary_budget_leaves_room_for_reasoning(self):
        """max_tokens=100 left the reasoning fast model nothing to answer with."""
        from modules.rag_optimizations import ContextualRetriever
        budgets = []

        class Recording(FakeLLMClient):
            def complete(self, system_prompt, user_prompt, max_tokens=500):
                budgets.append(max_tokens)
                return super().complete(system_prompt, user_prompt, max_tokens)

        self._add(ContextualRetriever(FakeVectorStore(), llm_client=Recording(), contextual=True))
        assert budgets and budgets[0] >= 256

    def test_empty_summary_is_not_cached(self):
        from modules.rag_optimizations import ContextualRetriever
        llm = FakeLLMClient(summary="")
        retriever = ContextualRetriever(FakeVectorStore(), llm_client=llm, contextual=True)
        self._add(retriever)
        self._add(retriever)
        assert len(llm.summary_calls) == 2  # retried, not served from the summary cache
        assert retriever._summaries == {}


# =============================================================================
# 5.  HYDE
# =============================================================================


class TestHyDERetriever:
    """Tests for HyDERetriever (hypothetical answer as query)."""

    def test_hypothetical_answer_used_as_query(self):
        """HyDE should use the LLM-generated hypothetical answer as the search query."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="Hypothetical answer for pricing.")
        retriever = HyDERetriever(store, llm_client=llm)

        retriever.search("pricing", top_k=5)

        assert len(llm.hyde_calls) == 1
        assert store.search_calls[0]["query"] == "Hypothetical answer for pricing."

    def test_hyde_falls_back_to_original_query(self):
        """When LLM returns empty, original query is used."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="")  # LLM unavailable
        retriever = HyDERetriever(store, llm_client=llm)

        retriever.search("original query", top_k=5)

        assert store.search_calls[0]["query"] == "original query"

    def test_hyde_get_context(self):
        """get_context should use HyDE search."""
        from modules.rag_optimizations import HyDERetriever

        store = FakeVectorStore()
        llm = FakeLLMClient(hypothetical="Hypothetical answer.")
        retriever = HyDERetriever(store, llm_client=llm)

        context = retriever.get_context("pricing")
        assert "Hypothetical answer." in context


# =============================================================================
# 6.  QUERY DECOMPOSITION
# =============================================================================


class TestQueryDecomposer:
    """Tests for QueryDecomposer (sub-question decomposition)."""

    def test_decompose_complex_query(self):
        """Complex queries should be decomposed into sub-questions."""
        from modules.rag_optimizations import QueryDecomposer

        decomposer = QueryDecomposer()
        result = decomposer.decompose("Compare pricing and refund policies")
        assert len(result) > 1
        assert any("pricing" in s.lower() for s in result)
        assert any("refund" in s.lower() for s in result)

    def test_decompose_simple_query(self):
        """Simple queries should not be decomposed."""
        from modules.rag_optimizations import QueryDecomposer

        decomposer = QueryDecomposer()
        result = decomposer.decompose("What is the pricing?")
        assert len(result) == 1
        assert result[0] == "What is the pricing?"

    def test_answer_returns_sub_questions(self):
        """Answer should return sub-questions and sub-answers."""
        from modules.rag_optimizations import QueryDecomposer

        decomposer = QueryDecomposer()
        result = decomposer.answer("Compare pricing and refund policies")
        assert result["decomposed"] is True
        assert len(result["sub_questions"]) > 0
        assert len(result["sub_answers"]) > 0


# =============================================================================
# 7.  CRAG
# =============================================================================


class TestCRAGRetriever:
    """Tests for CRAG (Corrective RAG)."""

    def test_crag_supported_answer(self):
        """CRAG should return supported answer without retries."""
        from modules.rag_optimizations import CRAGRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        crag = CRAGRetriever(store, llm_client=llm)

        result = crag.retrieve_and_answer("test query", top_k=5, max_retries=2)
        assert result["supported"] is True
        assert result["retries"] == 0

    def test_crag_unsupported_answer(self):
        """CRAG should retry when answer is not supported."""
        from modules.rag_optimizations import CRAGRetriever

        store = FakeVectorStore()
        # Use a FakeLLMClient that returns an unsupported answer for verify
        llm = FakeLLMClient()
        # Override verify response to be unsupported
        original_complete = llm.complete

        def patched_complete(system_prompt, user_prompt, max_tokens=500):
            sp = system_prompt.lower()
            if "verify" in sp:
                # Return an answer that is NOT in the context
                return "The answer is 42 and the moon is made of cheese."
            return original_complete(system_prompt, user_prompt, max_tokens)

        llm.complete = patched_complete
        crag = CRAGRetriever(store, llm_client=llm)

        result = crag.retrieve_and_answer("test query", top_k=5, max_retries=2)
        assert result["supported"] is True  # Will be supported after retry
        assert result["retries"] >= 1

    def test_crag_rewrite_query(self):
        """CRAG should rewrite the query when answer is unsupported."""
        from modules.rag_optimizations import CRAGRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        crag = CRAGRetriever(store, llm_client=llm)

        result = crag.retrieve_and_answer("test query", top_k=5, max_retries=2)
        assert "retries" in result


# =============================================================================
# 8.  CONVERSATIONAL RETRIEVER
# =============================================================================


class TestConversationalRetriever:
    """Tests for ConversationalRetriever (history-aware retrieval)."""

    def test_retrieve_with_history(self):
        """ConversationalRetriever should rewrite queries using history."""
        from modules.rag_optimizations import ConversationalRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        retriever = ConversationalRetriever(store, llm_client=llm)

        history = "User: 'Summarize CRM-3 pricing' AI: '$100'"
        result = retriever.retrieve_with_history("What about that other file?", history, top_k=5)

        assert "rewritten_query" in result
        assert result["rewritten_query"] != "What about that other file?"

    def test_retrieve_without_history(self):
        """Without history, should still work (return original query)."""
        from modules.rag_optimizations import ConversationalRetriever

        store = FakeVectorStore()
        llm = FakeLLMClient()
        retriever = ConversationalRetriever(store, llm_client=llm)

        result = retriever.retrieve_with_history("test query", "", top_k=5)
        assert "rewritten_query" in result


# =============================================================================
# 9.  SEMANTIC CACHE
# =============================================================================


class TestSemanticCache:
    """Tests for SemanticCache."""

    def test_lookup_caches_answer(self):
        """SemanticCache should cache and return cached answers."""
        from modules.rag_optimizations import SemanticCache

        store = FakeVectorStore()
        cache = SemanticCache(store)

        query = "test query"
        answer = "Cached answer for test query."

        cache.store(query, answer)
        result = cache.lookup(query)

        assert result is not None
        assert result == answer

    def test_lookup_returns_none_when_missing(self):
        """Lookup should return None when no cache entry exists."""
        from modules.rag_optimizations import SemanticCache

        store = FakeVectorStore()
        cache = SemanticCache(store)

        result = cache.lookup("nonexistent query")
        assert result is None

    def test_clear_caches(self):
        """Clear should remove all cache entries."""
        from modules.rag_optimizations import SemanticCache

        store = FakeVectorStore()
        cache = SemanticCache(store)

        cache.store("query1", "answer1")
        cache.store("query2", "answer2")
        assert cache.collection.count() == 2

        cache.clear()
        assert cache.collection.count() == 0

    def test_lookup_matches_on_query_not_answer(self):
        """Regression: a repeat query must match the stored *query* (distance
        ~0), not the stored *answer*. The old code embedded the answer as the
        document, so repeat questions never matched and the cache never hit."""
        from modules.rag_optimizations import SemanticCache

        class QueryAwareCollection:
            def __init__(self):
                self._docs = []
                self._metas = []
            def upsert(self, ids, documents, metadatas):
                self._docs.extend(documents)
                self._metas.extend(metadatas)
            def query(self, query_texts, n_results=3, include=None):
                q = query_texts[0]
                # Simulate ChromaDB: distance 0 only when the query equals the
                # embedded (stored) document; otherwise it is far away.
                if self._docs and q == self._docs[0]:
                    return {"metadatas": [[self._metas[0]]], "distances": [[0.0]]}
                return {"metadatas": [[]], "distances": [[]]}

        class QueryAwareClient:
            def get_or_create_collection(self, name, metadata=None):
                return QueryAwareCollection()

        class Store:
            client = QueryAwareClient()

        cache = SemanticCache(Store())
        cache.store(
            "What does OptimizedRAG include?",
            "A long answer that is nothing like the question text.",
        )
        # Repeat of the SAME question must hit (query matches query at dist 0).
        assert cache.lookup("What does OptimizedRAG include?") == (
            "A long answer that is nothing like the question text."
        )
        # A different question must miss.
        assert cache.lookup("What is the weather in Paris?") is None


# =============================================================================
# 10.  OPTIMIZED RAG (full pipeline)
# =============================================================================


class TestOptimizedRAG:
    """Tests for OptimizedRAG (full pipeline combining all features)."""

    def test_add_document_uses_contextual_retrieval(self):
        """add_document should prepend summaries."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            rag.add_document(file_path)
            # Verify chunks were added to the collection
            assert len(store.collection._data) == 1
        finally:
            os.unlink(file_path)

    def test_search_uses_hybrid(self):
        """search should use hybrid search."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        store.collection.upsert(
            ids=["doc_0"],
            documents=["Test document content for hybrid search testing."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        results = rag.search("test query", top_k=5)
        assert len(results) > 0

    def test_get_context_uses_hybrid(self):
        """get_context should use hybrid search."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        store.collection.upsert(
            ids=["doc_0"],
            documents=["Test document content for hybrid search testing."],
            metadatas=[{"file": "test.txt", "chunk_index": 0}],
        )
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        context = rag.get_context("test query", top_k=5)
        assert "Relevant document excerpts" in context

    def test_clear_resets_all(self):
        """clear should reset all internal state."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            rag.add_document(file_path)
            assert len(rag.list_documents()) == 1

            rag.clear()
            assert len(rag.list_documents()) == 0
        finally:
            os.unlink(file_path)


# =============================================================================
# 11.  INTEGRATION: REAL VectorStore INTERFACE
# =============================================================================


class TestVectorStoreCompatibility:
    """Verify the wrappers are compatible with the real VectorStore interface."""

    def test_contextual_retriever_has_vector_store_methods(self):
        """ContextualRetriever should expose the same interface as VectorStore."""
        from modules.rag_optimizations import ContextualRetriever

        store = FakeVectorStore()
        retriever = ContextualRetriever(store, llm_client=FakeLLMClient())

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


# =============================================================================
# 12.  FULL TEST SUITE
# =============================================================================


class TestFullRAGOptimizations:
    """Full integration test for all 7 features."""

    def test_all_features_work_together(self):
        """All 7 features should work together in OptimizedRAG."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        rag = OptimizedRAG(store, llm_client=FakeLLMClient())

        # Add a document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            result = rag.add_document(file_path)
            assert "Added" in result

            # Search
            results = rag.search("test query", top_k=5)
            assert len(results) > 0

            # Get context
            context = rag.get_context("test query", top_k=5)
            assert "Relevant document excerpts" in context

            # Stats
            stats = rag.get_stats()
            assert "hybrid" in stats
            assert "contextual" in stats
            assert "cache" in stats
        finally:
            os.unlink(file_path)

    def test_all_features_with_llm(self):
        """All features should work with a real LLM client."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        llm = FakeLLMClient()
        rag = OptimizedRAG(store, llm_client=llm)

        # Test contextual retrieval
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            result = rag.add_document(file_path)
            assert "Added" in result

            # Test hybrid search
            results = rag.search("test query", top_k=5)
            assert len(results) > 0

            # Test HyDE
            results = rag.search("test query", top_k=5)
            assert len(results) > 0

            # Test query decomposition
            decomp = rag._decomposer.answer("Compare pricing and refund policies")
            assert decomp["decomposed"] is True

            # Test CRAG
            crag_result = rag._crag.retrieve_and_answer("test query", top_k=5)
            assert "supported" in crag_result

            # Test conversational retrieval
            conv_result = rag._conversational.retrieve_with_history("test query", "history", top_k=5)
            assert "rewritten_query" in conv_result

            # Test semantic cache
            cache = rag._cache
            cache.store("test query", "cached answer")
            result = cache.lookup("test query")
            assert result is not None
        finally:
            os.unlink(file_path)

    def test_graceful_degradation_without_llm(self):
        """When LLM is unavailable, features should still work."""
        from modules.rag_optimizations import OptimizedRAG

        store = FakeVectorStore()
        rag = OptimizedRAG(store, llm_client=None)

        # add_document should work (no LLM needed)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Content.")
            f.flush()
            file_path = f.name

        try:
            result = rag.add_document(file_path)
            assert "Added" in result

            # search should work (no LLM needed)
            results = rag.search("test query", top_k=5)
            assert len(results) > 0

            # get_context should work (no LLM needed)
            context = rag.get_context("test query", top_k=5)
            assert "Relevant document excerpts" in context
        finally:
            os.unlink(file_path)


# =============================================================================
# 13.  RUN ALL TESTS
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
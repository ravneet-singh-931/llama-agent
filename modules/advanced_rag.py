"""
Advanced RAG Module — Rearranging, SubQuestion, Router, Langfuse

This module adds 4 advanced features on top of ChromaDB:

1. RE-RANKING (Rearranging)
   After vector search finds 10 results, re-rank them for better relevance.
   Example: Initial search returns chunks in wrong order → re-rank puts
   the most relevant chunk first.

2. SUBQUESTION QUERY ENGINE
   Breaks complex questions into sub-questions, answers each, combines.
   Example: "Compare pricing and refund policies"
   → "What are the pricing plans?" + "What is the refund policy?"
   → Answer each → Combine into comparison

3. ROUTER QUERY ENGINE
   Routes queries to different document collections based on content type.
   Example: "How does this function work?" → Routes to code collection
            "What is the API documentation?" → Routes to docs collection

4. LANGFUSE OBSERVABILITY
   Logs every step: timing, tokens, what was searched, what was found.
"""

import time
from typing import List, Dict, Any


# ============ 1. RE-RANKING (Rearranging) ============


class ReRanker:
    """
    Re-ranks search results for better relevance.
    
    How it works:
      1. Initial vector search returns chunks sorted by cosine similarity
      2. Re-ranker applies ADDITIONAL scoring:
         - Keyword overlap with query
         - Chunk position (earlier in document = often more important)
         - Chunk length (too short = not enough info, too long = noisy)
         - Exact phrase matching
      3. Returns re-sorted results
    
    Why it helps:
      Vector search sometimes puts a partially-matching chunk #1 when
      a better chunk is at #3. Re-ranker fixes this.
    """
    
    def __init__(self):
        self.keyword_weight = 0.3
        self.position_weight = 0.1
        self.length_weight = 0.1
        self.phrase_weight = 0.5
    
    def rerank(
        self,
        query: str,
        results: List[Dict],
        top_k: int = 5,
    ) -> List[Dict]:
        """
        Re-rank search results.
        
        Args:
            query: Original search query
            results: Initial search results (from ChromaDB)
            top_k: Number of results to return after re-ranking
        
        Returns:
            Re-ranked list of results
        """
        if not results:
            return []
        
        query_lower = query.lower()
        query_words = set(query_lower.split())
        
        scored_results = []
        for result in results:
            content = result.get("content", "").lower()
            
            # Score 1: Keyword overlap (how many query words appear)
            content_words = set(content.split())
            keyword_score = len(query_words & content_words) / max(len(query_words), 1)
            
            # Score 2: Position importance (chunk 0 is usually introduction/summary)
            chunk_idx = result.get("chunk_index", 0)
            total_chunks = result.get("total_chunks", 10)
            position_score = 1.0 - (chunk_idx / max(total_chunks, 1))
            
            # Score 3: Optimal length (200-400 chars is ideal for context)
            content_len = len(content)
            if 200 <= content_len <= 400:
                length_score = 1.0
            elif content_len < 200:
                length_score = content_len / 200
            else:
                length_score = max(0.5, 1.0 - (content_len - 400) / 1000)
            
            # Score 4: Exact phrase match (highest signal)
            phrase_score = 1.0 if query_lower in content else 0.0
            
            # Combined score
            final_score = (
                self.keyword_weight * keyword_score +
                self.position_weight * position_score +
                self.length_weight * length_score +
                self.phrase_weight * phrase_score
            )
            
            # Keep original similarity but blend with re-rank score
            original_sim = result.get("similarity", 0.5)
            blended_score = 0.6 * original_sim + 0.4 * final_score
            
            scored_results.append({
                **result,
                "rerank_score": round(blended_score, 4),
                "original_similarity": original_sim,
            })
        
        # Sort by re-ranked score
        scored_results.sort(key=lambda x: x["rerank_score"], reverse=True)
        
        return scored_results[:top_k]


# ============ 2. SUBQUESTION QUERY ENGINE ============


class SubQuestionEngine:
    """
    Breaks complex questions into sub-questions.
    
    Example:
      Input: "Compare the pricing plans and refund policies of all vendors"
      
      Step 1: Identify sub-questions
        - "What are the pricing plans?"
        - "What are the refund policies?"
      
      Step 2: Answer each sub-question separately
        - Pricing answer: "Basic $9/mo, Premium $29/mo..."
        - Refund answer: "30-day money back guarantee..."
      
      Step 3: Combine into final answer
        - "Pricing: Basic $9/mo, Premium $29/mo. Refund: 30-day..."
    """
    
    # Keywords that indicate complex/comparison questions
    COMPLEX_INDICATORS = [
        "compare", "comparison", "difference", "differences",
        "versus", "vs", "both", "all", "every", "pros and cons",
        "advantages and disadvantages", "better", "best",
        "and also", "as well as", "in addition", "moreover",
    ]
    
    # Question patterns that need decomposition
    DECOMPOSE_PATTERNS = [
        "compare {a} and {b}",
        "what are the {a} and {b}",
        "how does {a} differ from {b}",
        "tell me about {a} and {b}",
    ]
    
    def __init__(self, answer_func=None):
        """
        Args:
            answer_func: Function that takes a query string and returns an answer.
                        If None, uses a simple placeholder.
        """
        self.answer_func = answer_func or self._default_answer
    
    def _default_answer(self, query: str) -> str:
        """Default answer function (override with your AI)"""
        return f"[Answer for: {query}]"
    
    def should_decompose(self, query: str) -> bool:
        """
        Check if a query should be decomposed into sub-questions.
        
        Returns True for complex queries that combine multiple topics.
        """
        query_lower = query.lower()
        
        # Check for complex indicators
        for indicator in self.COMPLEX_INDICATORS:
            if indicator in query_lower:
                return True
        
        # Check for "and" connecting different topics
        if " and " in query_lower:
            # Simple heuristic: if "and" appears with a comma or lists topics
            parts = query_lower.split(" and ")
            if len(parts) >= 2:
                # Check if parts seem like different topics
                return True
        
        return False
    
    def decompose(self, query: str) -> List[str]:
        """
        Break a complex query into sub-questions.
        
        Example:
          "Compare pricing and refund policies"
          → ["What are the pricing plans?", "What is the refund policy?"]
        """
        query_lower = query.lower()
        sub_questions = []
        
        # Try to split on common patterns
        # Pattern: "compare X and Y"
        if "compare" in query_lower:
            parts = query_lower.split(" and ")
            if len(parts) >= 2:
                for part in parts:
                    clean = part.replace("compare", "").strip().rstrip("?").strip()
                    if clean:
                        sub_questions.append(f"What are the {clean}?")
        
        # Pattern: "X and Y" with topic keywords
        elif " and " in query_lower:
            parts = query_lower.split(" and ")
            if len(parts) >= 2:
                for part in parts:
                    clean = part.strip().rstrip("?").strip()
                    if clean and len(clean) > 3:
                        # Don't create sub-questions that are too short
                        if any(kw in clean for kw in ["policy", "plan", "feature", "price", "cost", "refund", "code", "api", "data", "test"]):
                            sub_questions.append(f"What is the {clean}?")
                        else:
                            sub_questions.append(f"Tell me about {clean}.")
        
        # Pattern: "tell me about X and Y"
        elif "tell me about" in query_lower:
            parts = query_lower.split(" and ")
            if len(parts) >= 2:
                for part in parts:
                    clean = part.replace("tell me about", "").strip().rstrip("?").strip()
                    if clean:
                        sub_questions.append(f"What is the {clean}?")
        
        # If decomposition failed, create simple sub-questions
        if not sub_questions:
            # Split on commas or semicolons
            for sep in [",", ";", " vs ", " versus "]:
                if sep in query_lower:
                    parts = query_lower.split(sep)
                    for part in parts:
                        clean = part.strip().rstrip("?").strip()
                        if clean and len(clean) > 5:
                            sub_questions.append(f"Tell me about {clean}.")
                    break
        
        # If still no sub-questions, use the original query
        if not sub_questions:
            sub_questions = [query]
        
        return sub_questions
    
    def answer(self, query: str) -> Dict:
        """
        Process a query with sub-question decomposition.
        
        Returns:
            {
                "sub_questions": [...],
                "sub_answers": [...],
                "combined_answer": "...",
                "decomposed": True/False
            }
        """
        # Check if we should decompose
        if not self.should_decompose(query):
            answer = self.answer_func(query)
            return {
                "sub_questions": [query],
                "sub_answers": [answer],
                "combined_answer": answer,
                "decomposed": False,
            }
        
        # Decompose into sub-questions
        sub_questions = self.decompose(query)
        
        # Answer each sub-question
        sub_answers = []
        for sq in sub_questions:
            answer = self.answer_func(sq)
            sub_answers.append(answer)
        
        # Combine answers
        combined = self._combine_answers(query, sub_questions, sub_answers)
        
        return {
            "sub_questions": sub_questions,
            "sub_answers": sub_answers,
            "combined_answer": combined,
            "decomposed": True,
        }
    
    def _combine_answers(
        self,
        original_query: str,
        sub_questions: List[str],
        sub_answers: List[str],
    ) -> str:
        """Combine sub-answers into a coherent response."""
        if len(sub_questions) == 1:
            return sub_answers[0]
        
        combined = f"Based on analysis of {len(sub_questions)} aspects:\n\n"
        for i, (sq, sa) in enumerate(zip(sub_questions, sub_answers), 1):
            combined += f"**{i}. {sq}**\n{sa}\n\n"
        
        combined += f"**Summary:**\n"
        combined += f"To answer your question about {original_query.lower().rstrip('?')}, "
        combined += f"I analyzed {len(sub_questions)} different aspects above."
        
        return combined


# ============ 3. ROUTER QUERY ENGINE ============


class RouterQueryEngine:
    """
    Routes queries to different document collections based on content type.
    
    Example:
      - "How does this function work?" → Routes to code collection
      - "What is the API documentation?" → Routes to docs collection
      - "Show me sales trends" → Routes to data collection
      - "General question" → Routes to all collections
    
    How it works:
      1. Analyze the query to determine what TYPE of content is needed
      2. Route to the appropriate collection(s)
      3. Return combined results
    """
    
    # Keywords that indicate code-related queries
    CODE_KEYWORDS = [
        "function", "class", "method", "variable", "loop", "if statement",
        "import", "def", "return", "error", "exception", "debug",
        "python", "javascript", "code", "script", "programming",
        "algorithm", "data structure", "API endpoint", "database query",
        "syntax", "compile", "runtime", "bug", "fix", "refactor",
    ]
    
    # Keywords that indicate documentation queries
    DOCS_KEYWORDS = [
        "documentation", "docs", "guide", "tutorial", "how to",
        "getting started", "installation", "setup", "configuration",
        "reference", "manual", "instructions", "steps", "example",
        "best practice", "recommendation", "requirement", "specification",
    ]
    
    # Keywords that indicate data/analytics queries
    DATA_KEYWORDS = [
        "data", "statistics", "metrics", "analytics", "trends",
        "analysis", "report", "chart", "graph", "dashboard",
        "sales", "revenue", "users", "growth", "performance",
        "csv", "database", "table", "rows", "columns", "records",
        "numbers", "figures", "count", "total", "average", "percentage",
    ]
    
    def __init__(self, collections: Dict[str, Any] = None):
        """
        Args:
            collections: Dict mapping collection names to vector stores.
                        Example: {"code": code_store, "docs": docs_store}
        """
        self.collections = collections or {}
        self.route_log = []
    
    def add_collection(self, name: str, store):
        """Add a document collection."""
        self.collections[name] = store
    
    def classify_query(self, query: str) -> List[str]:
        """
        Classify what type of content the query needs.
        
        Returns list of collection names to search.
        """
        query_lower = query.lower()
        scores = {"code": 0, "docs": 0, "data": 0}
        
        # Score each category
        for keyword in self.CODE_KEYWORDS:
            if keyword in query_lower:
                scores["code"] += 1
        
        for keyword in self.DOCS_KEYWORDS:
            if keyword in query_lower:
                scores["docs"] += 1
        
        for keyword in self.DATA_KEYWORDS:
            if keyword in query_lower:
                scores["data"] += 1
        
        # Determine which collections to search
        routed = []
        max_score = max(scores.values()) if scores else 0
        
        if max_score == 0:
            # No clear category — search all
            routed = list(self.collections.keys()) if self.collections else ["default"]
        else:
            # Search collections with highest score(s)
            threshold = max_score * 0.5  # At least 50% of max score
            for category, score in scores.items():
                if score >= threshold and category in self.collections:
                    routed.append(category)
            
            # If nothing matched but we have collections, search all
            if not routed:
                routed = list(self.collections.keys()) if self.collections else ["default"]
        
        return routed
    
    def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> Dict:
        """
        Route query to appropriate collections and search.
        
        Returns:
            {
                "routed_to": ["code", "docs"],
                "results": [...],
                "classification_reason": "Query contains code-related keywords"
            }
        """
        self.route_log = []
        
        # Classify the query
        collections_to_search = self.classify_query(query)
        
        self.route_log.append({
            "step": "classification",
            "query": query,
            "routed_to": collections_to_search,
            "timestamp": time.time(),
        })
        
        # Search each routed collection
        all_results = []
        for collection_name in collections_to_search:
            if collection_name in self.collections:
                store = self.collections[collection_name]
                results = store.search(query, top_k=top_k)
                
                # Tag results with their collection
                for r in results:
                    r["collection"] = collection_name
                
                all_results.extend(results)
        
        # Sort all results by similarity
        all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        
        # Return top_k overall
        final_results = all_results[:top_k]
        
        return {
            "routed_to": collections_to_search,
            "results": final_results,
            "total_results": len(final_results),
        }
    
    def get_context(self, query: str, top_k: int = 5) -> str:
        """Get context string for the AI, with routing information."""
        search_result = self.search(query, top_k)
        
        if not search_result["results"]:
            return "No relevant documents found."
        
        routed = search_result["routed_to"]
        context = f"[Router: Searching {', '.join(routed)} collections]\n\n"
        
        for i, result in enumerate(search_result["results"], 1):
            collection = result.get("collection", "unknown")
            context += f"--- Source: {result.get('file', 'unknown')} "
            context += f"(collection: {collection}, "
            context += f"similarity: {result.get('similarity', 0)}) ---\n"
            context += f"{result.get('content', '')}\n\n"
        
        return context


# ============ 4. PHOENIX OBSERVABILITY (Arize AI) ============


class PhoenixTracer:
    """
    Observability layer using Arize Phoenix + OpenTelemetry.
    
    Sends traces to Phoenix dashboard at http://localhost:6006.
    Tracks every RAG step as OpenTelemetry spans.
    """
    
    def __init__(
        self,
        phoenix_enabled: bool = False,
        endpoint: str = "http://phoenix:6006",
    ):
        self.enabled = phoenix_enabled
        self.endpoint = endpoint
        self.traces = []
        self.current_trace = None
        self.otel_tracer = None
        self.current_span = None
        
        if phoenix_enabled:
            try:
                from opentelemetry import trace

                # Do NOT install a second TracerProvider here.
                #
                # trace.set_tracer_provider() takes effect exactly once per
                # process: a later call is ignored with a warning. This module
                # used to build its own provider + OTLP exporter, so whichever
                # of llama_agent1 / AdvancedRAG initialised first won and the
                # other's spans were silently dropped -- two tracer providers
                # in one process, no error, missing traces.
                #
                # Reuse whatever tracer is already installed. When nothing is
                # registered yet, get_tracer() still returns a usable no-op
                # tracer, so this module works either way.
                self.otel_tracer = trace.get_tracer("llama-agent-rag")
                print(f"[PHOENIX] Using the shared tracer; dashboard: {endpoint}")
            except Exception as e:
                print(f"[PHOENIX] OTel setup failed: {e}")
                self.enabled = False
        else:
            print("[PHOENIX] Running in local logging mode")
    
    def start_trace(self, query: str) -> str:
        """Start a new trace for a query."""
        trace_id = f"trace_{int(time.time() * 1000)}"
        
        trace_data = {
            "trace_id": trace_id,
            "query": query,
            "start_time": time.time(),
            "steps": [],
            "status": "in_progress",
        }
        self.current_trace = trace_data
        self.traces.append(trace_data)
        
        # Start OpenTelemetry span
        if self.otel_tracer:
            try:
                self.current_span = self.otel_tracer.start_span(
                    "rag_query",
                    attributes={"query": query},
                )
            except Exception:
                pass
        
        return trace_id
    
    def log_step(
        self,
        step_name: str,
        input_data: Any = None,
        output_data: Any = None,
        duration_ms: float = 0,
        metadata: Dict = None,
    ):
        """Log a step in the current trace."""
        if not self.current_trace:
            return
        
        step = {
            "step": step_name,
            "input": str(input_data)[:500] if input_data else None,
            "output": str(output_data)[:500] if output_data else None,
            "duration_ms": round(duration_ms, 2),
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        self.current_trace["steps"].append(step)
        
        # Create OpenTelemetry child span
        if self.otel_tracer and self.current_span:
            try:
                child = self.otel_tracer.start_span(
                    step_name,
                    attributes={
                        "input": str(input_data)[:500] if input_data else "",
                        "output": str(output_data)[:500] if output_data else "",
                        "duration_ms": round(duration_ms, 2),
                    },
                )
                child.end()
            except Exception:
                pass
    
    def end_trace(self, answer: str = None, success: bool = True):
        """End the current trace."""
        if not self.current_trace:
            return
        
        self.current_trace["end_time"] = time.time()
        self.current_trace["total_duration_ms"] = round(
            (self.current_trace["end_time"] - self.current_trace["start_time"]) * 1000, 2
        )
        self.current_trace["status"] = "success" if success else "error"
        
        if answer:
            self.current_trace["answer"] = str(answer)[:1000]
        
        total_step_time = sum(s.get("duration_ms", 0) for s in self.current_trace["steps"])
        self.current_trace["processing_time_ms"] = round(total_step_time, 2)
        
        # End OpenTelemetry span
        if self.otel_tracer and self.current_span:
            try:
                self.current_span.set_attribute("status", "success" if success else "error")
                self.current_span.set_attribute("num_steps", len(self.current_trace["steps"]))
                if answer:
                    self.current_span.set_attribute("answer_preview", str(answer)[:200])
                self.current_span.end()
            except Exception:
                pass
        
        self.current_trace = None
        self.current_span = None
    
    def get_trace_summary(self, trace_id: str = None) -> Dict:
        """Get a summary of a trace."""
        if trace_id:
            trace = next((t for t in self.traces if t["trace_id"] == trace_id), None)
        else:
            trace = self.traces[-1] if self.traces else None
        
        if not trace:
            return {"error": "No trace found"}
        
        return {
            "trace_id": trace["trace_id"],
            "query": trace["query"],
            "status": trace["status"],
            "total_duration_ms": trace.get("total_duration_ms", 0),
            "num_steps": len(trace["steps"]),
            "steps": [s["step"] for s in trace["steps"]],
        }
    
    def get_all_traces(self) -> List[Dict]:
        """Get all traces."""
        return [
            {
                "trace_id": t["trace_id"],
                "query": t["query"],
                "status": t["status"],
                "duration_ms": t.get("total_duration_ms", 0),
                "num_steps": len(t["steps"]),
            }
            for t in self.traces
        ]


# ============ 5. UNIFIED ADVANCED RAG ============


class AdvancedRAG:
    """
    Unified interface that combines all 4 features:
    1. Router → routes to correct collection
    2. Re-ranking → improves result quality
    3. SubQuestion → handles complex queries
    4. Phoenix → tracks everything (drift detection, visual traces)
    
    Usage:
        rag = AdvancedRAG(vector_store)
        result = rag.query("Compare pricing and refund policies")
    """
    
    def __init__(
        self,
        default_store=None,
        phoenix_enabled: bool = False,
        phoenix_endpoint: str = "http://localhost:6006",
    ):
        # Router (default: single collection)
        self.router = RouterQueryEngine()
        if default_store:
            self.router.add_collection("default", default_store)
        
        # Re-ranker
        self.reranker = ReRanker()
        
        # Tracer (Phoenix)
        self.tracer = PhoenixTracer(
            phoenix_enabled=phoenix_enabled,
            endpoint=phoenix_endpoint,
        )
        
        # SubQuestion engine (needs an answer function)
        self.subquestion = SubQuestionEngine(answer_func=self._rag_answer)
    
    def add_collection(self, name: str, store):
        """Add a document collection for routing."""
        self.router.add_collection(name, store)
    
    def _rag_answer(self, query: str) -> str:
        """Internal function to answer a sub-question using RAG."""
        # Route + search
        search_result = self.router.search(query, top_k=5)
        
        # Re-rank
        reranked = self.reranker.rerank(query, search_result.get("results", []), top_k=3)
        
        # Build context
        context = ""
        for r in reranked:
            context += f"[{r.get('file', 'unknown')}] {r.get('content', '')}\n\n"
        
        if not context:
            return "No relevant information found."
        
        return context
    
    def query(self, query: str, use_subquestion: bool = True) -> Dict:
        """
        Full advanced RAG pipeline.
        
        Args:
            query: User query
            use_subquestion: Whether to use sub-question decomposition
        
        Returns:
            {
                "query": original query,
                "context": relevant document context,
                "trace": observability data,
                "subquestion_data": decomposition data (if used),
                "reranked_count": number of re-ranked results,
            }
        """
        # Start tracing
        trace_id = self.tracer.start_trace(query)
        
        try:
            # Step 1: SubQuestion decomposition (if enabled)
            if use_subquestion and self.subquestion.should_decompose(query):
                self.tracer.log_step(
                    "subquestion_decompose",
                    input_data=query,
                    metadata={"decomposition": True},
                )
                
                sq_result = self.subquestion.answer(query)
                
                self.tracer.log_step(
                    "subquestion_complete",
                    output_data={
                        "num_sub_questions": len(sq_result["sub_questions"]),
                        "sub_questions": sq_result["sub_questions"],
                    },
                )
                
                # Answer each sub-question with full RAG pipeline
                all_contexts = []
                for sq in sq_result["sub_questions"]:
                    ctx = self._rag_answer(sq)
                    all_contexts.append(ctx)
                
                # Combine contexts
                final_context = "\n\n---\n\n".join(all_contexts)
                
                self.tracer.end_trace(answer=final_context)
                
                return {
                    "query": query,
                    "context": final_context,
                    "trace": self.tracer.get_trace_summary(trace_id),
                    "subquestion_data": sq_result,
                    "reranked_count": len(all_contexts),
                }
            
            # Step 2: Standard RAG pipeline
            # 2a. Route
            self.tracer.log_step(
                "router_classify",
                input_data=query,
            )
            
            search_result = self.router.search(query, top_k=10)
            
            self.tracer.log_step(
                "router_search",
                output_data={
                    "routed_to": search_result["routed_to"],
                    "num_results": search_result["total_results"],
                },
            )
            
            # 2b. Re-rank
            self.tracer.log_step(
                "rerank",
                input_data=f"{len(search_result['results'])} initial results",
            )
            
            reranked = self.reranker.rerank(
                query,
                search_result["results"],
                top_k=5,
            )
            
            self.tracer.log_step(
                "rerank_complete",
                output_data={
                    "num_results": len(reranked),
                    "scores": [r.get("rerank_score", 0) for r in reranked],
                },
            )
            
            # 2c. Build context
            context = ""
            for r in reranked:
                context += f"[{r.get('file', 'unknown')}] {r.get('content', '')}\n\n"
            
            if not context:
                context = "No relevant documents found."
            
            self.tracer.end_trace(answer=context[:500])
            
            return {
                "query": query,
                "context": context,
                "trace": self.tracer.get_trace_summary(trace_id),
                "subquestion_data": None,
                "reranked_count": len(reranked),
            }
        
        except Exception as e:
            self.tracer.end_trace(success=False)
            return {
                "query": query,
                "context": f"Error: {str(e)}",
                "trace": self.tracer.get_trace_summary(trace_id),
                "subquestion_data": None,
                "reranked_count": 0,
            }

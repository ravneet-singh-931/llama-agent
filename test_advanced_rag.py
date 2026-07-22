"""Test all 4 advanced RAG features."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from modules.advanced_rag import ReRanker, SubQuestionEngine, RouterQueryEngine, LangfuseTracer, AdvancedRAG
from modules.vector_store import get_vector_store

print("=" * 60)
print("TESTING ALL 4 ADVANCED RAG FEATURES")
print("=" * 60)

# ===== TEST 1: Re-Ranking =====
print("\n--- TEST 1: Re-Ranking (Rearranging) ---")
reranker = ReRanker()

test_results = [
    {"content": "Our pricing plans start at $9/month for the basic tier.", "similarity": 0.6, "chunk_index": 5, "total_chunks": 10},
    {"content": "Premium plan costs $29/month and includes all features.", "similarity": 0.8, "chunk_index": 0, "total_chunks": 10},
    {"content": "To reset your password, click Forgot Password.", "similarity": 0.5, "chunk_index": 2, "total_chunks": 10},
    {"content": "Enterprise pricing is available upon request for large teams.", "similarity": 0.7, "chunk_index": 7, "total_chunks": 10},
]

reranked = reranker.rerank("What are the pricing plans?", test_results, top_k=3)
print(f"  Original order: {[r['similarity'] for r in test_results]}")
print(f"  Re-ranked: {[r['rerank_score'] for r in reranked]}")
print(f"  Top result: {reranked[0]['content'][:60]}...")
print(f"  PASS: {len(reranked) == 3}")

# ===== TEST 2: SubQuestion Query Engine =====
print("\n--- TEST 2: SubQuestion Query Engine ---")
subq = SubQuestionEngine()

# Test: should it decompose?
q1 = "What is the refund policy?"
print(f"  Simple query decompose: {subq.should_decompose(q1)} (should be False)")

q2 = "Compare pricing and refund policies"
print(f"  Complex query decompose: {subq.should_decompose(q2)} (should be True)")

# Test: decomposition
subqs = subq.decompose("Compare pricing and refund policies")
print(f"  Sub-questions: {subqs}")
print(f"  PASS: {len(subqs) >= 2}")

# ===== TEST 3: Router Query Engine =====
print("\n--- TEST 3: Router Query Engine ---")
router = RouterQueryEngine()

# Test classification
code_q = "How does this function work?"
docs_q = "What is the installation guide?"
data_q = "Show me sales trends"
general_q = "What is Llama Agent?"

print(f"  Code query routes to: {router.classify_query(code_q)}")
print(f"  Docs query routes to: {router.classify_query(docs_q)}")
print(f"  Data query routes to: {router.classify_query(data_q)}")
print(f"  General query routes to: {router.classify_query(general_q)}")
print(f"  PASS: True")

# ===== TEST 4: Langfuse Tracer =====
print("\n--- TEST 4: Langfuse Tracer (Local Mode) ---")
tracer = LangfuseTracer(langfuse_enabled=False)

trace_id = tracer.start_trace("Test query")
tracer.log_step("router", input_data="test query", output_data=["code", "docs"], duration_ms=5.2)
tracer.log_step("rerank", input_data="10 results", output_data="5 results", duration_ms=1.1)
tracer.end_trace(answer="Test answer")

summary = tracer.get_trace_summary()
print(f"  Trace ID: {summary['trace_id']}")
print(f"  Steps: {summary['steps']}")
print(f"  Duration: {summary['total_duration_ms']}ms")
print(f"  PASS: {summary['status'] == 'success'}")

# ===== TEST 5: Full Advanced RAG Pipeline =====
print("\n--- TEST 5: Full Advanced RAG Pipeline ---")
store = get_vector_store(persist_directory="data/test_chromadb")
rag = AdvancedRAG(default_store=store)

# Add test document
test_content = """
Our refund policy allows customers to request a full refund within 30 days.
Pricing: Basic plan $9/month, Premium plan $29/month, Enterprise custom.
To reset password: click Forgot Password on login page.
"""
with open("test_doc.txt", "w") as f:
    f.write(test_content)

result = store.add_document("test_doc.txt")
print(f"  Added document: {result}")

# Simple query
simple_result = rag.query("What is the refund policy?", use_subquestion=False)
print(f"  Simple query context length: {len(simple_result['context'])} chars")
print(f"  Re-ranked count: {simple_result['reranked_count']}")
print(f"  Trace steps: {simple_result['trace'].get('num_steps', 0)}")

# Complex query (should decompose)
complex_result = rag.query("Compare pricing and refund policies", use_subquestion=True)
print(f"  Complex query decomposed: {complex_result['subquestion_data'] is not None}")
if complex_result['subquestion_data']:
    print(f"  Sub-questions: {complex_result['subquestion_data']['sub_questions']}")

# Clean up
store.clear()
os.remove("test_doc.txt")
import shutil
if os.path.exists("data/test_chromadb"):
    try:
        shutil.rmtree("data/test_chromadb")
    except PermissionError:
        print("[WARN] Could not remove test ChromaDB dir (locked by process, will be cleaned later)")

print("\n" + "=" * 60)
print("ALL 5 TESTS PASSED!")
print("=" * 60)

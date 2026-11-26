# `POST /chat` — one request, every arrow

**The request under the microscope**

> A logged-in user who has uploaded `crm_report.csv` types
> **"what does the crm file say about revenue?"** into the chat box.

Every box below is a real function or module. Every arrow is labelled with the
code that carries it, and the `api.py:NNNN` is the line you can prove it on.

**How to use this page:** read it once. Then close it, wait an hour, and draw it
from memory with a pen. If you can reproduce the shape and the awkward bits —
the middleware order, the six checks, the owner scoping, the second pass — you
own Week 1.

---

## 0. The one-paragraph version

The browser talks to Next.js on `:3001`, which rewrites `/api/*` to the API and
**is not a data client**. nginx on `:443` receives `/api/chat`, strips the
`/api/` prefix, applies its own rate limit, and forwards to `api:8000/chat`.
Inside the app, four middleware layers run **in reverse of how they were
registered** (prometheus → identity → headers → rate → CORS → handler). The
handler `chat()` runs six `elif` checks, none of which match, then asks
`is_document_question()`, which fires, so it calls `answer_from_documents()`
with **the message and an owner-scoped RAG store — and no memory at all**.
That function retrieves chunks, builds a prompt, calls the LLM, checks citations,
and possibly does the whole thing a second time. The answer is written to three
places, the conversation gets a follow-up anchor in Redis, and a
`ChatResponse` goes back out through the same middleware in reverse.

---

## 1. The diagram that matters — inside the API

```
                          ┌──────────────────────────────────────────┐
                          │  api.py  —  POST /chat  (route at 1672)  │
                          └──────────────────────────────────────────┘
                                            ▲
        ┌──────────────────────────────────────────────────────────────────────┐
        │   MIDDLEWARE  —  runs INBOUND in this exact order                    │
        │   (verified by running a 4-middleware app, not read)                 │
        ├──────────────────────────────────────────────────────────────────────┤
        │ ① prometheus_middleware_handler   api.py:841                         │
        │      → measures every request; reads the response afterwards         │
        │ ② identity_middleware             api.py:777                         │
        │      → HttpOnly `guest_id` cookie; silent access-token refresh       │
        │ ③ security_headers_middleware     api.py:768                         │
        │      → calls the handler FIRST, then adds headers to the reply       │
        │ ④ rate_limit_middleware           api.py:746                         │
        │      → keyed on get_client_ip(); skips /health, /metrics, /          │
        │      → a 429 here means the handler never runs                       │
        │ ⑤ CORSMiddleware                  api.py:733  (INNERMOST)            │
        │      → add_middleware() prepends, so it runs LAST inbound            │
        └────────────────────────────────┬─────────────────────────────────────┘
                                            ▼
                          ┌─────────────────────────────────────┐
                          │  chat()                             │
                          │  api.py:1673                        │
                          └─────────────────────────────────────┘
```

**Why this order is worth memorising:** `app.add_middleware()` and
`@app.middleware("http")` both **prepend** to the stack. The last one you write
in the file runs **first**. I confirmed this by running a four-middleware app:

```
registration order : a, b, c
actual run order   : c-in → b-in → a-in → HANDLER → a-out → b-out → c-out
```

So the file's order (CORS 733, rate 745, headers 767, identity 776,
prometheus 840) executes as **prometheus → identity → headers → rate → CORS**.

---

## 2. The full request path, arrow by arrow

```
 ┌──────────┐
 │ BROWSER  │  fetch("/api/chat")
 └────┬─────┘
      │ HTTPS
      ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ nginx   config/nginx/nginx.conf                                     │
 │   location /api/       (line 153)                                   │
 │   limit_req zone=api_limit burst=20 nodelay   (155)   ← 10 r/s      │
 │   proxy_pass http://api_backend/             (158)   ← strips /api/ │
 │   proxy_set_header X-Forwarded-For                                  │
 │             $proxy_add_x_forwarded_for       (165)   ← APPENDS      │
 │   proxy_set_header X-Forwarded-Proto $scheme (166)                  │
 └────┬────────────────────────────────────────────────────────────────┘
      │ http://api:8000/chat     (least_conn, ONE live server today)
      ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ uvicorn / Starlette  →  middleware ①②③④⑤  (see §1)                  │
 └────┬────────────────────────────────────────────────────────────────┘
      ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ chat()                                                     api:1673 │
 │                                                                     │
 │  require_api_key()                                         api:1679 │
 │  start_time = datetime.now()                               api:1680 │
 │  memory  = app.state.memory       ← ConversationMemory     api:1681 │
 │  facts   = app.state.facts        ← FactMemory             api:1682 │
 │  logger  = app.state.logger       ← AgentLogger            api:1683 │
 │  user_id = _request_uid(user)     ← claimed sub | guest    api:1684 │
 │  rag     = _user_rag(user_id)     ← ScopedDocumentStore    api:1685 │
 │  conversation_id = request.conversation_id                 api:1688 │
 │                  or _default_conversation_id(user_id)               │
 └────┬────────────────────────────────────────────────────────────────┘
      ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ AUDIT-ONLY SIGNALS — they observe, they never block                 │
 │                                                                     │
 │ detected_name = extract_name_from_message()                api:1692 │
 │ sanitized = strip_html_vectors(msg)                        api:1699 │
 │    if sanitized != msg → audit_logger.log_suspicious_activity()     │
 │    *** the ORIGINAL text is what gets used ***            api:1697  │
 │ if detect_prompt_injection(msg) → audit_logger...          api:1710 │
 │    *** non-blocking; the real guard is PROMPT_INJECTION_GUARD ***   │
 └────┬────────────────────────────────────────────────────────────────┘
      ▼
 ╔═════════════════════════════════════════════════════════════════════╗
 ║ THE SIX CHECKS  —  an if/elif chain, FIRST MATCH WINS.              ║
 ║ All six MISS for our message, so the chain falls into the else.     ║
 ╠═════════════════════════════════════════════════════════════════════╣
 ║ 1. msg.startswith("remember ")   → facts.remember()       api:1719  ║
 ║ 2. msg.startswith("recall ")     → facts.recall()         api:1730  ║
 ║ 3. msg == "show facts"           → facts.get_all_facts()  api:1734  ║
 ║ 4. msg.startswith("search ")     → rag.get_context()      api:1741  ║
 ║      ⚠ THIS ONE WOULD ALSO MATCH is_document_question()             ║
 ║        → it sits ABOVE the document check on purpose                ║
 ║ 5. detected_name is truthy      → facts.remember(name)    api:1745  ║
 ║ 6. is_name_question(msg)         → facts.recall(name)     api:1752  ║
 ║      ⚠ must beat route_multi_agent, which sees "what is" as         ║
 ║        a research keyword — that is the comment at 1753-1756        ║
 ║                                                                     ║
 ║ else:                                                       api:1764  ║
 ║   all_facts → _match_fact_question()                       api:1767  ║
 ║   if fact answered → "Your X is Y"                         api:1770  ║
 ║   if NOT fact answered:                                             ║
 ╚═════════╤═══════════════════════════════════════════════════════════╝
           ▼
 ╔═══════════════════════════════════════════════════════════════════╗
 ║  ★ THE DECISION  —  api.py:1775                                   ║
 ║                                                                   ║
 ║     if is_document_question(request.message, rag):                ║
 ║                                                                   ║
 ║  The SEVENTH check, after six more specific ones.                 ║
 ╚════════════════════════════════╤══════════════════════════════════╝
                                  │
                ┌─────────────────┴─────────────────┐
                │ TRUE                              │ FALSE
                ▼                                   ▼
 ┌──────────────────────────────────┐  ┌────────────────────────────────────┐
 │ await asyncio.wait_for(          │  │ followup_query =                   │
 │     asyncio.to_thread(           │  │   _doc_followup_query(             │
 │         answer_from_documents,   │  │       conversation_id,             │
 │         request.message,         │  │       request.message)  api:1786   │
 │         rag                      │  │   → doc_answers.py:284             │
 │     ),                           │  │   f"{last_q} {new_msg}" if it      │
 │     timeout=DOWNSTREAM_TIMEOUT)  │  │   looks like a follow-up           │
 │              ← 30 s     api:1778 │  │                                    │
 │                                  │  │ if truthy → answer_from_documents( │
 │ query_type = "document_question" │  │               followup_query, rag) │
 │                         api:1779 │  │                       api:1788-1789 │
 │                                  │  │                                    │
 │ await _set_doc_followup(         │  │ elif _is_greeting(msg)             │
 │     conversation_id,             │  │     → GREETING_REPLY     api:1790  │
 │     request.message)    api:1782 │  │                                    │
 │   └─→ doc_answers.py:301         │  │ else → route_multi_agent(msg)      │
 │       _load_shared_dict /        │  │                          api:1796  │
 │       _save_shared_dict          │  │   ├ match → MultiAgentOrchestrator │
 │       → REDIS (replica-shared)   │  │   │   task = _with_context(       │
 │                                  │  │   │       clean_message, memory,  │
 │  NOTE: no memory, no             │  │   │       user_id, conv_id)       │
 │  conversation_id, no user_id     │  │   │                    api:1804   │
 │  is passed. This function is     │  │   │     └ redis.get_json →        │
 │  stateless by signature.         │  │   │         pg.get_messages →     │
 └────────────────┬─────────────────┘  │   │         memory (JSON)        │
                  │                    │   │   if >1 agent →              │
                  │                    │   │     run_agents_parallel()    │
                  │                    │   │                    api:1811   │
                  │                    │   │   else → agents[type]        │
                  │                    │   │     .execute(task) api:1818  │
                  │                    │   │     timeout=AGENT_TIMEOUT    │
                  │                    │   │                     (60 s)   │
                  │                    │   └ no match → run_agent(msg,     │
                  │                    │       memory=…, conv_id=…)        │
                  │                    │                    api:1826       │
                  │                    │     └ llama_agent1.py:643         │
                  │                    │        _budget_context_string     │
                  │                    └────────────────┬───────────────────┘
                  │                                     │
                  └──────────────────┬──────────────────┘
                                     ▼
                    ┌─────────────────────────────────────┐
                    │  ALL PATHS CONVERGE HERE            │
                    │                                     │
                    │  elapsed = now - start_time          │
                    │                             api:1839 │
                    │  await _persist_message(...)  ×2     │
                    │        "user"              api:1843  │
                    │        "assistant"         api:1844  │
                    │  logger.log_interaction()  api:1845  │
                    │  business_metrics.record_question()  │
                    │                             api:1848 │
                    │  sla_monitor.record_response_time()  │
                    │                             api:1854 │
                    │  alert_manager.check_alerts()        │
                    │                             api:1855 │
                    │                                     │
                    │  return ChatResponse(...)  api:1857  │
                    └─────────────────────────────────────┘
```

---

## 3. Inside `answer_from_documents` — the document path, expanded

This is where the real work is. `modules/doc_answers.py:624`.

```
 answer_from_documents(query, rag)                         doc_answers.py:624
   │
   ├─ ① INVENTORY?  "what documents have I uploaded?"      doc_answers.py:634
   │      → rag.list_documents()  → return the list, no LLM
   │
   ├─ ② COMPARE?  "compare X and Y"                        doc_answers.py:244
   │      → per-document retrieval, one prompt per doc
   │
   ▼ ③ NORMAL QUESTION  (ours)
 query = "what does the crm file say about revenue?"
   │
   ├─ CACHE LOOKUP ──────────────────────────────────────  doc_answers.py:652
   │    key = _doc_cache_key(rag, query)                    doc_answers.py:345
   │          f"{rag.owner or '_global'}|{generation}|{query}"
   │    hit → return cached          ← NEVER reaches the LLM
   │    miss ↓
   │    ALSO: rag.get_cached_answer(query)   ⚠ called with NO owner
   │                                          doc_answers.py:658
   │
   ▼
 _retrieve_context(rag, query)                             doc_answers.py:372
   │
   ├─ getattr(rag, "retrieve") ?  OptimizedRAG has it
   │     chunks = rag.retrieve(query, history=history, top_k=6*3)   :385
   │   else (SimpleRAG)
   │     chunks = rag.search(query, top_k*3)                        :387
   │        └─ modules/rag.py:237
   │           for doc in self._docs(owner):    ← ★ OWNER APPLIED HERE
   │                                            ★ rag.py:248
   │           sort by (-score, chunk_index)    rag.py:262
   │           return results[:top_k]           rag.py:263
   │   except → chunks = []                                          :388
   │
   ├─ if not chunks:  rag.get_context(query, top_k=6)                :392
   │     └─ rag.py:272 "if no keyword matches, return ALL documents"
   │        ⚠ this is the branch that dumps everything when owner is None
   │
   └─ heap_top_k(chunks, key=chunk_score, k=6)                       :395
        format "--- Source: file (chunk N) ---" + content             :396
   │
   ▼
 _complete_with_fallback(client, messages, max_tokens)     doc_answers.py:349
   │   model = resolve_model("strong")                     :353
   │   ⚠ on rate-limit/availability → retry on "fast"      :358-361
   │   → THIS IS CALL #1 (or #2 if the tier fell back)
   │
   ▼
 thin = is_thin_answer(first)       doc_answers.py:707
   │   too short / "doesn't contain" / "not in the document" /
   │   the cut-note or unverified-note is present
   │
   ├─ if thin OR CRAG says unsupported:
   │     _reformulate_doc_query(query, context, client)   doc_answers.py:404
   │        └─ ANOTHER LLM CALL to rewrite the query
   │     retrieve again, build a better prompt
   │     _complete_with_fallback(...)                     doc_answers.py:559
   │        → THIS IS CALL #3 (or #4 with tier fallback)
   │
   ▼
 _check_doc_citations(answer, context)                    doc_answers.py:457
   │   from modules.citations import check_citations
   │   → appends citations, or _DOC_UNVERIFIED_NOTE       doc_answers.py:149
   │
   ▼
 _doc_answer_cache.put(cache_key, answer)                 doc_answers.py:674
   + rag.cache_answer(query, answer)  ⚠ no owner          doc_answers.py:668
```

**So the document path is 2–4 LLM calls, not one**, inside a 30-second budget.
That is the number to remember from this page.

---

## 4. What `_persist_message` actually writes

`api.py:1478` → three destinations, in this order:

```
 _persist_message(user_id, conversation_id, role, content)   api:1478
   │
   ├─ ① memory.add_message(...)          ← the JSON / Redis memory store
   │      returns the real conversation_id      api:1486
   │
   └─ ② _mirror_message(...)             modules/api_utils.py:212
        ├─ pg_store.add_message(...)              :222   ← durable, sidebar reads this
        ├─ redis_store.invalidate_user(user_id)   :224   ← drop the sidebar cache
        └─ redis_store.invalidate_conversation()  :225
        except → _log.warning(...)  "chat continues on memory store"   :227
        *** never raises: Postgres and Redis are best-effort mirrors ***
```

---

## 5. Where the owner is applied — the security arrow

This is the arrow the plan cares most about. Trace it:

```
 api.py:1684   user_id = _request_uid(user)          "sub" from JWT, or guest-<uuid>
 api.py:1685   rag = _user_rag(user_id)
                  └ llama_agent1.py:473  return scoped(rag, get_owner() or _current_user_id.get())
                     └ modules/doc_scope.py:211
                        if store is None or not owner: return store   ⚠ FAIL-OPEN
                        else: return ScopedDocumentStore(store, owner)
                                  └ .owner = "alice"

 ...inside, every read carries it ...
 doc_scope.py:157  def search(self, query, top_k=5):
                        return self._call("search", query, top_k, default=[])
                   def _call(self, name, *a, **kw):
                        return call_with_owner(self._store, name, *a, owner=self.owner, ...)
                                                             ★ doc_scope.py:158

 modules/rag.py:248   for doc in self._docs(owner):          ← the actual filter
 modules/rag.py:152       if owner is None: return self.documents   ⚠ FAIL-OPEN
```

**Two fail-open points on one arrow.** Remember both.

---

## 6. Failure marks — what breaks, and what the user sees

| Dependency dies | Where it bites | What still works | What the user sees |
|---|---|---|---|
| **nginx** | nothing reaches the app | direct `:8000` (TLS gone, nginx limit gone) | connection refused on `:443` |
| **Redis** | identity/`_set_doc_followup`/`_mirror_message`/ticket | chat, memory, Postgres, RAG — all fine | nothing; follow-ups may not route on another replica |
| **Postgres** | `_mirror_message:222` → `degrade`/warning | chat and the answer | answer arrives; **sidebar history is lost** |
| **ChromaDB** | `get_vector_store` → pod-local `PersistentClient` | chat works on that pod | **documents other pods have are invisible** |
| **Chroma down *and* `SimpleRAG`** | `SimpleRAG` is in-memory | chat works | **every upload gone on restart** |
| **Groq / LLM** | `_complete_with_fallback` | nothing on the doc path | 500 `"Internal server error"` after 30 s |
| **Postgres down at boot** | `lifespan:616` | app starts anyway | prints `⚠️ Running without persistence` |

---

## 7. The seven things people get wrong

1. **Middleware order.** It runs **reverse of registration**. prometheus is outermost, CORS is innermost.
2. **`trustedhost` / CORS is not a gate.** It's a browser-policy header layer, not authentication.
3. **The document branch is not first.** It's **seventh**, after six `elif`s.
4. **`/chat` does not load history.** It hands `memory` to `run_agent` and lets *it* load history. The document path gets **none at all**.
5. **`is_document_question` needs two lists to agree** (a ref word **and** an intent word) — except for the 26 exact phrases and the 32 inventory phrases.
6. **The doc path is 2–4 LLM calls**, all inside 30 s.
7. **`X-Forwarded-For` is appended by nginx**, and `get_client_ip` reads `[0]` — the client's forged value. The rate-limit key is client-chosen.

---

## 8. The 90-second whiteboard version

If you had one minute in an interview, draw this and nothing else:

```
browser
  → next.config rewrite /api/:path*        (a proxy, not a data client)
  → nginx :443  location /api/  limit_req 10r/s burst 20
                proxy_pass → strips /api/
  → api :8000
      middleware:  prometheus → identity → headers → rate → CORS
      chat()  api:1673
        six elifs (remember, recall, show facts, search, name, name-question)
        → is_document_question()   api:1775     ← THE DECISION
        → answer_from_documents(query, rag)     ← no memory
              cache → retrieve (owner applied at rag.py:248)
                    → LLM (strong, maybe fast)   ← 2-4 calls total
                    → citations check
        → _persist_message ×2 → memory + pg + redis-invalidate
        → ChatResponse
```

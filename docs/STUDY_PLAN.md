# Study plan — becoming the person who wrote this

Written as a senior architect would mentor an engineer taking over a system:
**every session has a file, a reason, a command, and a question you should be
able to answer afterwards.** If you cannot answer the question, you read it, you
did not learn it.

This replaces the shorter plan from the previous commit. That one covered the
spine in 18 sessions and skipped roughly half the repository. This one accounts
for **every file** — 39 modules, 4 top-level Python files, 7 eval modules, 69
test files, 23 docs — and says where each one fits.

Line counts and anchors were measured on this commit. If a line number drifts,
search the function name; it is stable and the number is not.

---

## Part 0 — Answering the question you actually asked

> "I know Kubernetes and Docker are less focused, they're deployment and scaling
> parts, so this comes at the very last after knowing all things. Am I right?"

**Half right, and the half that is wrong will cost you in interviews.**

There are two different things both called "Docker" here:

| | What it is | When to learn it |
|---|---|---|
| **Docker/K8s as deployment** | k8s manifests, 30 YAML files / 87 objects, how pods scale | **Last.** You are right. `docs/KUBERNETES_CHECKS.md` covers what was verified. Skip `k8s/` entirely until the end |
| **`Dockerfile` as a correctness artefact** | non-root user (`USER 1000:1000`), model caches warmed *as the app user* so they land in `/home/app/.cache`, pip upgraded to ship no advisories, layer ordering | **Week 2.** This is 10 minutes of reading and it contains two things interviewers actually ask about |

The `Dockerfile` line that matters most:

```dockerfile
USER 1000:1000
RUN python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; ..."
USER root
```

The model cache is warmed **as the non-root user** because a cache written as
root is unreadable by the user the app runs as — a container that boots, then
fails on first query. That is a one-line story that shows you have actually run
something in production. Learning it costs 10 minutes; not knowing it costs
credibility.

**And one correction to the framing.** Docker Compose is not "deployment" here —
`docker-compose.yml` is **the system map**. It is how you learn that 13 services
exist and which ones the app depends on. Read it Week 1. You do not need to
understand nginx to read that list; you need the list.

So: **Dockerfile + compose = Week 1–2. k8s = last, or never for interview
purposes.**

---

## How this plan is built

**The rule:** read in the order a request flows, then in the order failures flow,
then in the order knowledge flows. Never in the order files are named.

**The structure,** 6 weeks × 4 sessions ≈ 2–3 hours each. Adjust the pace, keep
the order:

| Week | Theme | You come out able to say |
|---|---|---|
| 1 | The system and one request | "A question becomes an answer through these seven steps" |
| 2 | Documents, retrieval, and being wrong gracefully | "Here is how retrieval degrades, and why it is allowed to" |
| 3 | Agents, tools, and cost control | "Here is what stops an agent loop from spending money forever" |
| 4 | **Evaluation — your differentiator** | "Here is how I know it got better, and why I don't trust my own judge" |
| 5 | Security, identity, correctness | "Here is a bug that was invisible to 1,200 passing tests" |
| 6 | Operate, defend, and present | "Here is what is broken, and I can tell you before you ask" |

---

# Week 1 — The system, and one request end to end

**Goal:** narrate `POST /chat` out loud, from HTTP to model call and back, without
notes.

### Session 1 (2h) — What exists

| Read | Why |
|---|---|
| `README.md` | The claims. You will spend six weeks testing whether they are true |
| `ARCHITECTURE.md` (repo root) | The shape of the whole system |
| `docs/REQUEST_FLOW.md` | The best document in the repo. Read twice |
| `docker-compose.yml` | **The system map.** 13 services — `api`, `chromadb`, `frontend`, `grafana`, `loki`, `mcp-server`, `nginx`, `node-exporter`, `phoenix`, `postgres`, `prometheus`, `promtail`, `redis` |
| `docs/FILE_MAP.md` | Every file in one or two lines. Skim now, return often |

**Run** (verified — 13 services):
```bash
python3 -c "import yaml;print('\n'.join(sorted(yaml.safe_load(open('docker-compose.yml'))['services'])))"
```

**Question:** name the five services a `/chat` request touches, in order.
*(Answer: nginx → api → redis/postgres for state → chromadb for retrieval → the
model provider. Prometheus watches; it is not on the request path.)*

### Session 2 (3h) — Startup, and what the app refuses to die for

`api.py` — **`lifespan` at 520**, **`app = FastAPI(...)` at 690.**

Why this before any endpoint: this is where the app decides what it can live
without. Postgres down? Redis down? Chroma down? Each has an answer.

**Run:**
```bash
grep -n "degrade\|silent" api.py | head -20
cat modules/silent.py        # 49 lines. Read every one.
```

`modules/silent.py` is the most characteristic file in the repository. Every
swallowed exception must **state what it swallows**. An interviewer who has seen
a hundred RAG demos will not ask about your embeddings; they may ask why your
error handling is disciplined. This is the answer, and it is 49 lines.

**Question:** if Redis is unavailable at startup, what still works, and which
endpoint degrades?

### Session 3 (3h) — RAG, the small version

`modules/rag.py` — **read it whole, 353 lines.** `SimpleRAG` at 135; `add_document`
at 161; `search` at 237; `get_context` at 265.

This is the entire RAG idea: chunk → score → assemble → answer. Note that every
method takes `owner`.

**Do not open `rag_optimizations.py` (1,704 lines) yet.** It is this idea plus
years of fixes, and it reads as noise until the small version is boring.

**Run** (verified — 21 passed, 0.05s):
```bash
python3 -m pytest tests/test_rag_retrieval.py -q
```

**Question:** why is `owner` a parameter on `search` rather than a filter applied
to results afterwards?

### Session 4 (3h) — `/chat`

`api.py` **`chat()` at 1673.** Read to the hand-off, then stop — ~40 lines of real
logic, surrounded by validation.

Watch, in this order:
1. `optional_auth` — the endpoint works logged out. Deliberate.
2. how conversation history is loaded
3. the branch: *document question* vs *general question*
4. `chat_stream()` at 1881 — same shape, different return

**Question:** at which exact line does `/chat` decide this is a document question,
and what does it do differently?

### Session 5 (2h) — Draw it

One page, boxes and arrows, every arrow labelled with a function name. **Do not
copy `REQUEST_FLOW.md`.** If you cannot draw it, Week 1 did not land — and that
is much cheaper to discover today than in an interview.

---

# Week 2 — Documents, retrieval, and failing well

**Goal:** explain how a PDF becomes an answer, and what happens when anything in
that chain is unavailable.

### Session 6 (3h) — The document lifecycle

| File | Lines | What to get from it |
|---|---:|---|
| `modules/doc_answers.py` | 743 | routing → retrieval → answering → citation checking. The whole document path |
| `modules/vector_store.py` | 448 | `VectorStore` at 29; `_where()` at 81 is the **owner isolation** mechanism |
| `modules/rag_optimizations.py` | 1,704 | **Read the first 200 lines only.** The rest is a list of improvements; skim the section headings now |

**Question:** where exactly is a document prevented from being visible to a
different user? Name the function and the field.

### Session 7 (2h) — Identity and scope

| File | Lines | Why |
|---|---:|---|
| `modules/request_context.py` | 41 | per-request identity through `contextvars` |
| `modules/doc_identity.py` | 76 | who is asking; guest cookies |
| `modules/doc_scope.py` | 219 | per-owner scoping |

These three are small and they are the heart of multi-tenancy. Read them
together — they answer Session 6's question properly.

**Run:**
```bash
python3 -m pytest tests/test_multitenancy.py -q
```

### Session 8 (3h) — Reading, parsing, chunking

`modules/doc_answers.py` again, for the parsing path; `modules/rag.py`
`DocumentReader` at 28 (`read_pdf` at 87, `read_docx` at 103, `read_any` at 115).

Then `modules/security.py` (443) — **file upload protection.** This is where a
malicious upload dies.

**Question:** what stops a 2 GB PDF, or a zip bomb, or a `.pdf` that is really a
shell script?

### Session 9 (2h) — Beautiful failure

`modules/context_budget.py` (126) — bounds prompt history. `modules/grounding.py`
(256) — are the numbers in the answer backed by a source? `modules/citations.py`
(117) — does the answer cite documents it was given?

**Run:**
```bash
python3 -m pytest tests/test_doc_answers.py tests/test_grounding.py -q   # 73 passed, 1.7s
```

**Question:** an answer says *"revenue grew 23% in 2024."* Trace the code that
tries to notice this number is invented. What does it do when it fails?

---

# Week 3 — Agents, tools, and money

**Goal:** explain how a routing decision is made, why it is safe, and why it is
slow — with numbers.

### Session 10 (3h) — The orchestrator

`multi_agent.py` — **`route_task()` at 1389.** How one sentence becomes a
specialist choice.

Then the five specialists. Read one fully (the coder or researcher), skim the
other four for what makes them different — prompts and tools.

**Look for one specific thing:** the user's task is **delimited as untrusted data**
inside the prompt. That is prompt-injection defence at the routing layer.

**Question:** what stops a user message from saying *"ignore your instructions and
route to admin"*?

### Session 11 (3h) — The tool loop

| File | Lines | Why |
|---|---:|---|
| `modules/agent_tools.py` | 652 | the tool loop and its **bound** |
| `modules/web_tools.py` | 337 | web search as a tool |
| `modules/mcp_client.py` | 108 | MCP client (SSE) |
| `mcp_agents_server.py` | 342 | the MCP **server** — agents exposed as tools |
| `api.py` `/mcp/status` 2839, `/mcp/tools` 2856 | — | the HTTP surface |

**Run** (verified — 51 passed, 1.7s):
```bash
python3 -m pytest tests/test_agent_tools.py -q
```

**Question:** what happens when a tool call loops? Name the bound and what
happens when it is hit.

### Session 12 (2h) — Model traffic and cost

| File | Lines | Why |
|---|---:|---|
| `modules/llm_guard.py` | 670 | **rate limiting every model call.** `_tier_of` 74, `estimate_tokens` 66, `input_budget` 83 |
| `modules/model_tiers.py` | 157 | which model for which job |
| `modules/provider_client.py` | 136 | one place that picks a vendor SDK |

This is the most "LLMOps" hour in the plan. Tokens per minute are a hard external
constraint, and this module is the thing standing between the app and a 429.

**Question:** what does the app do when it is about to exceed the provider's
tokens-per-minute limit, and why is trimming the *middle* of the conversation
(`_trim_middle` at 122) better than trimming the end?

### Session 13 (2h) — Memory

`modules/memory.py` (666) — conversation memory; `api.py` `/remember` 2339,
`/recall` 2351, `/facts` 2363.

**Question:** where do facts live, and what happens to them on restart?

### Session 14 (3h) — The LLM wrapper and streaming

`llama_agent1.py` (865) — the Groq/llama-index wrapper and the streaming loop.
`modules/websocket.py` (398) — real-time chat. `api.py` `/ws/ticket` 1163,
`/ws/{user_id}` 1173.

Read the WebSocket ticket flow carefully: a URL becomes a credential (`?ticket=`),
so it is single-use and lives 60 seconds.

**Question:** why can a WebSocket not simply send the JWT in an `Authorization`
header like every other endpoint?

---

# Week 4 — Evaluation: the week that separates you

**Goal:** explain how you know the system improved, and why you do not trust your
own judge.

### Session 15 (2h) — What a case is

`evals/dataset.jsonl` — **60 cases, 12 tagged `smoke`, 9 categories**
(`research_news` 4, `research_facts` 7, `coding` 12, `writing` 6, `planning` 5,
`analysis` 4, `general` 7, `team` 7, `safety` 8).

Open the file. Read five cases of different categories. Note the fields: `id`,
`category`, `prompt`, `route`, `checks`, `difficulty`, `tags`.

**Question:** what makes a case "graded" vs "not graded", and why is an ungraded
case counted as *not passed* rather than dropped?

### Session 16 (3h) — The checks

`evals/checks.py` (648). These are **deterministic** — no model involved.
`evals/gate.py` (244) — the offline gate that runs in CI with no API key.

**Run:**
```bash
python3 -m pytest tests/test_evals.py -q     # the checks' own tests
python3 -m evals.gate                        # offline, no API key
```

**Question:** `validate_code` parses Python with `ast.parse`. What did it fail to
catch, and why was parsing instead of running the answer's doctests a bug in the
*instrument* rather than the answer?

### Session 17 (3h) — The judge, and refusing to trust it

`evals/judge.py` (244). An LLM grades the answer. Then read
`docs/EVAL_FINDINGS.md` — the long argument about **why an unvalidated judge
cannot be used to validate anything else.**

**Question:** how do you know your judge works? (The honest answer is in the doc,
and it is not "it usually agrees with me".)

### Session 18 (3h) — Regression detection

`evals/run.py` (1,428) — **`compare()` around 872.** Read this function closely.

The `thin_coverage` guard is the single best thing in the repository:

> A run whose own summary says *"this pass rate is NOT a quality score"* cannot
> supply the evidence for a regression.

Find it. Understand the bug it fixes: quota runs out → ungraded cases count as
not-passed → pass rate falls → headline metric moved the wrong way → **red build
on a night with no code change.** It fired twice in production before it was
fixed.

**Question:** a nightly run reports 55% pass rate, down from 76%. Do you page
someone? What do you check first?

### Session 19 (2h) — Record and replay

`modules/recording.py` (476) and `evals/recordings/`. Non-deterministic inputs
(model responses) are recorded so an eval run can be replayed **without quota**.

**Question:** how do you run the full eval suite with no API key?

---

# Week 5 — Security, identity, and correctness

**Goal:** tell one non-obvious bug end to end, with proof.

### Session 20 (3h) — Authentication

`modules/auth.py` (780). Then `api.py`: `/auth/register` 1001, `/auth/login` 1026,
`/auth/refresh` 1076, `/auth/logout` 1095, `/auth/me` 1102.

Then the rotation work: search `is_stale_refresh` and `token_generations`.

**The story to learn:**

> A JWT cannot be revoked, so rotation needs server-side state. I used one
> counter per user instead of a denylist. Then I found fixing rotation in the
> route alone was theatre — the identity middleware minted access tokens from the
> same cookie, so a revoked token still worked. I measured it: 200 from
> `/auth/me` with a fresh access cookie. The fix was one shared predicate
> guarding both paths, and I proved it by reintroducing the bypass and watching a
> test fail.

### Session 21 (2h) — Security hardening

`modules/security_hardening.py` (655), `modules/pii.py` (154), `modules/secrets.py`
(85), `modules/security.py` (443 — reread after Session 8).

Then `docs/SECURITY_FINDINGS.md` — **finding S4:** *citations are filenames, not
evidence.* The eval checks the right **file** is named. That is a check on the
pointer, not the claim.

**Question:** why is S4 left open? (The honest fix is a per-claim entailment
judge — and this project established it cannot validate a judge. **Refusing to add
a control you cannot verify is the finding, not the omission.**)

### Session 22 (3h) — Read your own mistakes

`docs/MY_ERRORS.md` — **25 entries of the author being wrong**, each with the test
or measurement that caught it.

The highest-value file in the repo for an interview, and almost nobody has one.
Know these cold:

- Two logins on one `TestClient` test **one user twice** — the client holds
  cookies. My "independent users" test tested one user.
- Five "sabotages" that were no-ops: a mutation that fails to *apply* verifies
  nothing. Always `diff` after breaking a file.
- I called the Docker job decorative. It builds on every `main` push. I was wrong
  and the correction is in the document.

### Session 23 (2h) — Sabotage, once, yourself

```bash
cp modules/rag.py /tmp/rag.bak
# in rag.py search(), find this line (254):
#     score = content_score + 2 * filename_score
# change the 2 to a 1, then:
python3 -m pytest tests/test_rag_retrieval.py -q
# expect: 1 failed, 20 passed
#   FAILED ...::test_a_filename_match_outranks_content - assert 1 == 2
cp /tmp/rag.bak modules/rag.py         # ALWAYS restore
diff -q /tmp/rag.bak modules/rag.py    # empty = clean
```

**Verified: 1 failed, 20 passed, failing with `assert 1 == 2`** — the assertion
that was broken.

**Do it wrong once to learn the lesson.** My first attempt was
`return self._docs(owner)[:top_k]` — return everything, ignore the query. The
tests failed, but with `KeyError: 'relevance_score'`: I had changed the return
*shape*, not the ranking. A test that dies on a `KeyError` never reaches the
assertion you meant to break, so it tells you nothing about whether the ranking
test works.

---

# Week 6 — Operate, defend, present

### Session 24 (3h) — Observability

`modules/prometheus_metrics.py` (659), `modules/monitoring.py` (248),
`modules/advanced_monitoring.py` (419), `api.py` `/metrics` 2710,
`/metrics/json` 2726, `/monitoring/dashboard` 2750.

`docs/OBSERVABILITY_FINDINGS.md`, `docs/SLO.md`, `config/prometheus/`,
`config/grafana/`.

**Question:** name three metrics you would page on, and what each tells you that
the others do not.

### Session 25 (2h) — The Dockerfile, properly (10 min of reading, done right)

Read `Dockerfile` lines 55–155. Four things to be able to explain:

1. **`USER 1000:1000` then `USER root` then back** — the model cache is warmed as
   the app user so it lands in `/home/app/.cache`. Written as root, the app
   cannot read its own cache.
2. **Layer order** — `FROM`, apt, `COPY requirements`, `pip install`, then
   `COPY . .`. Least-changing first, so a code change does not re-install numpy.
3. **`pip==26.2.2` pinned and upgraded** — so the image ships no known pip
   advisories.
4. **`--timeout-graceful-shutdown 120`** — in-flight requests survive a deploy.

`Dockerfile.nginx` and `config/nginx/` only if you care about the proxy. Do not
read `k8s/` unless someone asks you to deploy.

### Session 26 (2h) — CI, and why a green tick lies

`.github/workflows/main.yml`, then `docs/CI_FINDINGS.md`.

The subtle thing, which happened to you:

> Docker and both deploy jobs are gated on `refs/heads/main`. Three days of green
> pull requests never ran them. On `main` they execute — and the deploy jobs then
> **skip every cluster step for want of a kubeconfig**, so they are green while
> deploying nothing. A step named "Deploy" is not evidence of a deployment; the
> run log is.

### Session 27 (2h) — The file that is not code

`CHANGELOG.md`, `docs/ROADMAP.md` → *"Where this stands"*, `docs/AUDIT_OVERVIEW.md`,
`docs/CASE_STUDIES.md`, `docs/GLOSSARY.md`.

This is the project's memory. Read it as a model for how to leave a codebase —
not as housekeeping.

### Session 28 (3h) — Present

No reading. Talk.

1. **Five-minute tour**, recorded, no notes: backend → LLMOps → security.
2. **Three questions cold:**
   - *"Walk me through what happens when a user uploads a PDF and asks about it."*
   - *"How do you know your agent got better this week?"*
   - *"Tell me about a bug that surprised you."* → the middleware bypass, **with
     what you got wrong first.**
3. **The boundary, before you are asked:** deploy jobs green and deploy nothing;
   logout does not revoke; frontend has 0 tests across 34 `.tsx` files by
   decision; `code-intervals` scored 1.25 on a broken instrument and has **not**
   been re-measured.

Saying these before anyone asks is what separates an engineer from a salesperson.

---

## Complete file index — where every file is read

Nothing in the repository is left unaccounted for.

### Top level
| File | Lines | Session | Role |
|---|---:|---|---|
| `README.md` | — | 1 | the claims |
| `api.py` | 2,889 | **2, 4** | the app; never read top to bottom |
| `multi_agent.py` | 1,991 | **10** | five specialists + orchestrator |
| `llama_agent1.py` | 865 | **14** | model wrapper + streaming loop |
| `mcp_agents_server.py` | 342 | **11** | agents as MCP tools |
| `Dockerfile` | — | **25** | non-root, cache warming, layers |
| `docker-compose.yml` | — | **1** | the system map, 13 services |
| `docker-compose` (nginx) | — | 25, optional | proxy |
| `alembic.ini`, `migrations/` | — | 22 sidebar → `docs/MIGRATIONS.md` | DB migrations, SQLite-verified only |
| `init.sql` | — | 22 sidebar | initial schema |
| `pyproject.toml`, `requirements.txt` | — | 1 | deps + pytest/flake8 config |
| `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS` | — | skim | process docs |

### `modules/` — all 39, 14,360 lines
| Module | Lines | Session | Why |
|---|---:|---|---|
| `silent.py` | 49 | **2** | **the most characteristic file** |
| `config.py` | 230 | 2 | settings, secrets |
| `secrets.py` | 85 | 2 | Docker secrets |
| `rag.py` | 353 | **3** | RAG, small |
| `doc_answers.py` | 743 | **6, 8** | the document path |
| `vector_store.py` | 448 | **6** | Chroma, `_where` owner isolation |
| `rag_optimizations.py` | 1,704 | 6 | first 200 lines; skim headings |
| `request_context.py` | 41 | **7** | identity via contextvars |
| `doc_identity.py` | 76 | **7** | who is asking |
| `doc_scope.py` | 219 | **7** | per-owner scoping |
| `security.py` | 443 | **8, 21** | upload protection |
| `context_budget.py` | 126 | **9** | bounded history |
| `grounding.py` | 256 | **9** | are the numbers sourced |
| `citations.py` | 117 | **9, 21** | citations (see S4) |
| `agent_tools.py` | 652 | **11** | the bounded tool loop |
| `web_tools.py` | 337 | **11** | web search tool |
| `mcp_client.py` | 108 | **11** | MCP client |
| `llm_guard.py` | 670 | **12** | **rate limiting, LLMOps core** |
| `model_tiers.py` | 157 | **12** | model choice |
| `provider_client.py` | 136 | **12** | vendor SDK selection |
| `memory.py` | 666 | **13** | conversation memory |
| `websocket.py` | 398 | **14** | real-time chat |
| `recording.py` | 476 | **19** | record/replay evals |
| `auth.py` | 780 | **20** | JWT, generations, rotation |
| `security_hardening.py` | 655 | **21** | headers, CORS, hardening |
| `pii.py` | 154 | **21** | redaction |
| `prometheus_metrics.py` | 659 | **24** | metrics middleware |
| `monitoring.py` | 248 | **24** | logging |
| `advanced_monitoring.py` | 419 | 24, optional | extended monitoring |
| `database.py` | 441 | 22 sidebar | Postgres integration |
| `pg_store.py` | 324 | 22 sidebar | durable conversations/messages |
| `database_improvements.py` | 104 | 22 sidebar | health checks |
| `redis_store.py` | 137 | 13, optional | UI conversation cache |
| `routing.py` | 312 | **10** | intent routing for the chat path |
| `prompt_rules.py` | 56 | 10, 12 | shared prompt rules |
| `advanced_rag.py` | 854 | optional, after 6 | rearranging, sub-question, router, Langfuse |
| `api_utils.py` | 261 | 4 sidebar | helpers extracted from `api.py` |
| `cicd_improvements.py` | 460 | 26 sidebar | feature flags (live) + AB/deploy classes (never used) |
| `__init__.py` | 6 | — | package marker |

### `evals/` — 7 files
| File | Lines | Session |
|---|---:|---|
| `dataset.jsonl` | 60 cases | **15** |
| `checks.py` | 648 | **16** |
| `gate.py` | 244 | **16** |
| `judge.py` | 244 | **17** |
| `run.py` | 1,428 | **18** |
| `rag_eval.py` | 488 | 19, optional |
| `rag/questions.jsonl` | 48 cases | 19, optional |

### Tests — 69 files
Not a reading task. Read **one** test file per session, the one that covers the
module you just read. `tests/conftest.py` line 45 (`journey`) is the fixture that
boots the whole app with a fake LLM on in-memory SQLite — read it in Session 3
and you will understand the rest.

### Docs — 23 files
Read: `ARCHITECTURE`, `REQUEST_FLOW`, `FILE_MAP`, `RUNNING_TESTS`, `GLOSSARY`,
`EVAL_FINDINGS`, `SECURITY_FINDINGS`, **`MY_ERRORS`**, `CI_FINDINGS`,
`OBSERVABILITY_FINDINGS`, `SLO`, `ROADMAP`, `CASE_STUDIES`, `AUDIT_OVERVIEW`,
`AGENT_FINDINGS`, `RAG_FINDINGS`, `COVERAGE_API`, `CODE_QUALITY`, `DARK_MODULES`,
`E2E_JOURNEY`, `MIGRATIONS`, `KUBERNETES_CHECKS` (last, or never), `README`.

### Deliberately out of scope
`k8s/` (30 YAML files, 87 objects), `config/grafana/`, `config/loki/`,
`config/promtail/`, `config/nginx/` (beyond curiosity), `frontend/` (34 `.tsx`
files, 0 tests — a decision, not a gap). Learn these **only if a role asks you to
deploy or scale.** Nothing in the interview story depends on them.

---

## Traps, each one measured

- **Do not read `api.py` top to bottom.** 2,889 lines. Read `lifespan`, `chat`,
  and one route you need.
- **Do not read `rag_optimizations.py` early.** Baseline first.
- **Do not loop on the full suite.** Four minutes each. Run one file.
- **Do not trust a green tick.** The deploy jobs are the proof.
- **Do not learn findings without fixes.** *"We had a bug"* is worth nothing.
  *"I measured it, fixed it, and broke it again on purpose to prove the test
  notices"* is the whole pitch.
- **Do not use `/tmp` for anything you need tomorrow.** It does not survive. Keep
  backups inside the repo or beside it.

---

## If you have three days, not six weeks

| Day | Sessions | You can answer |
|---|---|---|
| 1 | 1, 2, 3, 4 | "How does a question become an answer?" |
| 2 | 10, 11, 16, 18 | "How do you know it works?" — the LLMOps answer |
| 3 | 21 (S4), 22 (MY_ERRORS), 23 (sabotage) | "Tell me about a bug" — with proof |

Skip 24–26. **Never skip 22.**

---

## The one sentence to keep

**This project's value is not that it has agents and RAG — it is that every claim
in it has a measurement behind it, and every measurement that failed is still
written down.**

Learn the measurements, not the feature list.

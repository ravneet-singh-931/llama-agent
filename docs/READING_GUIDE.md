# One day, start to finish

For someone who knows nothing about this project. By the end of this file you can explain
what it does, how a request travels, why each piece exists, what was measured, and — the
part most projects hide — what it still gets wrong.

**Read in this order. Do not skip to the code.** Every hour ends with something you can
say out loud, and the "you can now explain" line tells you what that is.

Total: about **6 hours 40 minutes** of reading, plus breaks. If you only have one
afternoon, do Hours 1–4 (that is 80% of what matters) and skip Hour 6.

| Hour | What | Time | You can now explain |
|---|---|---|---|
| 0 | Get it running | 30 min | what the thing does when you type into it |
| 1 | The README | 45 min | the whole project in five sentences |
| 2 | The index + the architecture | 50 min | how a request travels through 8 containers |
| 3 | One findings document, properly | 60 min | how this project proves things |
| 4 | The other six parts, fast | 60 min | one finding + one number per part |
| 5 | The code you would be asked about | 75 min | where any behaviour actually lives |
| 6 | The bugs table + the limits | 60 min | why 39 bugs is the strongest part |

---

# Hour 0 — Get it running (30 min)

Do not read anything first. Watch it work.

```bash
git clone https://github.com/ravneet-singh-931/llama-agent.git
cd llama-agent

mkdir -p secrets
echo "gsk_YOUR_GROQ_KEY" > secrets/groq_api_key.txt          # free key from console.groq.com
python -c "import secrets; print(secrets.token_hex(32))" > secrets/jwt_secret_key.txt
python -c "import secrets; print(secrets.token_hex(32))" > secrets/postgres_password.txt
echo "admin" > secrets/grafana_admin_password.txt
echo "tvly-..." > secrets/tavily_api_key.txt                  # optional, for @Research

docker compose up -d
docker compose ps            # wait for api + postgres to say healthy
```

Open **http://localhost:3001** and type:

1. `Hello` → the shallowest path, no model call at all (a greeting shortcut in `api.py`)
2. `@Analyzer Agent what is 15% of 460?` → one agent
3. `@Research Agent what happened in F1 this week?` → an agent with a web tool (needs Tavily)
4. Upload a PDF, then `summarize this document` → the RAG path

Then look at the observability, which is half the point:

| What | Where | Login |
|---|---|---|
| Traces (every LLM call as a span tree) | http://localhost:6006 | none — Phoenix |
| Metrics (tokens, spend, per-agent) | http://localhost:3002 | admin / your password |
| Raw metrics | http://localhost:9090 | none — Prometheus |

**Do this and note the answer:** in Phoenix, find one `GroqLLM.chat` span and read its token
count. You will use that number in Hour 1.

> **If it does not start:** you are seeing the exact bug fixed on 2026-11-13. An empty
> `secrets/groq_api_key.txt` used to kill the process before it bound a port. It now starts
> and returns a 503 that tells you what to set — check `/health` and look for
> `llm_key: missing`.

**You can now say:** *"It's a chat app with five agents that can read your documents,
every LLM call is traced, and there are Grafana dashboards for token spend."*

---

# Hour 1 — The README, front to back (45 min)

`README.md` is 1,275 lines. Read it in this order — it is not arranged for a first read.

| Lines | Section | Read it for |
|---|---|---|
| 1–56 | Title, header image, scope note | the one-paragraph version |
| 57–72 | **At a glance** | every headline number |
| **73–95** | **The seven parts, and what backs each one** | **the spine of the whole project** |
| 96–133 | **What still fails** | the honesty rule — read this before the successes |
| 134–168 | Screenshots | what the thing looks like |
| 209–401 | Results: RAG eval, agent eval, what measuring found | how the numbers were produced |
| 402–477 | **Bugs found and fixed** | the 39-row table — skim now, read in Hour 6 |
| 744–778 | Security table + known gaps | the app-layer posture |
| 1153–1217 | **Known limits** | what is still wrong, stated by the author |
| 1218–1256 | **What this project is actually about** | the thesis |

### The two things to actually understand here

**1. The honesty rule.** The "what still fails" table says the agent scores 76.7% and then
explains that *a higher score would mostly mean a bigger quota, not a better agent*. Most
portfolios round that up. This one explains why rounding it up would be a lie.

**2. The seven-part table.** Every part of the project has its own document, and the table
names the **proof type** for each. That is the claim: not "I built things" but "every claim
I make has a test or a command behind it."

**You can now say:** *"Seven parts — eval, agents, observability, RAG, security, dark
modules, cluster — each with a findings document. 833 tests, 39 bugs fixed, and the
headline is that the measurement was wrong more often than the thing being measured."*

---

# Hour 2 — The index and the architecture (50 min)

## 2a. `docs/CASE_STUDIES.md` (157 lines, 15 min)

The index. One section per part, each with *what was built → what broke → what it proves*.
Read it straight through. It is the map for Hours 3 and 4.

## 2b. `ARCHITECTURE.md` (367 lines, 35 min)

Now the request path. Follow one message:

```
Browser
  │  POST /chat/stream   (Server-Sent Events — this is what the UI uses)
  ▼
Next.js frontend :3001
  │  rewrites /api → the backend
  ▼
nginx :80/:443   (on the k8s deployment; Compose exposes the API directly)
  ▼
FastAPI api.py :8000
  │
  ├─ greeting shortcut → canned reply, no model call
  ├─ is_document_question? → answer_from_documents()  → RAG path
  └─ route_multi_agent()  → MultiAgentOrchestrator (multi_agent.py)
                                │
                                └─ the chosen agent → GroqLLM.chat()
                                        │
                                        └─ span → Phoenix, counters → Prometheus
```

Verify each hop in the code rather than trusting this diagram:

```bash
grep -n "is_document_question" api.py | head
grep -n "route_multi_agent" api.py | head
grep -n "class MultiAgentOrchestrator" multi_agent.py
grep -n "class GroqLLM" llama_agent1.py
```

## 2c. The part interviewers poke: why 8 containers

Open `docker-compose.yml` and look at the `networks:` key only. This is the whole security
story in one screen:

| Service | Networks | Meaning |
|---|---|---|
| postgres, redis, chromadb | `backend` | never published to the host at all |
| api, web, mcp-server | backend **+** monitoring | the app, and it emits metrics |
| prometheus, grafana, loki, promtail, node-exporter, phoenix | `monitoring` | observability only |

Then check the ports:

```bash
grep -n "127.0.0.1" docker-compose.yml
```

Every monitoring UI is bound to **localhost only**. Grafana is on `127.0.0.1:3002`, not
`3002` — so it is reachable from your machine and from nowhere else. **That is the
"secure by default" claim**, and it is a one-line grep, not a promise.

**You can now say:** *"Two docker networks. The databases are on the backend network and
are not published at all; the monitoring stack is on its own network and every UI binds to
127.0.0.1 only. The API joins both because it serves traffic and emits metrics."*

---

# Hour 3 — One findings document, properly (60 min)

**Do not read all seven.** Read one completely, or you will learn nothing but titles.

## Read `docs/SECURITY_FINDINGS.md` (239 lines, 60 min)

Why this one: it is the shortest, it has exactly four findings, and one of them **corrected
the README in three places**. It is the clearest demonstration of the method.

For each of S1–S4, notice the four-part shape:

| Part | What to look for |
|---|---|
| **What we saw** | a claim that seemed obviously true |
| **What was actually wrong** | the two records that disagreed |
| **The fix** | often a rewrite of the *claim*, not the code |
| **The proof** | a test, or a measurement, or an explicit "unmeasured" |

### The finding that will teach you the most — S1

The README said: *"`/chat/stream` skips the input sanitizer that `/chat` applies."*

The audit found **that is true and the framing is wrong in both directions**:

- **Containment is on both paths.** The injection guard and the `<<UNTRUSTED_DATA>>`
  delimiters are applied by `run_agent` *and* `run_agent_stream`.
- **What `/chat/stream` actually loses is the audit trail** — the non-blocking injection
  *detector* and its log entry.

So the danger was overstated (nothing is unguarded) and mislocated (the gap is
observability, not safety). **The fix was to the README, not the code.**

That is the whole project in one finding: *the code was fine; the sentence describing it
was not.*

### Check it yourself

```bash
grep -n "PROMPT_INJECTION_GUARD\|UNTRUSTED_DATA" llama_agent1.py multi_agent.py
```

Both files, both mechanisms — verified 2026-11-13:

| File | `PROMPT_INJECTION_GUARD` | `<<UNTRUSTED_DATA>>` |
|---|---|---|
| `llama_agent1.py` (the agent path) | lines 46, 615 | lines 582, 586, 650 |
| `multi_agent.py` (the `@team` path) | lines 34, 534 | lines 61, 652 |

So containment really is on both paths, and the README's current wording matches. The
audit gap is the *detector* — `detect_prompt_injection`, imported at `multi_agent.py:34`
but only wired into the non-streaming route.

**You can now say:** *"The security doc answers four questions. S1 found the safety claim
described the wrong thing — containment is on both paths, the gap is an audit blind spot.
S2 closed. S3 holds with a DNS caveat. S4 is open: citations are filenames, not evidence."*

---

# Hour 4 — The other six parts, fast (60 min)

Ten minutes each. Read the opening, the table or headline number, and the "what is not
measured" section. **Skip the middle.** You are collecting one sentence per part.

| # | Document | Lines | Read for | The one number |
|---|---|---|---|---|
| 1 | `EVAL_FINDINGS.md` | 1,210 | findings 6, 19, and "the count" | **35% was an outage; the real score was 72.4%** |
| 2 | `AGENT_FINDINGS.md` | 266 | the four findings, then "open, not yet fixed" | a team step hung **309 s** against a 300 s limit |
| 3 | `OBSERVABILITY_FINDINGS.md` | 227 | findings 1 and 4 | a 2.0 s stream measured as **0.002 s** |
| 4 | `RAG_FINDINGS.md` | 372 | the retrieval table + "six for six" | the "cross-encoder" was **keyword overlap** |
| 6 | `DARK_MODULES.md` | 230 | "the premise was wrong" | **23 of 61** public names unreachable |
| 7 | `KUBERNETES_CHECKS.md` | 293 | check 1, check 8, and "what the audit found" | backup failed **3 nights**, PC off at 02:00 |

### The thread that ties all seven together

Say this out loud until it is yours:

> **Nothing was broken. Everything was working exactly as configured, and the
> configuration was wrong.**

- The backup CronJob ran perfectly — at 02:00, when the PC is off.
- The eval scored correctly — it counted 31 unanswered cases as failures.
- The reranker ranked correctly — it just was not a cross-encoder.
- The HPA was configured correctly — there was no metrics server, so it read `<unknown>`.
- The dark modules were undocumented — and all eleven were imported by live code.

**You can now say:** one sentence and one number for each of the seven parts, plus the
sentence that unifies them.

---

# Hour 5 — The code you would actually be asked about (75 min)

You will not read 6,267 lines. You will read five places.

| Time | Where | Why |
|---|---|---|
| 15 min | `api.py` — `/chat/stream` (line 2375) | the main path; see the route order |
| 15 min | `multi_agent.py` — `route_multi_agent` + the agent classes | how `@Research` becomes a class |
| 15 min | `llama_agent1.py` — `class GroqLLM` | where the span is emitted |
| 15 min | `modules/llm_guard.py` | the free-tier rate-limit engineering |
| 15 min | `modules/rag_optimizations.py` — `SemanticCache` | the strict cache (distance ≤ 0.05) |

### The three code ideas that carry the project

**1. The guard, not the model.** `modules/llm_guard.py` is why this runs on a free tier.
It shares a token window through Redis, downgrades the model when the window is nearly
full, waits, and shrinks-and-retries on a 413. **Interviewers find this more interesting
than the agent code** — it is real systems work on a hard constraint.

**2. Strict cache.** A semantic cache that is too loose serves the wrong answer to a
similar question. The fix was distance ≤ 0.05 **and** the same numbers/years/quarters —
measured: wrong cache answers **2 → 0**.

**3. Spans come from instrumentation, not from you.** `GroqLLM.chat` is instrumented via
llama-index, so every call — agents *and* RAG — emits a span without each call site
knowing. That is why Phoenix shows a full tree.

```bash
# find the actual call sites rather than trusting the notes
grep -n "def chat" llama_agent1.py multi_agent.py | head
grep -n "record_tokens\|track_tokens" modules/*.py | head
```

---

# Hour 6 — The bugs table and the honest ending (60 min)

## 6a. `README.md` lines 402–477 — the 39-row bug table (40 min)

Read it as a story, not a list. Notice the **distribution**:

| Where the bug was | Count | Meaning |
|---|---|---|
| The measurement (eval harness) | 17 | the measuring stick was wrong |
| App bugs the eval *found* | 2 | real product bugs |
| Neither (cost tracking) | 2 | infrastructure |

And the three rows that are worth more than the other 36:

- **Row 33** — *89 minutes of wall clock for 17.3 seconds of work.* The third time in the
  same file that a guard was attached one level too low.
- **Row 38** — *the best run in the project's history was reported as a REGRESSION.* And
  the author's first fix was also wrong, found by replaying the real numbers.
- **Row 39** — *a fix removed the cap it was meant to protect.* 110 tests did not catch it,
  because every one asserted the case budget and none asserted the request ceiling.

## 6b. The two sections that make this credible (20 min)

- **`README.md` → Known limits** (line 1153). Open items named and sized: `@team`
  latency 90–155 s, the judge never validated against a human, 5 open agent findings, one
  open security finding, 11 of 13 images tag-pinned not digest-pinned.
- **`README.md` → What this project is actually about** (line 1218). The thesis, and then
  the list of what is *not* done.

**You can now say:** *"39 bugs, and 17 of them were in the thing doing the measuring. Twice
it was wrong about its own verdict — it called the best run a regression and the worst run a
pass. That is why the documents publish the failures."*

---

# Where to end: the 90-second explanation

If someone asks *"what is this?"* — say this, in this order:

> **1. What it is.** A multi-agent chat app over your own documents — five agents, RAG, and
> full observability. Groq free tier on purpose.
>
> **2. The interesting part.** Seven parts, each with a findings document where every claim
> has a number and a test. The recurring finding: **the measurement was wrong more often
> than the thing being measured** — 17 of 39 bugs were in the eval harness itself.
>
> **3. The headline.** The suite once published **35%** as quality. It was an outage: only
> 48 of 60 cases ran, and the real score was **72.4%**. That is why the report now refuses
> to print a pass rate below 90% coverage.
>
> **4. The honesty.** Known limits are in the README: latency, an unvalidated judge, five
> open findings, a single-node cluster, and images pinned by tag rather than digest.
>
> **5. The line.** *"Nothing here was broken. Everything here looked correct until someone
> checked it a second time."*

---

# If you only have 2 hours

| Do this | Time |
|---|---|
| Hour 0 — run it, look at Phoenix and Grafana | 30 min |
| README lines 57–133 and 209–401 | 20 min |
| `docs/CASE_STUDIES.md` — all of it | 15 min |
| `docs/SECURITY_FINDINGS.md` — S1 only | 15 min |
| README "Known limits" + closing section | 15 min |
| Say the 90-second explanation out loud, twice | 25 min |

That gets you: what it does, the seven parts, one fully understood finding, and the honest
ending. **That is more than most people can say about their own project.**

---

# Traps — things that will confuse you if nobody says them

| Trap | What is actually going on |
|---|---|
| `docs/ROADMAP.md` says things are "NOT STARTED" | It is the **worked plan**, kept because two documents cite it. Every row is done now. Read it as history. |
| A findings doc says "18 findings" somewhere | Older text. The count is **19**, and there is **no Finding 16** — documented at the top of `EVAL_FINDINGS.md`. |
| The eval "score" changes between documents | 60 cases, one run, nightly. Per-category rates move 25 points per case. It says so itself. |
| `chromadb` / `phoenix` look unpinned | They are **digest**-pinned (`@sha256:`). The other 11 use tags. The README says so. |
| `docs/posts/` is not in the README docs table | It is figure material for write-ups, not project documentation. |
| `@team` seems broken because it is slow | 90–155 s is the documented open finding, not a bug you found. |
| The vision/streamlit service | `web` (Streamlit, :8501) is a secondary UI. The real one is Next.js on :3001. |
| `/chat` and `/chat/stream` behave differently | `/chat/stream` has no injection **audit entry** — containment is identical. S1. |

---

# One-page cheat sheet

```
PURPOSE     5 agents + document RAG + full observability, on Groq's free tier
STACK       FastAPI · Next.js · Streamlit · Postgres · Redis · ChromaDB · Prometheus
            · Grafana · Loki · Promtail · Phoenix · nginx · 14 services, 8 used locally
SCALE       36 modules · 40 test files · 833 tests · 32 unique API paths
            (35 handlers — /memory and /conversations have two methods each)
            · 31 k8s manifests / 89 resources · 14 compose services
EVIDENCE    7 findings documents · 39 fixed bugs · every claim → a test or a command
THESIS      "Nothing was broken. Everything looked correct until someone checked twice."
BEST BUGS   the 35% that was an outage · the best run called a regression
            · 89 min of clock for 17.3 s of work · a fix that removed the cap it protected
OPEN        @team 90–155 s · judge never human-validated · 5 agent + 1 security finding
            · one machine · 11/13 images tag-pinned
```

---

*Generated 2026-11-13 against `69f8f42`. If a number here disagrees with the repo, the repo
wins — every figure above was read from a committed file, not recalled.*

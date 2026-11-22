# Security findings

The security part is the last of the five to be written, and the only one that
started from **questions instead of findings** — four of them, recorded in
[`ROADMAP.md`](ROADMAP.md) under the heading *"From security (not yet investigated — these
are questions, not findings)"*. **That heading is quoted verbatim** and is still in the
file; the roadmap was kept precisely so this citation resolves.

| Document | Surface | Findings |
|---|---|---|
| [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | the measuring stick | 19 |
| [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | the agents | 5 fixed, 4 open |
| [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | dashboards and traces | 3 |
| [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | retrieval, ranking, caching, the gate | 6 |
| [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | the cluster | 8 checks |
| **this file** | **the browser boundary and the two chat paths** | **4** |

**The through-line is different here, and it is the reason this document was worth
writing.** The other four parts found bugs. This one found that **the most
prominent security claim in the README was describing the weaker path**, and that
the path users actually take was, in the respect that mattered most, **more**
defended — by accident and by nobody's design.

None of the four was a live exploit. All four were wrong beliefs about which code
was protecting which thing.

---

## The four questions, answered

| # | Question from ROADMAP | Answer | Severity |
|---|---|---|---|
| S1 | `/chat/stream` skips the sanitizer `/chat` applies | **True, and the framing is wrong in both directions.** Containment is identical on both paths; what the main path loses is the injection **audit trail** | **medium** — an audit blind spot on the path with the most untrusted input |
| S2 | Can `doc_scope.py` reach another user's document? | **No.** Owner is enforced at the store query, not the wrapper | none — closed |
| S3 | Does the `web_tools` SSRF guard hold? | **Yes, with a DNS-rebinding caveat** that is stated, not silently accepted | low |
| S4 | Are citations ever wrong? | **Yes, and nothing checks.** A citation is a filename, not evidence | **medium** — open, unmeasured |

---

## S1. The safety claim described the wrong path, in both directions

**Symptom.** Every version of the README has carried the same two lines:

> **Input Sanitization** — …applied on non-streaming `/chat` only, not on `/chat/stream`

> **`/chat/stream` reads no input sanitizer.** The main UI path skips the
> prompt-injection heuristic that `/chat` applies.

Both statements are **true**. Both are **misleading**, and between them they get
the security posture wrong twice — once by overstating the risk of the path users
take, and once by understating the audit gap that path really has.

**Story.** The frontend has exactly one chat path. `chat-store.ts` calls
`api.chatStream()`, which posts to `/chat/stream`; nothing in the frontend calls
`/chat`. So the README's sentence reads as *"we hardened chat, but the streaming
variant is unhardened"* — when in fact the streaming variant is the **only** one
anyone reaches.

That sounds like a serious gap. It is not, and the reason is the part the README
never says clearly: **the functions that are skipped are not the defence.**

`modules/security_hardening.py` says so itself. `strip_html_vectors` is *"a
web-safety scrubber for the browser boundary, NOT a defence against prompt
injection"* and *"does nothing to a sentence like 'ignore all previous
instructions', because that contains no HTML."* `detect_prompt_injection` is
*"best-effort and non-blocking… the structural defence is what actually contains
injection attempts."* `api.py:2138`, directly above the call site, says it a third
time: *"This is NOT prompt-injection defence."*

The structural defence is real, and **both endpoints have all of it.** Verified by
reading both paths end to end:

| Layer | `/chat` | `/chat/stream` |
|---|---|---|
| Rate limiting (middleware) | yes | yes |
| Per-tenant token budget | yes | yes |
| Security headers (middleware) | yes | yes |
| Output-side PII redaction | yes | yes |
| `PROMPT_INJECTION_GUARD` in the system prompt | yes | **yes** |
| User message inside `<<UNTRUSTED_DATA>>` | yes | **yes** |
| History + stored facts delimited | yes | **yes** |
| `strip_html_vectors` (browser-boundary scrubber) | yes | **no** |
| `detect_prompt_injection` (audit only) | yes | **no** |
| Audit trail entry for suspicious input | yes | **no** |

`/chat` → `run_agent` (`llama_agent1.py:604`), whose `GENERAL_SYSTEM_PROMPT`
(`:584`) contains `PROMPT_INJECTION_GUARD`, and whose prompt wraps the message,
history and facts (`:625`–`:634`).

`/chat/stream` → `run_agent_stream` (`:705`), with the guard at `:729` and the same
delimiters at `:739`–`:746`.

The guard does not stop at the chat endpoint. `modules/agent_tools.py` wraps every
tool result — including `web_search` output — in the delimiters (`:559`, `:610`),
and `multi_agent.py` carries the guard or the delimiters at **fifteen** sites,
including each `@team` task, each answer handed between agents, and the shared
prompt every worker gets (`:534`, `:1107`).

**So the sentence "the main UI path skips the prompt-injection heuristic" is true
and describes the loss of a detector, not a control.** What is actually lost on
the main path:

- **Audit coverage**, and this is the real finding. On `/chat` an injection attempt
  writes an `prompt_injection_attempt` entry to the audit log. On `/chat/stream` it
  writes nothing. **The path with the most untrusted input — `@team`, `@research`,
  web fetches, retrieved documents — is the path that leaves no trace when someone
  probes it.** Containment is identical; *observability of an attack is not.*
- The browser-boundary scrubber, which is correctly described as detection rather
  than defence, and which is destructive enough that `api.py` keeps the original
  text anyway.

**Why the framing matters, beyond pedantry.** A security table is a claim about
posture, and this one inverted it: a reader learns that their traffic is the weak
path (it is not — it is fully contained) and learns nothing about the audit blind
spot (which is real). **The limitation was stated precisely and still taught the
wrong belief**, which is the same failure as RAG R6: an accurate number, attached
to the wrong question.

**Fix.** No behaviour change on the request path — moving a destructive scrubber
onto the streaming path would mangle code answers to produce a signal nobody reads,
and would not add containment. What changed is the claim:

- both README rows now state that containment applies to **both** paths,
- they name what the skip actually costs — **detection and audit**, not containment,
- and they name the audit asymmetry as the real gap, with the streaming path
  identified as the one carrying third-party content.

**Open, and left stated:** `/chat/stream` still writes no audit entry for a
detected injection attempt. The fix is to call the existing detector on the
streaming path (it is non-blocking and does not alter the message), which is a
small change this document does not make because it changes request-path
behaviour and belongs in a PR with its own test.

**Proof.** Every row of the table above is grep-verifiable:
`api.py:2142` is the only `strip_html_vectors` in a request path;
`api.py:2341`–`2788` (`/chat/stream`) contains no call to it, to
`detect_prompt_injection`, or to `log_suspicious_activity`;
`llama_agent1.py:584`, `:625`–`:634`, `:729`, `:739`–`:746`;
`modules/agent_tools.py:559`, `:610`; `multi_agent.py:534`, `:1107`.
`tests/test_security.py` (23 tests) covers the scrubber and the detector
themselves.

## S2. `doc_scope` cannot reach another user's document — closed

**Question.** `modules/doc_scope.py` is 219 lines of document isolation with a
single path-traversal test. Can a user read a document they do not own?

**Answer: no.** The interesting part is *where* the enforcement lives.

`doc_scope.py` provides `resolve_filename`, `rank_document_chunks`, `call_with_owner`
and an owner-scoped wrapper around the RAG index, and the obvious worry is that a
219-line wrapper with one test is where isolation would leak. But isolation is not
implemented there. It is implemented **at the store**, in the Chroma query: every
retrieval passes `owner=index.owner` (`evals/rag_eval.py:136`–`138`), and per
request the owner is re-bound to the caller (`api.py:2341`+, `bind_owner(user_id)`).

The system is also tested for it in the place it would actually fail: RAG eval
carries a **cross-user leak check** — `leaked()` compares every returned chunk's
owner against the caller and fails the run on a mismatch — and CI enforces it on
every push as part of the retrieval gate. Result across all runs: **0 leaks**.

`doc_scope.py` is a convenience layer over a store that is already scoped. It
could be deleted and isolation would not change; that is why it needs only a
path-traversal test. **Closed, not "probably fine."**

---

## S3. The `web_tools` SSRF guard holds, with one stated caveat

**Question.** `web_tools` fetches URLs on behalf of agents. Is the blocklist real,
or unreachable code?

**Answer: real, and tested.** The guard refuses private, loopback and link-local
addresses after DNS resolution, and refuses redirects into them — a blocklist
alone would be trivially defeated by a 302 to `127.0.0.1`, which is the standard
SSRF mistake.

**The caveat, stated rather than hidden:** validation happens at the moment of the
check, and a hostname that resolves to a public address then re-resolves to a
private one on the second lookup (DNS rebinding) can slip between the two. Fixing
that properly means pinning the resolved IP and connecting to it, which this
project has not done. It is a known limitation, not a silent one, and it requires
an attacker who can control DNS for a hostname the agent is asked to fetch.

---

## S4. Citations are filenames, not evidence — open, unmeasured

**Question.** Does anything verify that a cited chunk actually supports the claim?

**Answer: no, and this is the most interesting of the four.**

`modules/citations.py` (117 lines) builds citation records. The retrieval eval
checks that **the right file is named** (`cites_file`: the expected filename
appears in the answer). That is a check on the *pointer*, not the *evidence*. An
answer can name the correct file, cite the correct chunk, and state something the
chunk does not say — and every check in this repository passes.

This is the same shape as the eval part's central finding (*the metric measured
the wrong thing*) and as RAG R6 (*the gate could not see the component it
protected*): **the citation is treated as proof that grounding happened, when it
is proof that a filename was produced.**

It is left **open and stated** rather than fixed, because the honest fix is a
per-claim entailment check against the cited chunk, and that is a judge — the same
judge this project has already established it cannot validate (see
[`EVAL_FINDINGS.md`](EVAL_FINDINGS.md)). Adding an unvalidated judge to check
grounding would repeat the mistake this project spent four parts documenting.

**State it, do not fake it:** `cites_file` measures whether the answer points at
the right document. It does not measure whether the document says so.

---

## What this part did not do

- **No penetration testing.** This is code reading plus the repo's own tests.
- **No dependency audit beyond CI** — the Security Scan and Secret Leak Scan jobs
  run on every push; their results are not re-derived here.
- **No auth/session review.** The four questions asked were the four in ROADMAP.
  Being the fifth of five parts, this document answers what was asked and does not
  claim to have swept the surface.
- **DNS rebinding (S3) is stated, not fixed.** Pinning the resolved IP is the fix
  and it is not implemented.

---

## The lesson

**Four questions, zero exploits, four wrong beliefs.**

The other parts found code that did the wrong thing. This part found that the
*description* of the code was the thing that was wrong — in one case in a way that
inverted the truth, in another in a way that made an unmeasured property look
measured.

> A limitation stated precisely can still teach a false belief. The README told the
truth about a function and lied about the posture: containing an injection and
noticing one are different guarantees, and it had only named the missing one.

---

## S5. CORS reflected any origin with credentials — fixed 2026-11-13

Found while making `test_cors_headers_present` assert something, which it did not.
Removing the CORS middleware entirely left that test green, so the configuration had
never been checked by the suite at all.

### What was wrong

`api.py` registered, in every non-production environment:

```python
cors_origins = ["*"]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                   allow_credentials=True, ...)
```

The assumption in the comment was *"allow all (safe for local testing only)"*. Starlette
does not simply emit `Access-Control-Allow-Origin: *` here — it reflects the caller:

```
$ curl -H 'Origin: https://evil.example' localhost:8000/openapi.json -i
access-control-allow-origin: https://evil.example
access-control-allow-credentials: true
```

A browser refuses a literal `*` combined with credentials. It accepts a *reflected*
origin. So any page the developer visited could issue authenticated cross-origin
requests to their local API and read the response.

### Why it was reachable

The Next.js frontend does not need it. Everything the shipped UI does goes through a
same-origin `/api` rewrite (`frontend/next.config.*`), and Nginx proxies `/api/*` as
well. The wildcard bought nothing and was the only thing making the reflected-origin
behaviour possible.

### The fix

`api.py` now lists the local development origins explicitly — `localhost:3000`,
`3001`, `8501`, and the two `127.0.0.1` spellings — in development, and keeps the
already-restricted list in production. Verified:

```
Origin: https://evil.example     -> (no access-control-allow-origin header)
Origin: http://localhost:3000    -> access-control-allow-origin: http://localhost:3000
```

### The test now tests it

`tests/test_security.py::TestCORS::test_cors_headers_present` asserts that
`app.user_middleware` contains a `CORSMiddleware`, that `allow_origins` is non-empty,
that `"*"` is never combined with `allow_credentials=True`, and that the expected
methods and `Authorization` header are allowed.

**Proof it can fail:** commenting out the `add_middleware(CORSMiddleware, ...)` call
makes it fail with `assert []`. Before this change the same sabotage left it green —
the check and the thing checked had been configured to agree.

---

## S6. Fact length limits were declared and enforced nowhere — fixed 2026-11-13

`modules/security.py` declares `MAX_FACT_KEY_LENGTH = 100` and
`MAX_FACT_VALUE_LENGTH = 1000`, and defines `validate_fact_key` /
`validate_fact_value` directly beneath them. **Neither function was called from
anywhere in the repository.**

`POST /remember` was already capped by its Pydantic model
(`RememberRequest: Field(..., max_length=100/1000)`), so that endpoint was fine — and an
audit that stopped there would have concluded, wrongly, that the limits were live.
They were not, because six other call sites build a fact out of free text:

```python
key, value = content.split("=", 1)
facts.remember(key.strip(), value.strip(), user_id=user_id)
```

`content` there is a chat message, capped at `MAX_MESSAGE_LENGTH = 10_000`. So a
10,000-character message could become a 10,000-character stored fact — ten times the
declared limit, on the path a user actually reaches.

Enforced now in `FactMemory.remember()`, the one function every caller goes through:

```python
ok, err = validate_fact_key(key);   if not ok: raise ValueError(err)
ok, err = validate_fact_value(value); if not ok: raise ValueError(err)
```

Five tests in `tests/test_security.py::TestFactInputLimits` cover the oversized key,
the oversized value (the chat path), an empty value, the unaffected happy path, and the
endpoint's own 422.

### The lesson, again

> A validation function that exists and a validation that runs are different claims.

The declared constant `MAX_FACT_VALUE_LENGTH = 1000` sat in the source next to a
function that would have enforced it. Reading the file, the limit looks real. Running
the code, nothing enforced it — on one of seven paths.

---

## S7. A refresh token authenticated requests — fixed 2026-11-13

**Symptom.** None. Login worked, protected routes worked.

**Cause.** Two auth paths existed and only one checked the token type.

| path | checks `type == "access"`? |
|---|---|
| `get_current_user` (routes using `Depends(get_current_user)`) | **yes** |
| `authenticate_user` (dual JWT-or-API-key) | **no** |

`authenticate_user` verified the signature, read `sub`, and returned the user. A
refresh token is a perfectly valid JWT — same secret, same algorithm, different
`type` and a **7-day** life instead of 30 minutes. So the long-lived credential
worked wherever the short-lived one was meant to be required.

Measured before the fix: `authenticate_user(C(credentials=refresh_token))` →
`vic` **accepted**. A stolen refresh token bought 7 days of access, and refresh
tokens are the credential most likely to be stored in a place that leaks — they
are the one you *have* to persist to stay logged in.

**Fix.** Both paths require `payload["type"] == "access"`. Regression tests cover
the header path, the cookie path, and that a real access token still works.

> **When two functions answer the same question, the weaker one is the answer.**

## S8. Three password-handling bugs, all of which crashed instead of refusing — fixed 2026-11-13

`modules/auth.py` was at **53% coverage**, and the untested half was the half that
touches credentials. Writing the tests found three defects; none was found by
reading the file.

**1. bcrypt's 72-byte limit crashed registration.** `hashpw` raises `ValueError`
beyond 72 bytes — it does not truncate. `RegisterRequest` allows
`max_length=128`, so a 73-character password was accepted by validation and then
blew up in `hash()`. `/auth/register` caught the bare `Exception` and returned a
**500 Internal Server Error**. Measured: 72 ASCII characters fine, 73 raises;
8 emoji fine, 19 emoji (76 bytes) raises.

Fixed by rejecting at the edge with a message that names the limit and the
overflow, **not** by truncating to 72 bytes. Truncating would silently make a
long password and its 72-byte prefix the same password — turning a crash into a
wrong answer is the worse trade.

**2. A corrupt stored hash crashed login.** `bcrypt.checkpw` raises
`ValueError("Invalid salt")` when the stored value is not a bcrypt hash — a
corrupt row, a half-migrated column. Login returned 500 instead of refusing.
`verify_password` now returns `False` for an unusable hash or an over-long
password: an auth check that raises is a 500, an auth check that returns `False`
is a rejected login.

**3. The length requirement could be skipped.** `check_strength` scored 5 checks
and required `score >= 4`, so **any one check could fail — including length**.
`"aA1!"` (4 characters) scored 4/5 and was accepted. Not exploitable through the
HTTP API, because `RegisterRequest.min_length=8` also applies, but that made
`check_strength` a second, weaker gate that a caller reaching `UserManager`
directly would fall straight through. Length is now a hard requirement.

All three lived in **four copies** of the bcrypt calls (`PasswordManager`, the
`User` model, and the two module-level helpers). They are now one implementation
in `modules/database.py`, because four copies is how they drifted.

**Coverage: 53% → 93%** across 61 tests, covering register / login / refresh /
profile lookup / API-key create-validate-expire / the four FastAPI dependencies /
the cookie path. The remaining 7% is the production-import guard, which cannot be
exercised inside an imported module.

> **An error path that raises is indistinguishable from a bug at the call site.**
> Three of these read as "500 Internal Server Error" to a user, and a 500 is what
> the rest of this codebase returns when something is genuinely wrong.

> **A score of 4 out of 5 is a rule about the wrong thing.** It says "most of the
> checks passed", not "the password is long enough".

### What this part did not do

No change to token lifetimes, algorithms or the secret-loading path. The
production guard (`ENVIRONMENT=production` with a default secret raises at import)
was already correct and is unchanged.

---

## 10. The WebSocket ticket: the two properties that make it safe, both untested

**What the design does.** A browser cannot attach an `Authorization` header to a
WebSocket handshake, so the credential has to go in the query string — where it
lands in proxy logs, browser history and `Referer` headers. The mitigation is a
short-lived, **single-use** ticket, issued over HTTPS by `POST /ws/ticket`, and
the endpoint `/ws/{user_id}` that deliberately **ignores the path segment**.

**What was wrong.** Nothing — the code was correct. **Both properties were
untested**, which means either could be "simplified" away without a single red
test:

- Change `pop` to `get` and stop honouring `used` → the ticket becomes replayable,
  i.e. a password with a 60-second life sitting in a log line.
- Trust the path segment → **every user can connect as every other user**, and no
  existing test would notice.

Untested correctness is one edit away from being untested incorrectness.

**Fix.** `tests/test_ws_ticket.py`, 9 tests. The identity test does not settle
for "the connection opened": it wraps `handler.manager.connect` and asserts
**which user id was bound**, with a random UUID in the path so a failure names
the spoofed id.

**Proof (sabotage).** Taking `user_id` from the path fails 1 test; making the
ticket replayable fails 1; removing the expiry check fails 1; opening
`/ws/ticket` to unauthenticated callers fails 1. An invalid or expired ticket
closes with **1008** — fail closed, never a silent anonymous fallback.

**The lesson.** For an auth boundary, "the happy path works" is not the property
you care about. The properties are *replay* and *identity source*, and each needs
its own assertion because each fails silently and independently.

**Not covered.** The Redis path (`pop_json`, `GETDEL` + transactional fallback) is
atomic by construction but is not exercised here — Redis is not configured in
tests, so only the in-memory path runs.


---

## Part B — `modules/security_hardening.py`: 67% → 88%

`api.py` reached 74% and was declared done. Stopping there left the module that
*makes the security decisions* at **67%**, which is a worse place to stop: what
remains in `api.py` is mostly print statements, and what remained here was
defences. `tests/test_security_hardening.py`, 29 tests.

### Finding 1: `CSRFProtection` is dead code — and the app is still protected

The class defines `generate_token`, `validate_token` and `cleanup_expired`,
says "CSRF (Cross-Site Request Forgery) protection" in its docstring, and is:

- **not imported by `api.py`**
- **not called anywhere in the application**
- **not called by any test** (zero references before this file)

So the honest statement is that the app has **no CSRF token mechanism**. What it
does have is `samesite="lax"` on every auth cookie (`api.py` 823, 967–984), which
*is* the standard defence for a same-origin app: the frontend reaches the API
through the Nginx/Next proxy on the same origin, so a cross-site form post does
not carry the cookie. There is a comment at `api.py` 967 explaining exactly this
reasoning, including why `secure` is conditional.

**Status: documented, not changed.** Deleting a class or wiring it in is a
product decision, and the current design is defensible. A guard test
(`test_csrf_is_not_wired_into_the_app`) fails if someone starts using it, so the
next person gets a signal instead of a false sense of coverage.

**A second, smaller problem inside it:** `self.tokens` is never pruned except on
a successful `validate_token` or an explicit `cleanup_expired()`, which nothing
calls. It is unbounded — dead code today, a leak the moment it is wired up.

### Finding 2: upload filenames have no length bound — measured

`sanitize_filename` strips path traversal, separators, control characters, dot-tricks
and reserved Windows names. It does **not** bound length. Measured through the real
endpoint:

```
300-character filename  -> 200  "Added 1 chunks from aaaa..."   file written
5000-character filename -> 400  "Invalid multipart data."       but from the PARSER
```

The 400 is not our validation — it is Starlette refusing the multipart part. So
nothing in this codebase decides how long a filename may be.

Why it matters: most filesystems cap a single name at **255 bytes**, and a
multibyte character costs more than one byte, so a name under 300 *characters*
can still exceed it. The failure would arrive as `OSError: File name too long`
— a **500 during an upload**, not a clean rejection. ext4 happens to allow it
today, which is why nothing broke and nobody noticed.

This is recorded rather than fixed because a length limit is a product choice
(truncate? reject? by bytes or characters?). The test asserts the *current*
behaviour so it cannot change silently, and its failure message says to invert
it when the gap closes.

### Finding 3: the traversal defence is six layers deep — and one line is load-bearing

Sabotage found this. Removing the `filename.replace("..", "")` line entirely
breaks **no test**, and that is not a hole in the tests: the function is
deliberately over-determined. Measured, removing one line at a time:

| line removed | result |
|---|---|
| `os.path.basename` | still safe |
| `replace("..", "")` | still safe — `\.{2,}` collapses dots anyway |
| `replace("/", "")` | still safe — `[^\w\-.]` already maps `/` to `_` |
| `replace("\\", "")` | still safe |
| `re.sub` special chars | still safe |
| `\.{2,}` collapse | still safe |
| **`lstrip(".")`** | **`".htaccess"` survives — hidden file writable** |

So six of seven are redundant *for these inputs* and one is critical. A new test
(`test_hidden_files_cannot_be_written`) covers the critical line, and it is the
two-test catch for sabotage S1b.

This is worth stating plainly: **redundancy is good, but only if the tests target
the outcome.** A test that asserted "the `..` replace ran" would have passed while
the real defence was elsewhere.

### Sabotage results

| # | sabotage | result |
|---|---|---|
| S1 | `replace("..", "")` removed | **SURVIVED — correctly.** Redundant line; S1b covers the real one |
| S1b | `lstrip(".")` removed | CAUGHT (two tests) |
| S2 | lockout no longer escalates | CAUGHT |
| S3 | Redis state ignored | CAUGHT |
| S4 | local mirror never updated | CAUGHT |
| S5 | lockout never reported | CAUGHT |
| S6 | audit log unbounded | CAUGHT |
| S7 | CSRF token replayable | CAUGHT |
| S8 | expired CSRF token kept | CAUGHT |

**8 of 9 mutations caught, 1 correctly survived.**

### What was verified about degradation

The Redis branch is the one a multi-replica deployment uses, and nothing reached
it before. Now asserted:

- a lockout recorded by **pod A is seen by pod B** (empty local mirror, shared
  Redis) — without this, an attacker retries against a different replica and the
  per-process counter never fills
- a **failed Redis read or write degrades to the local mirror** and never
  propagates into a login request
- `REDIS_URL` set but **nothing listening** still locks out (the realistic
  degraded deployment, not a synthetic exception)

### Two of my own errors in this part

**1. I invented the `AuditLogger` API.** I wrote `log.log_event(...)` and
`log._trim()`. Neither exists; the real methods are `log_login_attempt`,
`log_registration`, `log_file_upload`, `log_rate_limit_violation`,
`log_suspicious_activity`, `log_tool_call`, and the bound is inside `_write_log`.
Two tests failed and I read the source instead of the docstring.

**2. I asserted a PII policy the code deliberately does not have.** I asserted
that an email address was redacted out of the audit trail; it was not.
`modules/pii.py` states the scope explicitly: SSNs and Luhn-valid card numbers
are redacted, and *"names, addresses, emails and phone numbers are deliberately
left alone, because redacting them would destroy the usefulness of the logs and a
regex cannot reliably tell a name from a noun"*. **The code was right and my test
was wrong.** Rewritten to assert what is actually promised, and the Luhn
behaviour is now covered too (an ordinary 16-digit id is not mangled).

**3. I stubbed away the code under test.** The first version of the bounded-log
test replaced `_write_log` with a lambda that did the trimming itself — so it
asserted that *my stub* trimmed a list and could never fail for the real code.
Found by reading it back before committing; rewritten to run the real
`_write_log` and stub only its two side effects (file, Redis).

**4. I built a sabotage that could not happen.** An early probe replaced
`_get_redis` with a function that *raises*, printed `RAISED: ConnectionError`,
and I briefly reported a defect. The real `_get_redis` catches everything and
returns `None` — my stub produced a shape the real function cannot produce. The
correct probe sets `REDIS_URL` to a dead port; that one passes.

### Measured after this part

```
tests/test_security_hardening.py       -> 29 passed
security_hardening.py coverage         -> 88%   (was 67%)
full suite (CI flags)                  -> 1166 passed, 6 skipped, 1 xfailed
TOTAL (--cov=., CI gate 74)            -> 88%
sabotage                               -> 8/9 caught, 1 correctly survived
```


---

## Part C — `modules/pg_store.py`: 66% → 92%

`pg_store` is the module that writes chat history to the **real** database. The
rest of the test suite runs on in-memory SQLite, so this is the layer where a
defect can be invisible in CI and live in production.

### The gap was structural, not incidental

`tests/test_pg_store.py` is 242 lines and 22 tests. It is *thorough* — and every
single test ends the same way: the pool raises, and the function returns
`None`/`[]`/`False` as promised. That is genuinely valuable (it is why chat
survives an outage) but it means:

> **not one SQL statement in this module had ever been executed by a test.**

A broken `WHERE c.user_id = $2` would still return `None` against an unreachable
database. Every existing test would stay green. The SQL itself — owner checks,
conflict handling, ordering, bind order — was unverified.

`tests/test_pg_store_sql.py` (13 tests) drives the real functions against a
recording fake pool. It does **not** prove the SQL is valid PostgreSQL; only a
server can. That limit is stated in the file rather than implied away.

### Finding 4: a user can write into another user's conversation (measured)

`add_message` runs:

```sql
INSERT INTO conversations (id, user_id, title, ...) VALUES ($1, $2, $3, ...)
ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
INSERT INTO messages (id, conversation_id, role, content, ...) VALUES ($1, $2, ...)
```

**There is no owner predicate.** If user B supplies user A's conversation id:

- the conflict clause fires on **A's row**; `user_id` remains A's
- the `messages` INSERT then writes **B's content into A's conversation**

A's conversation id is not a secret. The default is
`uuid5(DNS, f"llama-agent-default:{user_id}")` — computable by anyone who knows
A's user id, which appears in A's own JWT and in URLs the frontend builds.

**What was checked before calling this a defect:**

| question | answer |
|---|---|
| can B **read** A's history? | **No.** `get_messages` joins `c.user_id = $2`; verified by test |
| can B **write** into A's conversation? | **Yes** — measured against the recording fake |
| can B rename A's conversation? | No — the title update is `WHERE id = $2 AND title IN ('New chat','New Chat')` |
| what does B gain? | nothing readable: `get_messages(B, ...)` returns `[]` |

So this is a **write-only integrity defect**, not an exfiltration one. B can
inject content into a conversation B cannot read, and bump its `updated_at`,
which moves A's conversation to the top of A's own sidebar. A also cannot
detect it: the message looks like their own history.

**Status: recorded, not fixed.** The correct fix is an owner predicate on the
conflict clause (`ON CONFLICT (id) DO UPDATE ... WHERE conversations.user_id =
EXCLUDED.user_id`) or a pre-check, and it is a schema-and-behaviour change that
belongs with the product owner. The test asserts current behaviour and its
failure message says to invert it when the fix lands.

### Finding 5: the auto-title can be raced, and the ordering has no stable tie-break

Minor, both recorded rather than changed:

- **Auto-title is a separate statement** after the insert (`UPDATE ... WHERE title
  IN ('New chat','New Chat')`). Two concurrent first messages both see the
  default title; the second update wins. Harmless (the title is cosmetic) but it
  is not atomic with the insert it describes.
- **`ORDER BY m.created_at ASC, m.id ASC`** — the tie-break is `m.id`, a random
  UUID, so two messages written in the same timestamp resolve in random order.
  Removing the tie-break entirely breaks nothing either way (sabotage P4
  survived); `created_at` is `NOW()`, which is transaction-start time in
  Postgres, so two messages in one transaction share it. Extremely unlikely with
  one row per transaction, and not worth changing without evidence.

### Sabotage results

| # | sabotage | result |
|---|---|---|
| P1 | owner check removed from the read query | CAUGHT |
| P2 | auto-title overwrites a user-chosen title | CAUGHT |
| P3 | title no longer capped at 80 characters | CAUGHT |
| P4 | ordering tie-break dropped | SURVIVED — redundant, as above |
| P5 | message row bound to the wrong conversation | **SURVIVED first, then CAUGHT** |
| P6 | assistant replies retitle the conversation | CAUGHT |

**P5 is the instructive one.** My first version asserted the *conversation*
argument but never the *message id*, so swapping `$1` and `$2` in the VALUES list
went unnoticed — and that swap means two messages share a primary key. Fixed by
adding `test_a_message_row_gets_a_fresh_id_and_the_right_conversation`, which
also asserts that a second message gets a **different** id.

### A process failure worth recording

P5's sabotage was left **applied on disk** for a moment: the restore ran, then a
later command re-applied it, and the next coverage run failed with the sabotage
still in the file. Caught because the failure named the sabotaged SQL
(`VALUES ($1, $1, $3, $4, NOW())`), not because I remembered. The rule already
in `RUNNING_TESTS.md` — diff against the backup after every sabotage — is what
identified it in one step.

### Measured after this part

```
tests/test_pg_store_sql.py            -> 13 passed
pg_store.py coverage                  -> 92%   (was 66%)
existing test_pg_store.py             -> 22 passed, unchanged (degradation paths)
full suite (CI flags)                 -> 1179 passed, 6 skipped, 1 xfailed
TOTAL (--cov=., CI gate 74)           -> 88%
sabotage                              -> 5/6 caught, 1 redundant (P4)
```

---

## S9. Four open findings, measured during the documentation audit

Four defects found while checking `ARCHITECTURE.md` and `docs/REQUEST_FLOW.md`
against the code. **Recorded here, not fixed** — no behaviour was changed. Each
entry names the exact line, what is wrong, and what "fixed" would mean.

### S9.1 `get_client_ip` trusts a header the client controls

`modules/security_hardening.py:637-650`:

```python
forwarded = request.headers.get("X-Forwarded-For")
if forwarded:
    return forwarded.split(",")[0].strip()      # first entry
```

Two independent problems:

1. **No trusted-proxy check.** The header is believed unconditionally, so a
   client that reaches the app without nginx (port `8000` is published —
   `docker-compose.yml:157`) chooses its own rate-limit key.
2. **Reads the wrong end of the list.** nginx sets
   `X-Forwarded-For $proxy_add_x_forwarded_for` (`config/nginx/nginx.conf:159`,
   `:180`, `:189`), which **appends**. A client sending
   `X-Forwarded-For: 9.9.9.9` produces `9.9.9.9, <real-ip>`; `[0]` is the forged
   value, `[-1]` is what nginx observed.

**Reachable through the front door.** `api.py:746` rate-limits on
`get_client_ip`, and `modules/security_hardening.py:275-300` keys brute-force
lockout on the same function. A caller varying the header gets a fresh bucket per
request, so **both the application rate limit and the login lockout are
bypassable without touching port 8000**. nginx's own `limit_req` is *not*
affected — it keys on `$binary_remote_addr`.

**Fixed would mean:** read `[-1]` when the peer is a trusted proxy, `request.client.host`
otherwise; add a test that forges the header and asserts one bucket.

### S9.2 The WebSocket has no `Origin` check

`api.py:1173-1219`. The ticket path is safe by construction — a ticket can only be
obtained from `POST /ws/ticket` (`api.py:1163`), which is a normal CORS-gated
request. The **cookie fallback** (`api.py:1212-1218`) is not: a browser attaches
`access_token` to the handshake automatically, and WebSocket handshakes are not
subject to CORS. A hostile page can therefore open an authenticated socket.

Severity is bounded: an unknown cookie yields a `guest-` id, so the attack
requires a logged-in victim to visit the attacker's page.

**Fixed would mean:** reject the handshake unless `websocket.headers["origin"]`
is in the allowed list.

### S9.3 `/health` is a hardcoded string used as both liveness and readiness

`modules/monitoring.py:226-238` returns `{"status": "healthy", ...}` as a
**literal**, with the docstring stating it is "about the process being up, not
about traffic". `api.py:910-930` adds fields that are mostly constants; the only
measured one is `llm_key`.

`k8s/api.yaml:255` and `:263` use the same endpoint for **both** probes, so a
replica whose dependencies are unreachable is reported **Ready** and receives
traffic. Observed consequence: a replica that fell back to a pod-local Chroma
store (S9.4) passes readiness and serves from a split index.

**Fixed would mean:** split `/health/live` (process only) from `/health/ready`
(checks Chroma, Postgres, Redis).

### S9.4 The Chroma fallback silently recreates the multi-replica bug

`modules/vector_store.py:433-450`. When `CHROMA_SERVER_URL` is set and
`heartbeat()` fails, the code prints `[WARN]` and **returns a pod-local
`PersistentClient`**. `get_vector_store()` is called **once**, at startup
(`api.py:543`), so the pod stays on the private store for its whole lifetime even
after chromadb returns.

In a single-replica deployment this is a graceful degradation. In a
multi-replica deployment it is a **silent data split**: one replica serves
documents another cannot find, with no error and no failed health check.

**Fixed would mean:** make the fallback fatal when more than one replica is
configured (e.g. `CHROMA_REQUIRED=1`), and expose which client is in use on
`/health/ready`.

### S9.5 `require_api_key` returns 503 with no `Retry-After`

`api.py:897-907`. The status code is right — the fault is the server's, and it
is retryable. The response carries no `Retry-After`, so clients have to guess.

### S9.6 `"what's new"` routes an ordinary document question to a research agent

`modules/routing.py:257-273`. `RECENCY_KEYWORDS` (added to the automaton as
`research`) contains `"what is new"` and `"what's new"`. Measured:

```
route_multi_agent("what's new with my documents?")   -> (True, 'research')
route_multi_agent("what files do I have?")           -> (False, 'research')
```

The first is a document question sent to a research agent, because "new" read as
news. `api.py:1752-1756` documents the same class of collision for `"what is
my name?"` and resolves it by ordering the name check first; there is no
equivalent guard for document phrasing.

**Fixed would mean:** remove those two phrases from `RECENCY_KEYWORDS`, or run
`is_document_question()` first for any message naming an uploaded file (it partly
does — `_DOC_QUESTION_REF_WORDS` does not include "new").

---

## What this part did not do

It did not change behaviour. No file under `modules/`, `api.py`, or
`config/nginx/` was modified — only docs and comments, so that the next reader
is told the truth about the system as it currently runs.

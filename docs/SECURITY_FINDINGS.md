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

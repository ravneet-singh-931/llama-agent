# My own errors

This file exists because it is the most useful thing in the audit. Every entry is
a mistake **I** made while auditing this repository, what it cost, and the rule
that came out of it. They are kept rather than tidied away.

The reason: an audit is a claim about someone else's work. If the auditor's own
errors are not recorded, a reader has no way to judge how much of the rest was
checked. Every one of these was caught — mostly by a test going red for a reason
I did not expect — and that is exactly why they belong here.

---

## 1. I called a working job "decorative"

**What I said.** The `docker` job in CI looks like it does nothing — it builds an
image that is never pushed, so it is effectively fake.

**What was true.** It **builds on every main push**. Only the *push* is
conditional (`push: ${{ secrets.DOCKER_USERNAME != '' }}`), and the login step has
`continue-on-error: true`. `gh run view 43422024177 --json jobs` shows every
docker step genuinely ran.

**How I caught it.** I checked the job's own step log instead of reasoning from
the YAML. The YAML is what it *might* do; the run is what it *did*.

**The rule.** Never call a job decorative. `gh run view <id> --json jobs` — read
what ran, not what you think the config implies.

**A related limit.** `gh secret list` returns **403** in this environment, so
whether a secret is set **cannot be verified here**. That means the consequence of
a skipped push is unverifiable from this sandbox, and it is written up as a risk
rather than fixed.

---

## 2. I repeated "mostly error branches" without counting

**What I said.** The remaining `api.py` coverage gap is mostly error handling, so
filling it is low value.

**What was true.** Measured:

```
missing lines inside `except` blocks : 106 / 588  (18%)
everything else                      : 482
```

**18%.** The other 482 lines were **untested features** — and the largest group was
conversation CRUD, where an uncaught bug is a **silent data leak** rather than an
ugly error page.

**How I caught it.** I counted the missing lines that actually fall inside an
`except` handler with an AST walk, instead of trusting the phrase I had been
writing for weeks.

**The rule.** A characterisation repeated three times is still not a measurement.
Count it, then write the number.

---

## 3. A sabotage that patched the wrong line, and looked like a pass

**What I did.** To prove my `/facts` test was real, I removed the user filter:

```python
s.replace("all_facts = facts.get_all_facts(user_id=user_id)",
          "all_facts = facts.get_all_facts()", 1)
```

The test **passed**. I nearly recorded that as "the test is weak."

**What actually happened.** That line appears **five times** in `api.py`. `replace(..., 1)`
patched the **first** occurrence — a completely different handler. `/facts` was
never touched. The test passed because **I had not changed the program.**

**The fix.** Patch by line number after asserting the line's content:

```python
assert "get_all_facts" in lines[2214]
lines[2214] = "        all_facts = facts.get_all_facts()\n"
```

The test then failed immediately, as it should have.

**The rule.** A sabotage that does not change the code proves nothing. Assert the
pattern was found **and** that it is the occurrence you meant. With a string that
occurs more than once, position is part of the claim.

---

## 4. I assumed a passing sabotage meant a weak test — the code was just safer

**What I did.** Removed the owner argument from `delete_conversation`:
`memory.delete_conversation(user_id, conversation_id)` →
`memory.delete_conversation(None, conversation_id)`. All 6 tests still passed.

**What was true.** Ownership is checked **twice**. `api.py` passes `user_id`, and
`memory.delete_conversation` re-checks `conv["user_id"] != user_id` itself.
Passing `None` was caught by the inner guard. Removing **that** one does fail the
test.

**The rule.** Two layers of defence mean a single sabotage may be absorbed. Before
concluding a test is weak, check whether a second guard is doing the work — and
then prove *that* one. It is worth knowing which layer is load-bearing.

---

## 5. I reported a finding that was already documented

**What I did.** Presented the two feature-flag systems as a new discovery.

**What was true.** `README.md` already records it, with the numbers
(7 of 8 flags unread) and the deleted flag list.

**The rule.** Search the findings directory before writing a finding. Re-reporting
a known issue as new work is how a backlog stops meaning anything: the list grows,
the reader loses trust in the count, and nothing is actually finished.

---

## 6. I wrote a test whose fixture already satisfied its own assertion

**What I did.** Wrote a test for the OTel span exporter whose setup created the
span the assertion then looked for. It could not fail.

**How I caught it.** Reordered the data so a no-op would make it fail, and it did.

**The rule.** Set the fixture up so that a **no-op fails**. If the setup already
satisfies the assertion, the test is measuring the fixture.

---

## 7. "Returns a string" is not a timeout assertion

**What I did.** Asserted that a deadline-guarded call returned a string.

**What was true.** Returning *any* string satisfies that — including a
success message produced after hanging forever. A timeout test must assert on
**elapsed time** relative to the deadline.

---

## 8. `set -e` inside a sabotage script killed the restore step

**What I did.** Wrote a sabotage script with `set -e` and `&&` chaining. The
sabotage made the test command fail (the point), which aborted the script **before**
the `cp` that restores the original file. The repo was left sabotaged.

**The rule.** Always `cp` the backup back, unconditionally, and verify with
`diff -q` afterwards. A verification script that can leave the tree broken is a
worse risk than the bug it is investigating.

---

## 9. I counted lines instead of references

**What I did.** Reported `PhoenixTracer` as dead because it is constructed nowhere.

**What was true.** The live tracing integration is `api.py`'s lifespan under a
different name. The capability was wired; the class was a leftover shell.

**The rule.** Low coverage is not dead code. **Grep for the capability, not the
class name**, and check the wiring before raising the alarm.

---


## 17. I asserted a policy the code deliberately does not have

**What I did.** Tested that an audit-trail entry had its PII redacted, and asserted
the user's **email address** was gone. It failed: `contact bob@example.com or
[REDACTED]`.

**What I did next was the mistake.** My first instinct was that the redactor had a
bug. Reading `modules/pii.py` showed the opposite: SSNs and Luhn-valid card
numbers are redacted, and *names, addresses, emails and phone numbers are
deliberately left alone* — the module says why ("redacting them would destroy the
usefulness of the logs and a regex cannot reliably tell a name from a noun").

**Why it matters.** I had written a test from what I assumed a security module
*should* do, then would have "fixed" production code to match my assumption. A
test that encodes a preference is not a test; it is an opinion with a failure
message attached. The rule: read the stated scope before asserting on behaviour,
and when a test fails, consider that the test may be the wrong one.

---

## 18. I stubbed away the code under test and called it covered

**What I did.** To avoid touching disk, I replaced `_write_log` with a lambda that
appended to the list *and did the trimming itself*. The test then asserted the
list was bounded — testing my stub, not `_write_log`. It could never have failed
for the real code.

**How I found it.** Reading the test back before committing, not by a tool.

**The rule.** Stub the *edges* (a file, a socket, a clock), never the *subject*.
If a stub has to contain the logic you are checking, the thing you are checking
is missing.

---

## 19. I built a sabotage that could not happen and reported a defect

**What I did.** Probed the brute-force degradation path by replacing
`_get_redis` with a function that raises `ConnectionError`. It raised, I saw
`RAISED: ConnectionError`, and I briefly reported "brute-force protection crashes
when Redis is down".

**What was actually true.** The real `_get_redis` wraps everything in
`try/except` and returns `None`. My stub produced a return shape the real function
*cannot* produce. The correct probe — set `REDIS_URL` to a dead port — locks out
normally.

**The rule.** A stub must be able to exist. If the fake behaves in a way the real
implementation cannot, the experiment measures the fake.

---

## 20. I asserted an argument that could not distinguish the bug I was testing for

**What I did.** Sabotaged the `messages` INSERT by changing `VALUES ($1, $2, ...)`
to `VALUES ($1, $1, ...)` — binding the message id where the conversation id
belongs, which would make two messages collide on a primary key. My test asserted
`msg_args[1]` (the conversation) and passed. **The sabotage survived.**

**Why.** `$1` and `$2` are the *first and second* arguments. I checked the second
one, and the sabotage broke the first. It would only have shown up against a real
server, as a duplicate-key error.

**The rule.** Check every bound value, and check that two calls produce two
different ids. A test that pins one argument of a four-argument statement is
pinning a quarter of the contract and looks like it pins all of it.

---

## What these have in common

Most of them were **a check that appeared to pass**. Not a wrong answer — a
missing measurement that looked like a completed one.

That is the same failure the audit found in the repository itself: tests that
asserted nothing, flags read by nothing, validators called from nowhere. The
auditor was making the identical mistake, in the same way, at the same time.

Which is the argument for the method the whole audit ended up using: **break it
and watch it fail.** Reading code, re-reading my own notes, and reasoning from
configuration all produced confident, wrong answers above. Only sabotage, or
counting, or reading the actual run, produced the right one.

---

## 10. I patched a name that did not exist, six times, and called it a test

**What I did.** Wrote six tests for the startup uploads re-index loop, each
patching `api.SimpleRAG`:

```python
import api as _api
monkeypatch.setattr(_api, "SimpleRAG", SpyRAG, raising=False)
```

`raising=False` meant no error if the attribute was absent — and it was absent.
The lifespan does its import **inside the function**:

```python
async def lifespan(app):
    ...
    from modules.rag import SimpleRAG          # api.py:504
    app.state.rag = SimpleRAG()
```

So the name lives in `modules.rag`, not on `api`. All six tests patched nothing,
ran the real `SimpleRAG`, re-indexed nothing, and failed — for the wrong reason.
Fixing the target to `modules.rag.SimpleRAG` made all six pass.

**The rule.** `raising=False` turns a patch that matches nothing into a silent
no-op. When a patch is the setup of a test, assert the target exists first —
otherwise "the test fails" and "the patch missed" look identical.

**The same family as three earlier entries** (#3 patched the first of five
identical lines, #6 asserted on a fixture that already satisfied itself, and the
sabotage that produced an `IndentationError` and so tested nothing). In every
case **a check ran against something other than what it claimed to check**, and
the failure looked like a result.

## 11. I "confirmed" a bug that was dead code

**What I did.** Found `streamed += _tail` in the agent branch of `/chat/stream`
and reported it as a live `UnboundLocalError` — `streamed` is assigned only in the
team branch. The static reasoning was correct: the name is a team-branch local.

**What was true.** The line is **unreachable**. It sits behind a second
`_redactor.flush()`, and `flush()` swaps the buffer out and returns it, so the
second call is always empty. Verified by flushing twice after feeding a card
number: first `'4111 1111'`, then `''`.

**How I caught it.** I wrote the test that was supposed to prove the crash, and it
passed. Instead of deleting the test I checked *why* — and the "why" was that the
branch cannot run.

**The rule.** "This variable is undefined on this path" is a hypothesis about
reachability, and reachability has to be measured. Confirming a bug requires
making it happen, not showing that it could.

## 12. I compared a mixed-case expectation to a lowercased string

**What I did.** Wrote a parametrised test:

```python
assert expected in plain.lower()      # expected == "your name is Ravi"
```

`"your name is Ravi"` cannot be a substring of `plain.lower()`, because the
lowercased haystack contains `ravi`, not `Ravi`. The test failed on the *name*
case and passed on the others, which looked like a route bug.

**What was true.** The routes were fine. `expected` needed `.lower()` too. One
line, and it was mine — but it cost a full run and a detour because a failing
test is not automatically evidence about the code under test.

**The rule.** When the assertion compares a literal to a lowercased value, the
literal is also a suspect. Lowercase both sides, or assert on the canonical form.

## 13. My fix had the same bug as the code it fixed

**What I did.** Found `/chat` answering a failed recall with a checkmark:

```
✅ nothing-here: I don't remember anything about 'nothing-here'
```

Cause: `FactMemory.recall()` returns the sentence `"I don't remember anything
about '<key>'"` on a miss — a truthy string — so `if value:` always took the
success path. I extracted a helper to fix it in one place, and wrote:

```python
if value:
    return f"✅ {key}: {value}"
```

**The same test. In the fix.** The helper reproduced the exact defect it was
created to remove.

**How I caught it.** Not by reading it back. I sabotaged `/chat` to restore the
old line, and the parity test *passed* — because both routes now returned the
identical ticked string. They agreed, and both were wrong. That made me look at
the helper, and it had the bug too.

**The rule.** A parity assertion only proves two things are *equal*, never that
either is *right*. Assert the property you actually want ("no tick on a miss"),
then assert parity as a second condition. And when a fix is extracted into a
helper, the helper needs the same adversarial test the original bug got.

## 14. I asserted a number before reading the code that produces it

**What I did.** Wrote a test for the Redis-present path of the counter fix and
asserted that two dispatches against a cold Redis would leave `2`:

```python
assert shared["handled"].get("coder") == 2
```

It failed with `101`.

**What was true.** `_shared_state` seeds Redis from the local copy when the
store is empty — `99 + 2 = 101`. That is deliberate and correct: a replica that
has just started should join the running total, not reset it to zero. The code
was right; my expectation was invented.

**Why it matters.** This is the same failure as entry 12, in a different
costume: I wrote the number I *assumed* the correct behaviour would produce
instead of deriving it from the implementation and then checking the derivation
against the run. In a test, an invented expectation does not merely fail — if it
had passed for the wrong reason it would have locked in a fiction.

**The rule.** An expected value in a test is a claim about the code. Make the
claim from the code, then let the run confirm it. If the run disagrees, the test
is at least as likely to be wrong as the program.

## 15. I tested that the session worked, not that it was refreshed

**What I did.** Wrote a test for `/auth/refresh` that called `/auth/me` afterwards
and asserted `200`. It passed. Then I sabotaged the route by replacing the
`_set_auth_cookies(...)` call with `pass` — and the test **still passed**.

**What was true.** Login had already put a perfectly valid access token in the
cookie jar. `/auth/me` was authenticating with *that* token, not with any token
the refresh had produced. The route could have been doing nothing at all and my
test would have called it healthy.

**How I caught it.** Sabotage, not review — and then it took two attempts to fix
properly. My first repair checked the cookie jar for a token, which still cannot
distinguish a fresh cookie from a leftover one. The version that works asserts on
the **response**: `set-cookie` must contain `access_token=`.

**The rule.** When a component's job is to *produce or replace* something, assert
that the new thing was produced. Asserting that the system still works afterwards
tests everything except the component.

## 16. I wrote a test that hangs instead of failing, and fixed it twice wrongly

**What I did.** Tested that a WebSocket with an invalid token is closed with code
`1008`, by calling `ws.receive_text()` and expecting a disconnect. Then I
sabotaged the close to prove the test guarded it.

**What happened instead.** The test did not fail. The **run sat until the command
timed out at 300 seconds**. There was nothing to report, because `receive_text()`
waits for a server that will never speak and the assertion is never reached. A
test that hangs is worse than no test: it blocks the suite and proves nothing.

**My first two fixes were also wrong.**

1. `asyncio.wait_for(ws.receive_text(), timeout=2)` — the timeout never fires,
   because starlette's receive is a blocking call, not a cancellable await.
2. A worker thread with a 5s ceiling — the assertion now fired, but the process
   **still hung on teardown**: the never-accepted socket kept the TestClient
   portal alive.
3. Thread plus a `finally` that closes the connection from the main thread —
   fails in ~2s and the process exits.

**The rule.** Any test that waits on I/O needs a bound, and the bound must be in
whatever layer actually blocks. "It passed" and "it would fail if broken" are
different claims, and only the second one needs a bounded failure to be worth
anything.

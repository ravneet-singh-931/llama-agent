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

## What these have in common

Eight of the nine were **a check that appeared to pass**. Not a wrong answer — a
missing measurement that looked like a completed one.

That is the same failure the audit found in the repository itself: tests that
asserted nothing, flags read by nothing, validators called from nowhere. The
auditor was making the identical mistake, in the same way, at the same time.

Which is the argument for the method the whole audit ended up using: **break it
and watch it fail.** Reading code, re-reading my own notes, and reasoning from
configuration all produced confident, wrong answers above. Only sabotage, or
counting, or reading the actual run, produced the right one.

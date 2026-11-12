# Post 3 — The Detector That Could Not Detect

**Status:** ready to publish. **2,997 characters**.
**Image:** none. See the note at the bottom.
**Plain text** — paste as-is.

---

I'm building a multi-agent AI assistant on Groq's free tier. Its eval suite: 60 cases, nightly, each run diffed against the last.

My regression detector ran for weeks and could not detect a regression.

When I traced it, I found four bugs with one shape — and I only saw the pattern after the third.

Three of them, briefly

The compare step crashed with KeyError: 'no_meta' on a run that produced a real number (83.3%). no_meta failed once in the baseline, zero times today, so the key existed on one side only. It crashed precisely because the check got better: "the check that used to fire, doesn't" was the comparison that couldn't run.

A red nightly compared a report graded by the old check logic against one graded by the new. Worst of its defects: the fingerprint hashed prompt text, so the largest change to the grading logic in history left it identical. The one mechanism built to flag a diff across a change stayed silent for exactly that diff. It now hashes the checks, judge, dataset and threshold; an incomparable diff is reported, not failed.

And when the judge call failed, the case was decided by rule checks alone, and could pass. A broken judge inflating your score is worse than no judge: it gives you confidence in a number that isn't real.

4. The timeout one level too low

Run 43029519652: 89 minutes of eval step, 11 cases, 17.3 seconds of actual work. Then case 12 never returned and the 90-minute job timeout killed it.

The deadline was checked between stream events. A silent stream has no events to check between. And the stream object is created on the calling thread first — if the request blocks before the first token, there is no event and nobody holds a clock.

Third time in the same file: per-future, so the Nth future got 150s on top of the first N-1; then between events; then on a thread that blocks before any event.

Fix: the guarantee moves up a level. A daemon worker runs the case; the runner stops waiting at timeout_s and abandons it, reported not graded and named.

Three more holes fell out. The recorder never restored on an abandoned case, so a tool patch stayed installed and corrupted every later case's calls. The judge was a second unbounded call after the deadline. Five rec.calls dereferences would have raised AttributeError.

A 5-case suite where #2 hangs now finishes in 2.9 seconds.

The pattern

Every time, the guard was real, tested, and pointed at the wrong thing.

A guard you have not watched fire is not a guard.

Then the fix for the false alarm was itself a false fix. I required 8 cases per category and a 12.5-point move. safety had exactly 8 cases and fell exactly 12.5. 12.5 >= 12.5, so it still fired. Only replaying the real numbers caught it.

The axis wasn't sample size. It was agreement: a category going down counts only if the pass rate went down too.

A fix must be tested against both failures it names — the one it stops, and the one it must still catch.

#LLMOps #AIEvaluation #SoftwareEngineering #Testing

---

## Corrections I made to the draft

- **Draft was 4,319 characters** — 1,319 over the limit.
- **Structure fixed.** The draft numbered four bugs but only labelled three, then said "the third
  time in the same file", which refers to a *different* three. Restructured: three quick bugs,
  then bug 4 with room to breathe.
- **"7-row table" appeared twice for two different tables** (findings 18 and 19 have 6 and 7
  rows). Removed the number; it belongs in Post 4, which is correct at seven.
- **Case name:** the draft said `safety-english`. Both `safety-english` and `safety-english-code`
  exist in the dataset — check which hung before publishing.
- **Hashtag `#Concur` was truncated** (probably `#Concurrency`). Dropped.

## Why no image

Posts 1 and 2 carried data. This one is about time and an absence — the insight is that the
timeout did not fire. A chart of "nothing happened" is a slide saying nothing happened.

If you want one, the honest visual is a timeline bar: 89 minutes, with 17.3 seconds highlighted,
the rest labelled "waiting for a stream that never spoke".

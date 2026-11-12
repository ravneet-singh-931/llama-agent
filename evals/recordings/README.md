# Eval recordings

A full 60-case run needs **~314,330 input tokens**. The Groq free tier allows
**200,000 a day**. A full run therefore cannot finish in one day, which means
nobody who clones this repo can reproduce the numbers in the README by running
the suite themselves — they exist in CI logs and nowhere else.

A **recording** fixes that. Every model reply and every tool result is saved to
disk once, and replay serves them for zero tokens in a few seconds.

```bash
# Once, spending quota. Writes the recording.
python evals/run.py --record evals/recordings/2026-11-12-full

# Forever after, free. No provider calls at all.
python evals/run.py --replay evals/recordings/2026-11-12-full
```

## What it is, and what it is not

**It is** proof that the grading pipeline is deterministic: the same answers in
produce the same report out. It runs in seconds, so it belongs in CI on every
pull request, and it fails loudly when a change to the grader moves a number.

**It is not** a test that the agent answers well today. The answers are frozen.
That is what the nightly is for, and it still costs quota. Replay checks the
*measuring stick*; the nightly checks the *thing being measured*.

## What gets recorded

| Kind | Key | Why |
|---|---|---|
| Model replies | model + messages + tools + sampling | the answer itself |
| Streaming chunks | same | the streamed path is a separate code path |
| Tool results | tool name + arguments | a replay that called Tavily would spend quota and return a different answer |
| The date | in `manifest.json` | `today_str()` is in *every* system prompt; unpinned, a recording made on Tuesday misses every key on Wednesday |

## Guarantees the tests enforce

- **A replay never calls the provider.** A miss raises `RecordingMiss` instead
  of falling through to the network. A replay that quietly hits the provider
  spends quota and returns a different answer under the same name.
- **A replay never runs a real tool.** Same reason.
- **First write wins.** Re-recording the same request does not overwrite what
  is already saved, so a committed recording cannot change meaning silently.
- **`off` is a pure no-op.** The nightly's behaviour is unchanged.

## Layout

```
evals/recordings/2026-11-12-full/
  manifest.json     run date, call counts
  llm.jsonl         one line per model call
  tool.jsonl        one line per tool call
```

Each line is `{"key": "<16 hex>", "value": ...}`. Plain JSONL so a recording is
reviewable in a diff — you can see a prompt change made a key disappear.

# Case studies

The project in four parts, each with the document that proves it and the numbers that
back it. If you read one file to judge whether this repo is worth your time, read this
one — it is an index, and every claim in it links to the evidence.

> **Where the work stands right now:** [`ROADMAP.md`](ROADMAP.md) tracks every part,
> what is done, what is open, and the order the rest will be done in. This file
> describes the four parts that are finished; the roadmap is honest about the ones
> that are not.

**The organising idea:** the interesting thing about this project is not that it has
five agents, a RAG pipeline and a Kubernetes deployment. It is that **every part of it
was measured, and the measurement was wrong at first**. Four separate parts, four
different kinds of wrong.

| Part | Document | Findings | The headline number |
|---|---|---|---|
| **1. Measurement** | [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | 20 | the suite published **35%** when the real figure was **72.4%** |
| **2. The agents** | [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | 5 fixed, 4 open | a team step hung for **309 s** against a 300 s limit |
| **3. Observability** | [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | 4 | a 2.0 s stream measured as **0.002 s** |
| **4. RAG** | [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | 6 | the "cross-encoder reranker" was keyword overlap |
| **5. Security** | [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) | 4 | the safeguard was on the endpoint nobody uses |
| **6. The dark modules** | [`DARK_MODULES.md`](DARK_MODULES.md) | 23 dead names | "undocumented" had been read as "unused" — and it is a third of the surface, not all of it |
| **7. The cluster** | [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | 5 checks | a nightly backup failed **3 nights** because the PC is off at 02:00 |

---

## Part 1 — Measurement

**What was built.** A 60-case eval suite: rule checks, figure grounding, and an LLM
judge, run nightly in CI and diffed against the previous run.

**What broke.** 18 findings, and **16 of them were in the harness, not the agent.** The
measuring stick was wrong far more often than the thing being measured.

**The three that matter:**

- **35% was not a score.** The free tier ran out at case 29; 31 unanswered cases entered
  the denominator as failures. Among cases that ran it was 72.4%. *The suite published
  its own outage as its quality.* It now refuses a pass rate below 90% coverage.
- **The best run in the project's history was reported as a regression.** Pass rate
  60.0 → 76.7%, judge 4.32 → 4.42, p95 119.8 → 85.8 s — and two categories slipping by
  one case each turned the verdict red. It stayed green only because it was dispatched
  by hand.
- **89 minutes of wall clock for 17.3 seconds of work.** A per-future timeout where a
  per-level deadline was needed, for the third time in the same file.

**Reproducible now.** A full run needs ~314,330 input tokens against a 200,000/day cap,
so it could never be re-run from scratch. It records once and replays for zero tokens,
and a replay needs no API key:
`python evals/run.py --replay evals/recordings/<run>`.

---

## Part 2 — The agents

**What was built.** Five specialists (research, coder, writer, planner, analyzer), a
`@team` orchestrator that runs them as a DAG, a critic, and shared prompt rules.

**What broke.** Four findings, all fixed, all with tests. None was a model quality
problem; every one was a missing input or a misplaced boundary.

| # | The agent answered | Without |
|---|---|---|
| 1 | a critique | the sources and the date — so it correctly called a right answer wrong |
| 2 | a synthesis | an instruction not to repeat its inputs |
| 3 | a tool-using step | a rescue path that covered the *stream*, not just the call |
| 4 | a DAG level | a deadline that applied to the level, not each future |

**Live proof.** Fix 3 moved two team cases **3.5 → 5.0** and **1.25 → 5.0**; **6 of its
8 tests fail against the old code**. Fix 4: three hanging steps now return in **1.0 s**,
not 3 s.

**Open, and stated as open.** `code-intervals` scored **1.25** on *"write a Python
function that merges overlapping intervals"* — CI run **43279398224**, 2026-11-12T18:23Z. Untriaged. Four more known
limits are listed in the document.

---

## Part 3 — Observability

**What was built.** Prometheus metrics per pod, Grafana dashboards, Phoenix traces (one
`GroqLLM.chat` span per LLM call), Loki logs, and per-route SLOs on `/chat/stream`.

**What broke.** Three findings, all of them **a metric that reported the wrong number** —
which is worse than no metric, because a wrong one gets read and trusted.

- **p50 0.5 s on the dashboard, ~3 s in the pods.** Prometheus scraped the load-balanced
  Service, so two pods' series interleaved. Every individual data point was true. The
  aggregate was fiction.
- **"No data" instead of 0%.** A counter series does not exist until it fires, so *zero
  failures* and *no signal* rendered identically. Absence of evidence read as absence of
  problems.
- **A 2.0 s stream measured as 0.002 s.** The timer stopped when headers were ready —
  before a streamed answer began. A **1,000× understatement**, silently, for the life of
  the endpoint.

**Not measured, and said so.** Tracing overhead has never been measured. Alert rules
exist with no Alertmanager. Both are in the document's "what is not measured" section.

---

## Part 4 — The cluster

**What was built.** kind cluster, 14 services, 24 manifests, Prometheus/Grafana/Loki/
Phoenix, HPA, NetworkPolicy, CronJob backups, restore drill.

**What broke.** Five checks, and the through-line is: **the cluster worked exactly as
configured, and the configuration was wrong.**

- **The backup failed 3 nights running.** Scheduled 02:00 UTC; the PC is off then, so
  Kubernetes ran the missed job at **boot** — starting 13 s after Postgres, before it
  accepted connections. Proven by scaling Postgres to 0 and watching the job wait
  `(1/60)` … `(2/60)` … complete.
- **The autoscalers could never scale.** No metrics-server, so both HPAs read
  `cpu: <unknown>`. Now `3%/70%`.
- **The restore had never been tested.** A drill now restores the newest dump and compares
  row counts against the live database (6 tables, matching).
- **NetworkPolicy, image digests** — verified rather than assumed.

---

## The four through-lines, together

| Part | What was actually wrong |
|---|---|
| Measurement | guards attached one level too low |
| Agents | agents answering without the input they needed |
| Observability | the instrument reporting a true number about the wrong thing |
| RAG | quality features that reported success while doing nothing |
| Security | a claim that was true about the function and false about the posture |
| Cluster | correct configuration for the wrong assumption |

Four different surfaces, and in all four the system was **not** failing loudly. It was
returning a plausible answer, a plausible number, or a plausible green dashboard. That is
the theme of the whole project: **the failure mode is not a crash, it is a confident wrong
answer** — and the only defence is measuring the thing twice.

---

## Reading order

1. **This file** — the map.
2. [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) — the largest, and the one with the most numbers.
3. [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) — shortest; what the product actually gets wrong.
4. [`RAG_FINDINGS.md`](RAG_FINDINGS.md) — six findings, and five of them were
   features that reported success while doing nothing — including the CI gate
   that was supposed to catch the first one.
5. [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) — four questions, zero exploits,
   four wrong beliefs.
6. [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) — four findings, one pattern.
7. [`DARK_MODULES.md`](DARK_MODULES.md) — the triage, and the correction to its own premise.
8. [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) — runnable commands, live cluster.

The README's [bugs table](../README.md#-bugs-found-and-fixed) is the flat list of all 39
findings across every part, with the fix and the proof for each.

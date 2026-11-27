# EVAL MASTERY MAP — every file, every test, in reading order

**Scope:** the eval area only. Nothing outside `evals/`, `tests/test_*eval*` and
`tests/test_team_step_deadline.py`.

**Counts verified in-session:** 7 source files (**3,276 lines**), 7 test files
(**2,897 lines, 218 tests**), 6 RAG corpus files, 1 dataset with 60 cases.

> An earlier draft of this map said "452 tests". That was double-counted. It is
> **218**. `grep -c "^\s*def test_"` per file, summed.

---

# Part 1 — The files, in the order to read them

| # | File | Lines | What it is | Why this order |
|---|---|---|---|---|
| 1 | `evals/dataset.jsonl` | 60 cases | The 60 questions | Everything else is machinery around these 60 lines |
| 2 | `evals/README.md` | 158 | The official explanation | Written by you, for a stranger |
| 3 | `evals/checks.py` | 648 | Deterministic graders, no AI | Where most findings live |
| 4 | `evals/judge.py` | 142 | The AI grader | Small, but the most dangerous file |
| 5 | `evals/run.py` | 1536 | The engine | Biggest; read in 5 blocks (Part 2) |
| 6 | `evals/gate.py` | 244 | Fast CI gate, no model calls | Read after `run.py` — it reuses the checks |
| 7 | `evals/rag_eval.py` | 488 | Retrieval eval — separate system | Optional; only if you want the RAG series |

**Also in the eval area, easy to miss:**

| Path | What |
|---|---|
| `evals/recordings/README.md` | The replay system — reproduce a run for zero tokens |
| `evals/rag/questions.jsonl` | 48 lines, but line 1-3 are `//` comments |
| `evals/rag/corpus/*.txt` | 6 documents, 265 lines total — the retrieval test set |
| `evals/rag/corpus_other_user/bluefin_memo.txt` | 7 lines — the cross-owner leak test |

---

# Part 2 — `evals/run.py` in 5 blocks

Do not read this file top to bottom. Read these blocks.

### Block 1 — Loading and ordering (`:44-95`)
```
:44  DATASET                     path to dataset.jsonl
:49  load_cases()                filter by id / category / tag
:78  _CATEGORY_COST              cost per category — this drives the order
:86  order_by_cost()             cheapest first, team last
```
**Finding 7.** One function decides that quota loss hits the tail instead of
taking out everything after case 29.

### Block 2 — Running a case (`:202-600`)
```
:222  _bounded()                 guards the gap BETWEEN stream events
:273  execute()                  one message through the app
:322  _Deadline                  one clock shared across the level
:326  _run_with_hard_deadline()  guards when there is NO event at all
:384  _case_span()               per-case token budget
:427  evidence_fingerprint()     hash of checks + judge + dataset + model
:453  prompt_fingerprint()       hash of prompt TEXT (the thing that missed)
:486  run_case()                 orchestration
```
**Findings 11a/b/c and 9(c).** The pair that matters: `_bounded` guards between
events; `_run_with_hard_deadline` guards before the first one. A silent stream
has no events, so only the second can catch it.

### Block 3 — Summarising (`:638-790`) ⭐ learn cold
```
:638  _TAIL_CATEGORIES = {"team"}
:641  summarize(results)
  :681  threshold = float(os.getenv("EVAL_MIN_GRADED_PCT", "90") or 90)
  :707  inconclusive = n > 0 and completeness < threshold
  :725  only_tail_lost = ... <= _TAIL_CATEGORIES
  :730  massive = completeness < 70
  :731  inconclusive = inconclusive and (not only_tail_lost or massive)
  :770  fabrication_rate = fabric / judged
```
**This block is post 1, in full.** Draw it on a whiteboard:

```
coverage < 90 ?  ──no──>  ok, print the rate
      │yes
      ▼
is EVERY missing case in {"team"} ?  ──no──>  INCONCLUSIVE
      │yes
      ▼
coverage < 70 ?  ──yes──>  INCONCLUSIVE
      │no
      ▼
  ok, print the rate AND name the missing category
```

### Block 4 — Reporting and gating (`:791-1128`)
```
:791   render_markdown()     the human report
:885   FLOORS               pass_rate ≥ 55 · fab ≤ 12 · completeness ≥ 70
:901   check_floors()       independent of status
:937   compare(old, new)    the regression detector
:990   MIN_CASES_FOR_SIGNAL = 8
:992   _pass_rate_down = _pr.get("delta") is not None and _pr["delta"] < 0
```
**Findings 8, 9, 20.** `compare()` answers *"worse than yesterday?"*. It has no
concept of *"acceptable"*. That is why thirteen green nights could hide a
doubling fabrication rate.

### Block 5 — CLI and preflight (`:1278-1536`)
```
:1278  DAILY_TOKEN_CAP = int(os.getenv("EVAL_DAILY_TOKEN_CAP", "200000") or 200000)
:1281  preflight()          predicts tokens BEFORE spending them
:1322  build_parser()       --record / --replay / --pause / --fail-on-floors
:1360  main()
```

---

# Part 3 — `evals/checks.py` (648 lines)

Six groups. Learn the boundaries between them.

| Group | Functions | Lines |
|---|---|---|
| Provider failure | `is_provider_failure` | `:75` |
| Code extraction | `code_blocks`, `inline_code_spans`, `prose` | `:92-243` |
| Code validation | `normalise_dashes`, `validate_code`, `run_doctests`, `check_code` | `:314-505` |
| Regex shape | `_looks_like_regex`, `_regex_literal_in`, `_is_bare_pattern` | `:117-215` |
| Grounding | `check_grounding` | `:573` |
| Text rules | `check_completed/english/no_meta/includes/excludes/length/min_length/sources` | `:244-280`, `:516-572` |
| Entry point | `evaluate` | `:601` |

**The three hard ideas in this file:**

1. **`normalise_dashes` at `:314`** folds 8 Unicode characters to ASCII.
   `U+2010 U+2011 U+2012 U+2013 U+2014 U+2015 U+2212 U+00AD`.
   **Finding 4.** The guard: a test proves it does not change real subtraction.

2. **`_is_bare_pattern` at `:205`** decides whether prose text is a regex or a
   sentence. **Finding 5** — three failed attempts at this one.

3. **`run_doctests` at `:399`** actually **executes** `>>>` examples in the
   answer. It has a 5 s timeout, because an answer can contain an infinite loop.

---

# Part 4 — `evals/judge.py` (142 lines) — the most dangerous file

```
:18  CRITERIA = ("correctness", "completeness", "relevance", "clarity")
:20  RUBRIC                   the prompt
:50  relevant_sources()       which sources the judge sees
:93  parse_verdict(text)      JSON out of a free-text reply -> or None
:114 judge(...)               never raises
```

**Four facts:**

1. Scores 4 criteria, 1-5 each.
2. `parse_verdict` returns `None` on garbage → case is **not graded**, not
   passed. **Finding 12.**
3. `judge()` never raises — a dead judge cannot kill the run.
4. `fabrication` is a boolean the judge sets. It is what `fabrication_rate`
   counts.

**The sentence you must be able to say out loud:**

> This judge has never been validated against a human. Everything I quote from it
> is an instrument I have not calibrated.

---

# Part 5 — `evals/gate.py` (244 lines) — the free gate

```
:51   DATASET
:58   route()             routing without a model call
:81   gate_routing()      60 prompts -> expected route
:117  _CHECK_FIXTURES     hand-written answers, each must pass or fail
:162  gate_checks()
:205  main()
```

**Why it exists:** a full run costs 200,000 tokens/day. This costs zero and runs
in seconds. It catches "the grader broke" without running the agent.

**The concept:** `_CHECK_FIXTURES` is a set of known-good and known-bad answers.
If a check stops failing the known-bad one, the gate goes red.

---

# Part 6 — The 218 tests, by file

## `tests/test_evals.py` — 130 tests, 1,702 lines

The changelog of every bug you fixed. Read the **names**, they tell the story.

| Theme | Tests | Examples |
|---|---|---|
| `compare()` / regression gate | **19** | `test_a_diff_across_a_changed_grader_is_incomparable_not_a_regression`, `test_the_best_run_the_suite_ever_had_is_not_called_a_regression` |
| code & regex checks | **14** | `test_an_inline_snippet_counts_as_code_when_the_case_asks_for_python`, `test_any_code_block_is_not_automatically_a_regex` |
| grounding | **11** | `test_grounding_is_not_applied_to_cases_that_do_not_ask_for_sources`, `test_a_skipped_grounding_check_says_so_instead_of_looking_clean` |
| coverage / INCONCLUSIVE | **10** | `test_a_half_run_report_is_inconclusive_not_a_pass_rate`, `test_losing_only_the_deliberately_last_category_is_not_an_inconclusive_run` |
| token budget | **8** | `test_a_case_can_no_longer_overspend_its_budget`, `test_the_default_case_budget_lets_a_full_run_fit_the_daily_tier` |
| doctest runner | **8** | `test_infinite_loop_doctest_times_out_instead_of_hanging`, `test_fragment_whose_examples_need_missing_names_is_not_failed` |
| run_case / execute | **7** | `test_run_case_survives_executor_crash` |
| judge | **5** | `test_judge_never_raises`, `test_a_failed_judge_means_not_graded_not_passed` |
| category rows | **5** | `test_a_category_whose_cases_never_ran_does_not_report_zero_percent` |
| deadlines & silent streams | **5** | `test_a_generator_that_goes_silent_stops_at_the_deadline`, `test_a_case_that_hangs_before_any_event_is_abandoned` |
| dataset & simple checks | **4** | `test_dataset_is_valid_and_varied` |
| no_meta | **4** | `test_a_single_weak_meta_phrase_does_not_fail_the_case` + `test_real_pipeline_narration_still_fails_the_case` |
| smoke set | **4** | `test_the_smoke_set_covers_every_category` |
| nightly config | **4** | `test_the_nightly_pause_can_sustain_a_full_run` |
| ordering & routing | **3** | `test_team_cases_run_last_so_quota_starvation_hits_them_not_the_cheap_ones` |
| the dash fold | **3** | `test_every_dash_in_the_family_is_folded`, `test_normalising_does_not_change_real_subtraction` |
| key leak | **3** | `test_a_real_product_key_is_caught_by_shape` |
| floors | **3** | `test_a_small_category_alone_never_fails_the_build` |
| summarize / markdown / CLI | **2** | `test_cli_exits_zero_when_coverage_is_thin` |
| **other (single-issue tests)** | **17** | see below |

**The 17 "other" tests — each one is a specific bug:**

```
test_code_checks                                    the code check end to end
test_a_run_where_a_third_of_cases_were_not_graded_says_so
test_a_baseline_without_an_evidence_record_is_not_comparable
test_a_single_document_is_not_iterated_character_by_character
test_a_number_grouped_with_a_narrow_space_still_matches
test_a_result_big_enough_to_check_at_runtime_is_checked_in_the_eval_too
test_a_release_date_from_last_year_satisfies_a_year_requirement
test_an_old_year_does_not_satisfy_a_year_requirement
test_dedenting_keeps_a_genuinely_indented_block_intact
test_code_valid_is_not_checked_when_the_case_never_asked_for_code
test_code_valid_still_runs_when_a_language_is_declared
test_an_lru_implementation_is_not_required_to_say_the_word_dict
test_a_python_call_is_not_mistaken_for_a_bare_pattern
test_an_exception_inside_a_case_still_propagates
test_the_reason_and_the_numbers_reach_the_reader
test_an_inline_regex_counts_as_code_when_the_case_asks_for_a_regex
test_a_pasted_doctest_session_is_valid_python
```

## `tests/test_eval_floors.py` — 17 tests, 244 lines

All about `FLOORS` and `check_floors()`. **This file is finding 20's proof.**

| Test | What it pins |
|---|---|
| `test_the_drift_is_caught_three_nights_running` | the real 13-run series |
| `test_floor_fires_on_four_of_the_thirteen_real_runs` | the 4-of-13 ratio |
| `test_pass_rate_floor_is_inclusive_at_the_boundary` | 55.0 passes, 54.9 fails |
| `test_fabrication_ceiling_is_inclusive_at_the_boundary` | 12.0 passes, 12.1 fails |
| `test_coverage_floor_matches_the_number_summarize_already_uses` | 70 == the `massive` number |
| `test_a_missing_metric_is_skipped_not_passed` | absent ≠ zero |
| `test_a_non_numeric_metric_is_skipped_not_compared` | |
| `test_a_boolean_is_not_a_number` | `True` is not 1 |
| `test_no_metrics_at_all_breaches_nothing_but_checks_nothing` | |
| `test_floors_and_regression_are_independent_facts` | the key idea |
| `test_compare_carries_floors_computed_from_the_new_run_only` | |
| `test_render_compare_names_the_breach_in_words` | |
| `test_render_compare_says_ok_when_floors_pass` | |
| `test_render_compare_names_floors_it_could_not_evaluate` | |
| `test_fail_on_floors_is_a_flag_and_defaults_off` | |

## `tests/test_eval_gate_and_pii.py` — 19 tests, 177 lines

**Gate (6):**
```
test_gate_passes_on_the_committed_dataset
test_gate_detects_a_routing_regression
test_gate_skips_cases_without_a_route
test_check_fixtures_all_pass
test_gate_detects_a_broken_checker
test_gate_main_returns_zero_on_clean_run
```
**PII (8):** `test_ssn_is_redacted`, `test_luhn_valid_card_is_redacted`,
`test_number_failing_luhn_is_left_alone`, `test_phone_and_email_are_out_of_scope`,
`test_redact_is_idempotent`, `test_redact_handles_empty_and_none`,
`test_redact_dict_walks_nested_structures`, `test_audit_logger_redacts_details`

> Note `test_number_failing_luhn_is_left_alone` — a checksum decides, not a
> regex. And `test_phone_and_email_are_out_of_scope` — the scope is *declared*.

**Dataset integrity (5):**
```
test_dataset_has_sixty_cases_with_unique_ids
test_every_case_declares_a_valid_difficulty
test_every_case_can_be_graded_somehow
test_safety_cases_are_adversarial
test_declared_expected_tools_are_real_tool_names
```

## `tests/test_eval_judge_and_markers.py` — 6 tests, 92 lines

```
test_judge_gets_the_passages_the_answer_uses
test_small_sources_are_passed_whole_and_in_order
test_rubric_judges_against_sources_not_memory
test_strip_citation_markers
test_stream_strips_marker_split_across_chunks
test_wrapup_answer_is_fact_checked
```
`test_rubric_judges_against_sources_not_memory` is the important one — it pins
that the judge is told to use the sources, not its own knowledge.

## `tests/test_team_step_deadline.py` — 2 tests, 49 lines

```
test_step_timeout_is_read_at_call_time
test_a_level_of_hanging_steps_is_bounded_by_one_timeout
```
**Finding 11a.** The second one asserts `elapsed < 1.5` at a 0.5 s step timeout.
Reproduced standalone: **0.50 s fixed vs 1.50 s buggy.**

## `tests/test_rag_eval.py` — 23 tests, 333 lines

Retrieval gate maths. The two that matter for the RAG series:
```
test_hit6_alone_cannot_see_the_reranker_the_old_gate_was_blind    <- R6
test_hit1_and_mrr_floors_can_see_the_reranker
test_require_reranker_rejects_a_keyword_fallback                   <- R1
test_gate_sensitivity_is_printed_in_the_summary
```

## `tests/test_rag_retrieval.py` — 21 tests, 300 lines

These are **API** retrieval tests, not eval-runner tests — but they bracket the
eval. Ownership and cache behaviour:
```
test_get_context_never_returns_another_owners_text      <- the leak
test_the_cached_answer_is_per_owner                      <- R3
test_the_cache_key_ignores_spacing_and_case
test_the_dead_engine_decomposes_any_query_containing_and  <- R2
test_the_garbled_sub_questions_are_shown_to_the_user
```

---

# Part 7 — The 10 things to be able to explain with no notes

1. **Why `team` runs last.** `order_by_cost()` (`run.py:86`). Expensive cases
   starve cheap ones; put them at the tail so quota loss is survivable.

2. **Why the report refuses a rate under 90%.** `run.py:707`. Exemption: every
   missing case must be in `_TAIL_CATEGORIES` (`:638`).

3. **Why 70 exists separately.** `run.py:885` `FLOORS`. 90 decides *what you
   see*; 70 decides *whether the run counts at all*.

4. **Why `compare()` cannot catch drift.** `run.py:992`. It only asks "worse
   than yesterday?". Thirteen greens while fabrication went 6.88% → 12.93%.

5. **Why a failed judge is not a pass.** `judge.py:93` returns `None` → not
   graded → counted as not passed.

6. **Why the dash fold has a guard test.** `checks.py:314` folds 8 characters;
   `test_normalising_does_not_change_real_subtraction` proves maths still works.

7. **Why an inline snippet counts as code.** `checks.py:105`
   `_parses_as_python`. Format is not correctness (finding 3).

8. **Why `_CHECK_FIXTURES` exists.** `gate.py:117`. A full run costs 200k tokens;
   the gate costs zero and still catches a broken checker.

9. **Why there are two deadlines.** `_bounded` (`:222`) guards between events;
   `_run_with_hard_deadline` (`:326`) guards when there is no event. A silent
   stream has none (finding 11b).

10. **Why the judge is uncalibrated.** It has never been scored against a human.
    Everything quoted from it is an instrument you have not validated.

---

# Part 8 — Six-day plan

| Day | Read | ~Lines |
|---|---|---|
| 1 | `dataset.jsonl` (all 60) + `evals/README.md` | 220 |
| 2 | `checks.py:92-500` (code, dash, regex, doctest) | 400 |
| 3 | `run.py:638-790` (**summarize**) + `tests/test_evals.py` coverage tests | 310 |
| 4 | `run.py:885-1128` (floors + compare) + `tests/test_eval_floors.py` | 480 |
| 5 | `judge.py` (all) + `gate.py:117-190` + `test_eval_gate_and_pii.py` | 380 |
| 6 | `test_evals.py` — **names only**, in order | 170 |

**Day 3 and Day 4 are the ones that matter.** They are post 1 and finding 20, and
they are the two blocks an interviewer would probe.

---

# What is deliberately not in this map

- `evals/rag_eval.py` detail (Part 6 lists its tests, but the file itself is a
  second series)
- Anything outside the eval area — model code, API, k8s, observability
- The post drafts themselves (`docs/posts/`)

**Cross-reference:** `docs/EVAL_POST_INVENTORY.md` is the *postable material*.
This file is the *learning route*. They share findings but not structure.

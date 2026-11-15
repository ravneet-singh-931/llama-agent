# CI findings

Seven findings about the pipeline. Every one of them is the same failure: **the
job reported success for something it did not do.**

A green tick is not a statement about the code. It is a statement about the job,
and only as good as what the job actually runs.

---

## 1. The "Test all modules import" job did not install what the image installs

**Symptom.** `integration` stayed green. The Docker image broke.

**Cause.** `test` (L38) installed `-r requirements.txt`. `integration` (L275)
hand-listed **five unpinned** `pip install` lines. It therefore installed the
newest `mcp<2` instead of the pinned `mcp==1.30.0`, and it installed the
standalone `fastmcp`, which **nothing imports** and which
`test_version_and_deps.py` asserts must not be a dependency — the MCP server uses
`mcp.server.fastmcp`, a submodule of `mcp`.

Two different environments, one of them wrong, both green.

**Fix.** `pip install -r requirements.txt`, one line, identical to the `test` job.

**Proof.** Verified that `mcp==1.30.0` alone provides `mcp.server.fastmcp`;
`test_unused_fastmcp_is_not_in_requirements` still guards the dependency set.

---

## 2. A test whose assertion was a `print`

**Symptom.** `test_cors_headers_present` passed. It would have passed with CORS
deleted entirely.

**Cause.** It built a list of middleware, **discarded it**, printed
`✅ CORS middleware configured`, and passed. The README cited it as proof.

**What that concealed.** Development ran `allow_origins=["*"]` together with
`allow_credentials=True`. Starlette turns that combination into a reflected
`Access-Control-Allow-Origin: <whatever origin asked>` **plus**
`Access-Control-Allow-Credentials: true` — which browsers accept. Any site the
developer visited could make authenticated cross-origin calls to their local API.

**Fix.** Explicit origin lists in both environments. The test now asserts the
middleware is registered, that origins are non-empty, that `*` is never combined
with credentials, and that the expected methods and headers are allowed.

**Proof.** Sabotaging `add_middleware` fails it with `assert []`.
`curl -H 'Origin: https://evil.example'`: header reflected → **absent**.

---

## 3. The integration smoke test called a method that had been deleted

**Symptom.** CI red, traceback pointing at a method name that no longer existed.

**Cause.** A previous cleanup deleted the method and updated its callers, but the
smoke test in `main.yml` is inline shell — it lives **inside the workflow YAML**,
where no test suite and no linter looks.

**Fix.** Updated the call and added a test that parses `main.yml` and checks the
inline code against the real API.

**The lesson.** Code inside CI configuration is code. It has no tests, no
typechecker and no import graph, so it rots silently while everything around it
changes.

---

## 4. A quoting bug introduced *by* the fix for #3

**Symptom.** The fix for #3 broke the workflow with a YAML/AWK quoting error.

**Cause.** Shell quoting inside `run: |` blocks is nested three deep (YAML → awk →
python -c). My first attempt got it wrong in a way that only shows at runtime.

**Fix.** Simpler quoting; the validation script in `tests/` now runs the extracted
inline code rather than only checking that it parses.

**The lesson.** A CI fix is not verified by reading the YAML. It is verified by
running the extracted code — which is why that test exists now.

---

## 5. The lint fix that broke the lint job

**Symptom.** `Code Quality` red after a change that only touched a test.

**Cause.** The new test invoked `python` (from `PATH`) while CI did not have that
on `PATH` in the step's context.

**Fix.** Absolute interpreter path (`sys.executable`).

---

## 6. Three reds, one cause: the verification loop was wrong

**Symptom.** Three consecutive CI failures on changes that passed locally.

**Cause.** My local loop was not CI's loop. CI runs
`pytest tests/ -v -m "not mcp" -p no:phoenix --cov=. --cov-fail-under=85`;
running plain `pytest -q` locally is a **different command with a different
scope**.

**Fix.** The CI-exact loop, run before every push:

```bash
pytest tests/ -v -m "not mcp" --tb=short -p no:phoenix \
  --cov=. --cov-report=term --cov-fail-under=85
bandit -r . -f json -o bandit-report.json --skip B101,B104,B105,B106,B107,B110
flake8 . --count --select=E9,F63,F7,F82
```

**The lesson.** "It passes locally" is only meaningful if `locally` is the same
command. Note the two coverage scopes: `--cov=.` (whole repo, CI) reads **85%**;
the narrower `--cov=modules --cov=api` reads about **70%**. Comparing them is
comparing two different measurements.

---

## 7. The deploy gate: three jobs that skip, on purpose

**Symptom.** Two `Deploy` jobs and `Build & Push Docker` show as **skipped** on
every run. A skipped run does not look like a green one.

**What I got wrong first.** I called the Docker job decorative. It is not. It
**builds on every main push**; only the *push* is conditional
(`push: ${{ secrets.DOCKER_USERNAME != '' }}`, with login
`continue-on-error: true`). Calling it fake was wrong, and the correction is in
`CASE_STUDIES.md`.

**What is actually true.** The deploy jobs are deliberately skipped while effort
goes to other parts of the project, and the pipeline is tied to Kubernetes. This
is **not broken**. It is a scope decision, and it is recorded in the workflow
itself.

**One honest consequence, not acted on.** `k8s/api.yaml`, `k8s/frontend.yaml` and
`k8s/mcp-server.yaml` pin the **moving tag** `:v7`, not a commit SHA. If the
image push is skipped, the cluster serves whatever `:v7` last pointed at. That is
unverifiable from this environment (`gh secret list` returns **403**, so secret
presence cannot be checked here), so it is stated as a risk rather than fixed.

**Proof of what the jobs actually do.** `gh run view <id> --json jobs` — before
calling any job decorative, look at what it ran.

---

## 8. `notify` opens a GitHub issue on every failure

Not a bug, but a constraint that shaped a decision: any change that makes the
pipeline fail more often would file an issue every time. That is why the deploy
gate was **not** converted into a failure. Awarding yourself more red builds is
not the same as improving the pipeline.

---

## The pattern

| Claimed | Actually |
|---|---|
| "Install steps verified" | a hand-listed, unpinned, different dependency set |
| "CORS configured" | a discarded list and a printed checkmark |
| "Smoke test passes" | called a method deleted in a previous commit |
| "Passes locally" | ran a different command with a different scope |

Every one is a control that existed and was not connected to the thing it named.
And every fix above is a test, not a YAML edit — because the thing that was wrong
was never the YAML, it was that **nothing checked the YAML**.

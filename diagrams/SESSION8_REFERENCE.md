# SESSION 8 REFERENCE — The Document Ingestion Pipeline

`modules/doc_answers.py` (parsing path) · `modules/rag.py` `DocumentReader` · `modules/security.py`

**Every number in this document was measured by running code. Nothing is copied from a docstring.**

---

## §0 The one-sentence version

> A file you upload is **never executed** — it is only *read as characters*. So the real danger is not
> "malware runs"; it is **how much memory and time the act of reading costs**, and **whether the
> guard that is supposed to stop that cost runs before or after the cost is already paid.**

---

## §1 The journey of a file — 7 stages

```
  browser
     │  POST /documents/upload  (multipart/form-data)
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 0  Starlette multipart parser                         │
│   chunk by chunk off the socket. Up to 1 MB → SPOOLED temp   │
│   file in RAM; beyond 1 MB → rolls over to a real file in     │
│   /tmp.  (measured: spool_max_size = 1024*1024)              │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 1  api.py:2453   content = await file.read()          │
│   ** the whole body is now one bytes object in RAM **        │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 2  security.py:79   validate_file_upload(...)         │
│   size → non-empty → extension allowlist → suspicious-regex  │
│   ** this is the FIRST time anyone looks at the size **      │
└─────────────────────────────────────────────────────────────┘
     │  (rejected here → 400, nothing written to disk)
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 3  sanitize_filename → get_upload_path                │
│   name → safe name → uploads/<owner>/<safe_name>            │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 4  asyncio.to_thread(_write_bytes)   api.py:2481      │
│   bytes hit the disk. From here the file is persistent.     │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 5  asyncio.to_thread(rag.add_document, path)  :2488   │
│           └─ rag.py:163  DocumentReader.read_any(path)      │
│                 └─ dispatch on extension → one reader fn    │
│                       └─ returns a big str of text          │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 6  rag.py:222  _split_text(text)                      │
│   hard slice every 500 characters → N chunks                │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 7  chunks → documents list / vector store, per owner  │
└─────────────────────────────────────────────────────────────┘
```

**The critical structural fact:** the ordering is **1 → 2**, not **2 → 1**. The size guard is
downstream of the allocation it is named after.

---

## §2 Stage by stage, with every term decoded

### Stage 0 — multipart parsing and "spooling"

| Term | Plain meaning |
|---|---|
| **multipart/form-data** | The envelope format a browser uses to send a file. The bytes are wrapped in a text boundary (`------WebKitFormBoundary...`) so the server can find where the file starts and ends. |
| **spooling** | Writing incoming bytes to a temporary holding area before you know what to do with them. |
| **SpooledTemporaryFile** | A file object that starts life **in memory** and silently **moves itself to disk** once it grows past a threshold. |
| **spool_max_size** | That threshold. Measured in this repo's Starlette: **1 MB**. |

Why it matters: for an upload of *n* bytes, the parser has already consumed **min(n, 1 MB) of RAM plus
n bytes of temp disk** before your handler function's first line runs. Your 10 MB limit is not
consulted. Nothing in this repo sets it.

### Stage 1 — `content = await file.read()` (api.py:2453)

- `await` here does **not** mean "streams to disk in the background". `UploadFile.read()` with no
  argument returns the **entire remaining body as one `bytes` object**.
- Therefore: **RAM cost = full file size**, regardless of the 10 MB cap.

### Stage 2 — `validate_file_upload` (security.py:79)

Signature: `validate_file_upload(filename, file_size, content_type=None) -> (bool, str)`

Runs four checks in order:

| # | Check | Measured behaviour |
|---|---|---|
| 1 | size vs `MAX_FILE_SIZE` (10 MB) | `2 GB` → `REJECT: File too large (2048.0 MB). Maximum: 10.0 MB` |
| 1b | boundary exactness | `10 485 759` accept · `10 485 760` accept · `10 485 761` reject — **`<=`, not `<`** |
| 2 | empty file | reject |
| 3 | extension in `ALLOWED_EXTENSIONS` | `.csv .docx .json .md .pdf .py .txt` |
| 4 | regex recheck for suspicious patterns | runs on the *name only* |

**Dead parameter:** `content_type` is declared, passed by the caller as `file.content_type`,
and **never read inside the function**. There is no MIME check anywhere on this path. The browser's
declared content type could be `text/x-shellscript` and it would change nothing.

### Stage 3 — `sanitize_filename` (security.py:42) and `get_upload_path` (security.py:134)

`sanitize_filename` is a funnel — each step removes one class of attack:

```
  "....//..//etc/passwd"                    raw, attacker-controlled
        │
        ├─ os.path.basename()               strip every directory component
        ├─ remove "\x00"                    NUL-byte truncation attack
        ├─ regex scrub to [\w\-.]           kill ; & | $ ` and friends
        ├─ delete ".."                      kill traversal
        ├─ strip('. ')                      kill hidden files / trailing dots
        ├─ [:]
        └─ truncate to 255 (lines 71-74)    keep under the filesystem limit
        │
        └─ empty? → "uploaded_file"         never return ""
```

`get_upload_path(upload_dir, filename)` then joins and re-checks with `os.path.realpath`:

```python
if not full_path_resolved.startswith(upload_dir_resolved):   # note: NO os.sep
    raise ValueError("Path traversal detected")
```

### Stage 4 — the write

`asyncio.to_thread(_write_bytes, file_path, content)` — the bytes finally reach disk, at
`uploads/<owner>/<safe_name>`.

### Stage 5 — `read_any` (rag.py:115) — the dispatcher

```python
ext = Path(file_path).suffix.lower()
readers = {'.txt': read_text, '.md': read_markdown, '.py': read_text,
           '.json': read_text, '.csv': read_csv,
           '.pdf': read_pdf, '.docx': read_docx}
reader = readers.get(ext)
if reader: return reader(file_path)
return f"Unsupported file type: {ext}"
```

This is a **dispatch table** (also called a registry or a lookup table): a `dict` mapping a key to a
function object. The alternative is an `if/elif` chain. A dict lookup is O(1) and, more importantly,
**one place to add a type** — you register the reader instead of editing control flow.

Measured: `read_any` on a file with no extension → `'Unsupported file type: '`. It never raises.

**The dispatcher trusts the extension.** There is no magic-byte check. Magic bytes (also: *file
signature*) are the first few bytes a format always starts with — `%PDF` for PDF, `PK\x03\x04` for
anything ZIP-based (`.docx`, `.xlsx`, `.jar`). Checking them is how you learn what a file *is*
rather than what it is *called*.

### Stage 5a — the readers

| Reader | Line | Behaviour | Failure mode |
|---|---|---|---|
| `read_text` | 35 | `open(..., encoding='utf-8').read()` | swallows all exceptions → `"Error reading file: ..."` |
| `read_markdown` | 82 | delegates to `read_text` | same |
| `read_csv` | 44 | header + **at most 10 sample rows** + a row *count* | exception → falls back to `read_text` |
| `read_pdf` | 87 | `pypdf.PdfReader`, `text += page.extract_text()` per page | `ImportError` → "not available"; any other exception → `"Error reading PDF: ..."` |
| `read_docx` | 103 | `docx.Document(path)` → join paragraph texts | same two-clause pattern |

**Every reader swallows its exception and returns a string beginning with `"Error"`.** That
convention is load-bearing: `add_document` (rag.py:165) checks `if text.startswith("Error"): return text`
and aborts. So a failed parse never becomes a document — it becomes a string that happens to be
returned to the user as the upload message.

`read_csv` is the quiet one. It reads **every** row to count them, but *keeps only the first 10*.

### Stage 5b — `read_docx` is a ZIP parser

A `.docx` is not a text format. It is a **ZIP archive** containing XML parts (`word/document.xml`,
`[Content_Types].xml`, …). So `docx.Document()` performs a **decompression**, and decompression is
where amplification lives.

### Stage 6 — `_split_text` (rag.py:222)

```python
for i in range(0, len(text), self.chunk_size):     # chunk_size = 500
    chunk = text[i:i + 500]
    if chunk.strip(): chunks.append(chunk)
```

| Term | Plain meaning |
|---|---|
| **chunk** | A small fixed-size slice of a document — the unit that gets embedded and retrieved. |
| **chunk_size** | 500 characters here. |
| **overlap** | Deliberately repeating the tail of one chunk at the head of the next, so a sentence cut in half is still findable. **This repo has zero overlap** — a fact that will lose you recall at the boundary of every chunk. |

Measured: 10 485 760 characters → **20 972 chunks**, from one upload.

---

## §3 `security.py` limb by limb

| Symbol | Line | Plain meaning | Verdict |
|---|---|---|---|
| `MAX_FILE_SIZE` | 26 | 10 MB | applied too late (Stage 2) |
| `ALLOWED_EXTENSIONS` | 29 | 7 suffixes | an **allowlist** — default-deny, correct shape |
| `BLOCKED_EXTENSIONS` | 35 | denylist | a **denylist** — default-allow, the wrong shape; unused on this path |
| `sanitize_filename` | 42 | path/name normaliser | correct on traversal; truncates at 255 |
| `validate_file_upload` | 79 | the gate | size+ext only; no MIME, no magic bytes |
| `get_upload_path` | 134 | join + contain | `startswith` **without `os.sep`** |
| `is_path_within_roots` | 152 | LFI guard | correct: `== root` **or** `startswith(root + os.sep)` |

### Allowlist vs denylist — the single most transferable idea in this file

| | Allowlist (default-deny) | Denylist (default-allow) |
|---|---|---|
| Rule | "only these are permitted" | "these are forbidden" |
| Unknown input | **rejected** | **accepted** |
| Attacker's job | find a gap in a finite set | invent one new name |
| This repo | `ALLOWED_EXTENSIONS` ✅ | `BLOCKED_EXTENSIONS` ❌ |

You cannot enumerate all bad names. You *can* enumerate the handful of good ones.

### The prefix bug, measured

```
upload dir   = /tmp/x/upload
sibling dir  = /tmp/x/upload-evil      <- NOT inside the upload dir

'/tmp/x/upload-evil'.startswith('/tmp/x/upload')        = True    <-- get_upload_path line 146
'/tmp/x/upload-evil'.startswith('/tmp/x/upload' + os.sep) = False  <-- is_path_within_roots line 174
is_path_within_roots('/tmp/x/upload-evil/x', ['/tmp/x/upload']) = False  ✅ correctly rejects
```

The identical string is judged **inside** by one function and **outside** by the other, in the same
file. The safe form is `path == root or path.startswith(root + os.sep)`.

**Honest scope:** this is **latent, not live**. `sanitize_filename` deletes `/` and `..` before
`get_upload_path` runs, so the upload endpoint cannot reach a sibling directory today. It is a trap
for the next caller, not a hole you can walk through now. Say that out loud in an interview — the
difference between "vulnerability" and "latent defect" is the difference between senior and junior.

---

## §4 YOUR QUESTION ANSWERED — three attacks, three *different* answers

> *"What stops a 2 GB PDF, a zip bomb, or a `.pdf` that is really a shell script?"*

### 4.1 A 2 GB PDF — **partly stopped, and stopped in the wrong order**

Measured with the pipeline's own code:

```
Stage 0  multipart parser    → spools to /tmp; ~2 GB temp disk consumed
Stage 1  await file.read()   → ~2 GB resident in RAM as one bytes object
Stage 2  validate_file_upload("huge.pdf", 2*1024**3)
         → REJECT  "File too large (2048.0 MB). Maximum: 10.0 MB"
```

So the answer is **not** "nothing stops it". The answer is:

> **The check rejects it, but only after the memory has already been spent.** The guard fires at a
> point that cannot undo the allocation it is meant to prevent. The correct order is to reject on
> `Content-Length`, then read in bounded chunks and abort mid-stream — never `.read()` an unbounded
> body into one object.

Second layer, if nginx were in the path: `config/nginx/nginx.conf` has **no `client_max_body_size`**,
so nginx's default of **1 m** applies and nginx would kill the request at 1 MB before Python sees it.
But `docker-compose.yml:156-157` publishes `"8000:8000"` — **the API is reachable directly, bypassing
nginx entirely**. A defence that exists only behind a proxy is not a defence for a published port.

Third layer: the 255-char filename truncation means two different long names can collide on disk and
overwrite each other. Data loss, not a breach.

### 4.2 A zip bomb — **not stopped at all, measured**

Measured at the gate:

```
validate_file_upload("bomb.pdf", 9*1024*1024)  →  PASS  (accepted!)
```

A 9 MB body is comfortably under the cap. The size check measures **compressed** bytes; the cost is
paid in **decompressed** bytes. Measured amplification in this repo's own `read_docx`:

```
.docx on disk : 8,017,704 bytes  (7.8 MB)   ← under the 10 MB cap
read_docx()   : peak RSS = 599 MB           ← from a 13 MB baseline
amplification : 599 / 7.8  ≈ 77x memory per MB uploaded
```

The 1 MB spool and the 10 MB cap are both irrelevant — the file is small. The explosion happens
*inside* `docx.Document()`, after every guard has already passed.

Why PDFs are **less** exposed here (also measured): `pypdf` validates the header first and rejects
non-PDF content (`invalid pdf header: b'PK\x03\x04'` → `EOF marker not found`), so a ZIP renamed
`.pdf` dies early. And `read_pdf` is purely sequential:
`for page in reader.pages: text += page.extract_text()` — so a 5 000-page PDF costs 5 000 LLM-free
extractions up front, unbounded in *time*, not memory. **No page limit is enforced.**

Why DOCX is **more** exposed: `python-docx` genuinely parses the archive. ZIP decompression has no
built-in ceiling in the standard library's common usage. There is no per-entry size limit, no total
expansion limit, no entry-count limit, and no page/paragraph limit downstream.

**What actually stops a zip bomb** (none of which is present): a decompression **ratio cap**; an
**absolute** expansion cap checked while streaming; a cap on the number of archive entries;
parsing in a subprocess with an `RLIMIT_AS` memory ceiling so the blast radius is one worker instead
of the API process.

### 4.3 A `.pdf` that is really a shell script — **"stopped", but not for the reason you think**

Measured with `pypdf` installed:

```
DocumentReader.read_pdf("payload.pdf")  where the file is  "#!/bin/sh\nrm -rf /\n"
   → stderr: invalid pdf header: b'#!/bi'
   → returns: 'Error reading PDF: Stream has ended unexpectedly'
```

Then `add_document` sees the `"Error"` prefix and returns without indexing anything.

But the *deeper* answer is the important one:

> **Nothing in this system ever executes an uploaded file — so a shell script named `.pdf` was never
> a code-execution attack in the first place. It is only ever `open()`ed and decoded as text.**

Execution requires an **execution sink**: `os.system`, `subprocess`, `eval`, `exec`, a template engine,
`pickle.loads`, a `Content-Type: text/html` response that the browser renders, a `PATH` entry, a cron
job, a `Dockerfile` `RUN`, or a `.so`/`.php` file landing in a directory a server will *interpret*.

Measured, precisely: `subprocess` / `os.system` / `exec(` / `eval(` appear **0 times** in
`api.py`, `modules/rag.py`, and `modules/doc_answers.py`. `modules/security.py` has 7 hits but every
one of them is inside `safe_math_eval` (line 412) — and that function is the *opposite* of a sink: it
replaced `eval(expression, {"__builtins__": {}})` with an AST walker that accepts only whitelisted
node types (`ast.Constant` numeric, the binops in `_MATH_BIN_OPS`, the unary ops in
`_MATH_UNARY_OPS`, and names in `_MATH_FUNCS`), raising `ValueError` on everything else. That is
**the allowlist pattern applied to code itself** — the same idea as `ALLOWED_EXTENSIONS`, one level
deeper. `eval` with emptied builtins is *still* a sandbox escape; an AST whitelist is a real boundary.

Measured: there is **no execution sink on this path**. `api.py` serves uploads back to nobody — there
is no `FileResponse`, no `StaticFiles` mount, no `send_file` for `uploads/`. The grep for those
returns only `JSONResponse`/`StreamingResponse` for chat streaming. An uploaded file never becomes a
URL.

So the honest answer has three layers:

1. **It does not execute**, therefore nothing runs. Verified by grep: no sink.
2. **It does not even parse**, therefore nothing is indexed. Verified by running it.
3. But the *content-type* is never checked (`content_type` is a dead parameter), so the system is
   relying entirely on (1) and (2) — two accidental properties — rather than on a control. If anyone
   later adds "serve the uploaded file back for preview" or "run uploaded `.py` as a tool", layer 1
   disappears and this becomes a live hole with no guard rail in between.

**That distinction — "safe today by structure" vs "safe by control" — is the answer that
distinguishes you in the interview.**

---

## §5 The bug ladder found in Session 8

| ID | Location | Finding | Severity |
|---|---|---|---|
| **F13** | `api.py:2453` | `await file.read()` buffers the **entire** body before `validate_file_upload` at 2461; the 10 MB cap cannot prevent the allocation it names. Correct order: `Content-Length` pre-check → bounded chunked read → abort mid-stream. | High |
| **F14** | `security.py:79` | `content_type` is a declared parameter, passed by the caller, **never read**. No MIME check, no magic-byte check, no signature inspection anywhere on the upload path. | High |
| **F15** | `rag.py:103` `read_docx` | No decompression limits of any kind. Measured **77× memory amplification** (7.8 MB file → 599 MB RSS) with no guard. `read_pdf` has no page cap either. | High |
| **F16** | `security.py:146` | `get_upload_path` uses `startswith` **without** `os.sep`; `is_path_within_roots` at 174 in the same file gets it right (`== root or startswith(root + os.sep)`). Measured: `upload-evil` judged *inside* `upload`. Latent — `sanitize_filename` removes `/` and `..` first, so unreachable today. | Medium (latent) |
| **F17** | `rag.py:44` `read_csv` | Reads every row to count them but keeps only the **first 10**. Measured: 1 000-row CSV → 435 chars, `user500` and `user999` **absent**. Rows 11+ are silently unindexed and the user is told "success". | Medium |
| **F18** | `rag.py:222` `_split_text` | `chunk_size=500`, **zero overlap**. Any fact spanning a chunk boundary is retrievable only by luck. | Medium |
| **F20** | `rag.py:103` / stdlib | A ZIP's declared uncompressed size is **attacker-controlled**. Measured: a **204 KB** zip declaring `file_size=1000` expanded to **200 MB** (peak RSS 612 MB) before the CRC check fired. A pre-decompression per-entry size check is therefore **bypassable**; only a *streaming* cap that aborts mid-read, or an `RLIMIT_AS` subprocess, actually holds. |
| **F21** | `docker-compose.yml` | The `api` service declares **no `mem_limit`** and **no `deploy.resources.limits`** — verified by parsing the service block; the only `memory`/`deploy` string matches are a volume mount and a comment. So there is no container memory ceiling for an upload to breach; an explosion consumes **host** RAM and is bounded only by the kernel OOM killer. |
| **F19** | repo-wide | **Two** `sanitize_filename` functions with **different** behaviour: `modules/security.py:42` truncates to 255 (measured 255); `modules/security_hardening.py:196` does not (measured 304, no length clamp). `api.py:2446` imports the `security` one. | Medium |

---

## §6 The trap I walked into — a lesson about reading tests

I previously recorded "live contradiction: the test asserts `len(out) > 255` but measurement says
255, so the test must be failing." **That was wrong, and the wrongness is instructive.**

```
$ pytest tests/test_security_hardening.py -q -k long_filenames
1 passed
```

The test imports:

```python
from modules import security_hardening as sh
```

Not `security`. Two modules, two functions, same name, different behaviour:

| Module | `sanitize_filename("a"*300 + ".txt")` | Length |
|---|---|---|
| `modules/security.py` | truncated | **255** |
| `modules/security_hardening.py` | not truncated | **304** |

There was never a contradiction. There were **two different functions** and I compared a measurement
of one against a test of the other.

**The transferable rules:**

1. A test's assertion is about *the module it imports*. Read the `import` line before judging a test.
2. A name collision across modules is itself a finding — here, two functions with the same name and
   the same docstring intent that disagree about a security-relevant property.
3. **"I found a contradiction" is a hypothesis, not a result.** Run the test before announcing a bug.

---

## §7 Redraw checklist — you should be able to produce this from memory after the 1-hour gap

1. Draw the 7-stage pipeline, and put an arrow where the size check *is* and where it *should* be.
2. State the spool threshold (**1 MB**) and what it implies about disk vs RAM.
3. Give the exact order-of-magnitude answer for a 2 GB upload: **2 GB temp disk, 2 GB RAM, then
   rejected** — and name the fix (Content-Length, bounded read).
4. Give the measured amplification number for a DOCX bomb: **≈77×** (7.8 MB → 599 MB).
5. Explain why a shell script named `.pdf` is not a code-execution attack **here**, and name the
   missing control that makes it safe only by accident.
6. Define allowlist vs denylist in one line each, and say which one `ALLOWED_EXTENSIONS` is.
7. Write the correct containment predicate from memory: `p == root or p.startswith(root + os.sep)`.
8. Say what `read_any` does with an unknown extension, and what `add_document` does with a string
   starting with `"Error"`.
9. State `chunk_size` (500), the overlap (0), and the consequence of zero overlap.
10. Explain the `read_csv` trap: what the user believes happened vs what actually happened.

---

## §8 Explain-back questions (answer in your own words — no notes)

**Q1.** A user uploads a 2 GB file. Walk the bytes from the socket to the rejection. At which stage
does memory first get spent, how much, and why does the 10 MB check not prevent it? Then state the
two changes that would make the check actually *guard*.

**Q2.** You are shown a 9 MB `.docx` and told "the 10 MB limit protects us". Explain precisely why
that sentence is false, using the measured number. Name the three limits a correct implementation
would add, and say what `RLIMIT_AS` in a subprocess buys you that a ratio cap does not.

**Q3.** A file named `invoice.pdf` is actually a shell script. Give the full answer in three layers:
(a) does it execute, and what is the general name for the thing that would be needed for it to
execute; (b) does it parse; (c) what control is *absent* that would make this safe by design rather
than safe by accident. Then name two future changes to this repo that would turn it into a live hole.

**Q4.** `security.py` contains both `get_upload_path` (line 134) and `is_path_within_roots` (line 152).
One of them has a containment bug. Which, what exactly is wrong, what is the correct predicate, and
why is the bug *latent* rather than *live* today? What would upgrade it from latent to live?

**Q5.** A user uploads a 1 000-row CSV of customers and later asks the agent "what is user 812's
email?". Trace the answer: what does `read_csv` return, what does `_split_text` see, and what will
the agent say? Is the failure a security bug, a correctness bug, or both — and what would you change?
Then explain why `chunk_size=500` with **no overlap** makes retrieval worse, in terms of the boundary
between chunk *n* and chunk *n+1*.

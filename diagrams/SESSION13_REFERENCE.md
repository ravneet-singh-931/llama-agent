# SESSION 13 REFERENCE — Memory: where facts live, and what survives a restart

`modules/memory.py` (666) · `modules/pg_store.py` (324) · `modules/redis_store.py` (137) ·
`api.py` `/remember` **2339**, `/recall` **2351**, `/facts` **2363**, `DELETE /facts/{key}` **2377**

**Baseline: `pytest tests/test_conversation_isolation.py -q` → 10 passed, 11.3 s.**
Every number below was measured by constructing real objects.

---

## §0 The two answers

> **Q: where do facts live?**
> **A: in two places that do not agree — a Redis hash (`llama:facts`) and a JSON file
> (`memory/fact_memory.json`) — plus a PostgreSQL `facts` table that exists, is migrated, has an ORM model,
> and has never been written to.** `FactMemory.remember()` writes the JSON file **every time** and Redis
> **only if a connection exists**. `pg_store.py` does not mention facts at all (`grep -c fact` → **0**).

> **Q: what happens to them on restart?**
> **A: it depends on the deployment, and the three answers are different.**
> **docker-compose** — survive: `./data/memory:/app/memory` is a bind mount.
> **k8s** — **gone**: `k8s/api.yaml` mounts only `uploads` and `logs`, both `emptyDir`, and Redis is
> explicitly non-persistent (`k8s/redis.yaml:95`, *"No volumeMounts — Redis data is ephemeral"*).
> **Two API pods, same restart** — the oldest surviving copy wins, because each pod rewrites the whole file
> from its own in-memory dict (§3).

---

## §1 Three stores, one write path

| store | where | written? | durable? |
|---|---|---|---|
| PostgreSQL `facts` | `migrations/…d8cc1ad144c8:75`, `modules/database.py:173` | **never** — `grep "Fact("` → only the class definition | table exists, no reader, no writer |
| Redis hash `llama:facts` | `memory.py:396` (`REDIS_KEY`) | only when `REDIS_URL` is set and reachable | **no** in k8s (`redis.yaml:95`); yes in docker-compose (`redis_data:/data`) |
| JSON `memory/fact_memory.json` | `memory.py:438` (`_save`) | **every** `remember()` / `forget()` / `clear()` | only if the path is on a volume |

The PostgreSQL table is the most interesting of the three. The migration creates it with a unique index on
`(user_id, key)` (`migrations/versions/d8cc1ad144c8_baseline_users_api_keys_conversations_.py:85`) — a
schema designed exactly for this feature — and nothing has ever inserted a row. This is the
"build, test, never wire" pattern from Session 12 (`resolve_target`) in its strongest form: **a table with a
constraint and no writer.**

### 1a. Where the JSON actually lands — measured

```
FactMemory().facts_file  ->  "memory/fact_memory.json"        (relative)
Dockerfile:76  WORKDIR /app
Dockerfile:132 RUN mkdir -p … memory …                        (the directory exists in the image)
```

So in the container the file is `/app/memory/fact_memory.json`. Whether that survives is entirely a
deployment question:

| deployment | mount for `/app/memory` | restart result |
|---|---|---|
| docker-compose (`docker-compose.yml:193`) | `./data/memory:/app/memory` | **survives** |
| k8s (`k8s/api.yaml:270–285`) | **none** — only `uploads` (emptyDir) and `logs` (emptyDir) | **lost on pod restart** |

A relative default path is a durability decision made by the *working directory*, not by the code. Measured:
the same `FactMemory()` started from a different cwd resolves to a different file and reports
`I don't remember anything about 'name'`.

---

## §2 The read path empties the local mirror — measured

`_read_all` (`memory.py:448`) is the canonical reader for `get_all_facts`:

```python
raw = self.redis.hgetall(self.REDIS_KEY)
...
self.facts = result          # ← REPLACES the in-memory mirror
return result
```

Measured with a fact on disk and Redis up but empty:

```
local facts after remember: ['alice::name']
redis hash:                 {}
get_all_facts("alice")   -> {}
local mirror AFTER the read: []          ← the read deleted the only in-process copy
file on disk still has:      ['alice::name']
```

The fact is not lost yet — but the next write makes it so, because `_save()` serialises `self.facts`:

```
recall after the read          -> "I don't remember anything about 'name'"
one more remember()            -> disk now holds ['alice::city']   ← 'name' gone for good
```

**A read is not supposed to be destructive.** The order inside `remember()` makes it worse: it writes the
file *before* Redis — `memory.py:499` (`_save()`) then `507` (`hset`) — so file and hash can disagree for the rest of the process's life,
and the file — the copy that survives Redis going away — is the one that gets drifted.

---

## §3 Two `FactMemory` objects, one file — measured

There are **two** live instances of `FactMemory` in a running API process:

| # | created at | role |
|---|---|---|
| 1 | `api.py:534` `app.state.facts = FactMemory()` | what `/remember`, `/recall`, `/facts` use |
| 2 | `llama_agent1.py:217` `facts = FactMemory()` (module import) | what the ReAct tool `remember_fact` uses |

`api.py:570` (`_llama_agent1.facts = app.state.facts`) rebinds the second to the first for the ReAct agent — but `llama_agent1.facts` was **already
constructed**, and its `__init__` ran `self.facts = self._load()`. Rebinding the name does not undo that.

On the JSON backend both objects hold a **whole-dict snapshot** and write the **whole dict** back
(`_save`, `memory.py:438`). Measured:

```
1) api_obj.remember("name","Ravneet","alice")   disk: ['alice::name']
2) tool_obj.remember("city","Delhi","alice")    disk: ['alice::city']
   >>> 'name' still on disk?  False
3) fresh object reads disk                      {'city': 'Delhi'}
```

**Last writer wins the entire file.** This is the same defect shape as a Redis `SET` against a hash — but
here it is two objects *in one process*, one of which exists only because the second was constructed before
the rebind.

---

## §4 The conversation store is better — and it is the contrast worth drawing

`ConversationMemory` handles the analogous problem correctly, and the difference is the storage model:

| | `FactMemory` | `ConversationMemory` |
|---|---|---|
| unit of write | **the whole dict** → whole file | **one conversation** → one key |
| owner check on read | `get_all_facts` filters by key prefix `uid::` | `get_conversation`/`get_messages` compare `conv["user_id"] != user_id` → `None`/`[]` |
| owner conflict on write | overwrites `self.facts[fk]` | `_resolve_conversation` (`memory.py:206`) **creates a fresh conversation** instead of rebinding |
| granularity | one file / one hash | one Redis key per conversation + a per-user index set |

Measured, both directions:

```
guest-aaa and guest-bbb both send conversation_id="default"
  guest-aaa -> "default"
  guest-bbb -> 67404c69-…            a NEW uuid; 'default' untouched  ✅

bob asks for alice's conversation id explicitly
  get_conversation("bob", alice_id) -> None          ✅
  get_messages("bob", alice_id)     -> []            ✅
  add_message(..., "bob", alice_id) -> f923dbc6-…    (a new conversation)  ✅
  alice's conversation still has 0 messages                               ✅
```

So the module contains **both** patterns side by side: one store that re-keys copies to avoid collision,
and one that overwrites a shared file from a private snapshot. `ConversationMemory`'s docstring
(`memory.py:210–216`) even records the bug that taught it: *"a signed-up user's first message was silently
appended to another user's 'default' conversation and its owner was overwritten."* The lesson was applied to
conversations and not to facts.

---

## §5 The provenance feature that does not survive the restart

`History` exists "so *why does the agent think my name is X?* has an answer once it was overwritten"
(`memory.py:400–404`, `HISTORY_MAX = 500`). Measured:

```
in-process history():   [('Alice', 'Bob')]
files in the directory: ['f.json']                    ← facts only
after restart, history(): []
fact survived: 'Bob'
```

History is `RPUSH` **only into Redis**; with no Redis it is `self._history`, a plain list. Nothing writes it
to disk, nothing loads it, and **no endpoint exposes it** — `FactMemory.history()` (`594`) is called from
tests and nowhere in `api.py`. On the JSON backend, provenance dies with the process; on the Redis backend it
dies with Redis, which in k8s has no volume.

So: **the fact is durable, the reason for the fact is not** — on exactly the deployment that keeps facts,
and not on the one that keeps Redis.

---

## §6 Retention: what disappears without anyone asking

| rule | value | consequence |
|---|---|---|
| `CONV_MEMORY_TTL` (`memory.py:62`) | **604 800 s = 7 days** | a conversation key expires; the per-user index is pruned lazily on next list (`_convs`, 163–170) |
| `max_history` (`memory.py:59`) | **50 messages/conversation** | measured: 60 written → **50 stored**, first kept is `msg-10` |
| `HISTORY_MAX` (`403`) | 500 | oldest change records dropped |
| facts TTL | **none** | facts never expire — only `forget()`/`clear()` remove them |

Measured on the 50-message cap: **no marker, no truncation flag, no count.** The oldest 10 messages vanish
and `get_messages` returns a list that looks complete. `get_context_string` (`320`) then assembles the
model's history from that list, so the model is reading a 50-message window it believes is the conversation.

---

## §7 Multi-replica: two pods, two fact universes — measured

```
podA.remember("team","platform","alice")
podB.get_all_facts("alice")  -> {}
podB.recall("team","alice")  -> "I don't remember anything about 'team'"
```

`FactMemory.__init__` connects Redis **once** (`_init_redis`, 413) and prints
`[FACTS] Redis unavailable, using local JSON file: …`. If `REDIS_URL` is unset the fallback is silent in
every subsequent request — the store simply behaves as pod-local. `docker-compose.yml:163` sets it
(`REDIS_URL=redis://redis:6379`); `.env.example:83` leaves it **commented out**, so the default local
deployment runs the pod-local path with no warning beyond one startup line.

### 7a. Guest identity, checked rather than assumed

`FactMemory._norm_user` (`468`) maps `guest`, `default`, `""` and `None` → `"default"`, while the API's
`_request_uid` (`modules/doc_identity.py:66`) issues a per-browser `guest-<cookie-uuid>`. Measured: two
distinct guest cookies get distinct fact namespaces and cannot see each other
(`get_all_facts` → `{}`). **The isolation holds** — because the uid is unique per browser, the shared
`"default"` bucket is never reached by the HTTP path. It is still a live fallback for any caller that omits
`user_id`, and `FactMemory.recall`/`remember` default to `"default"` when nobody passes one.

---

## §8 Findings

| ID | Location | Finding |
|---|---|---|
| **F84** | `modules/database.py:173`, `migrations/…d8cc1ad144c8:75` | **A `facts` table with a unique `(user_id, key)` index, migrated and modelled, and no writer.** `grep -c fact modules/pg_store.py` → **0**; the only `Fact(` occurrence is the class definition. The durable store for this feature exists and is unused. |
| **F85** | `memory.py:448–466` | **`_read_all` replaces the in-memory mirror with whatever Redis holds** (`self.facts = result`). Measured: Redis up but empty → a read empties the local copy; the next `remember()` then writes the emptied dict over the on-disk file. **A read destroys data two steps later.** |
| **F86** | `memory.py:438` + `api.py:534`/`llama_agent1.py:217` | **Two `FactMemory` objects serialise the whole dict to one file — last writer wins.** Measured: object A remembers `name`, object B remembers `city`, `name` is gone. `api.py:570` rebinds the *name* only; the second object's snapshot was taken in `__init__`. |
| **F87** | `k8s/api.yaml:270–285` + `k8s/redis.yaml:95` | **In k8s, facts are lost on every pod restart.** No volume mounts `/app/memory` (only `uploads`/`logs`, both `emptyDir`) and Redis has no `volumeMounts`. The JSON default path is durable only in docker-compose (`docker-compose.yml:193`). |
| **F88** | `memory.py:569` + `api.py` | **Fact history is written nowhere durable and exposed nowhere.** Redis-only (`lpush`), in-memory otherwise; `history()` has no HTTP route. Measured: `[('Alice','Bob')]` in-process → `[]` after restart, while the fact itself survived. The provenance feature fails on the deployment that keeps the fact. |
| **F89** | `memory.py:59` + `253–256` | **The 50-message cap drops history silently.** Measured 60 → 50, oldest `msg-10`…, no marker and no count; `get_context_string` presents the remainder as the conversation. |
| **F90** | `memory.py:499` (`_save()`) then `507` (`hset`) | **File and Redis are written in an order that guarantees divergence.** `remember()` `_save()`s first, then `hset`s. If the `hset` fails it prints and keeps the local copy; if the process dies between the two, the file is ahead and the hash is behind with no reconciliation. |
| **F91** | `api.py:534` vs `llama_agent1.py:217` (rebind at `api.py:570`) | **Two instances of the same store exist because construction order beat the rebind.** The import-time `FactMemory()` in `llama_agent1` reads the file before `api.py` binds `app.state.facts`; the rebind at `api.py:570` fixes the *reference*, not the *snapshot*. |

**Positive, measured:** the conversation store's ownership model is correct on every path tested —
`_resolve_conversation` re-keys instead of rebinding (`memory.py:206`), and both `get_conversation` (275) and
`get_messages` (287) compare `conv["user_id"]` and return `None`/`[]` on mismatch. Guest fact isolation also
holds (§7a).

---

## §9 Interview phrasing

> *"Facts live in two places that can disagree — a Redis hash and a JSON file — and the database table
> that was designed for them has never been written to. On restart it depends entirely on the deployment:
> docker-compose mounts the memory directory and they survive; the k8s manifest mounts only uploads and logs,
> both emptyDir, and Redis has no volume either, so a pod restart takes every fact with it. The interesting
> part is the failure mode before that. The JSON backend writes the whole dict back on every change, and
> there are two FactMemory objects in one process, so the second one to write erases the first one's facts —
> I measured a name disappearing when a different object remembered a city. And a plain read can destroy
> data two steps later: the read replaces the in-memory mirror with Redis's contents, and the next write
> flushes that emptied mirror onto disk. The conversation store doesn't have this problem, because it keys
> one conversation per Redis key and refuses to rebind an owner instead of overwriting — the module contains
> both patterns side by side."*

---

## §10 Redraw checklist

1. Name the **three** stores and which one is never written (`facts` table).
2. Say what `remember()` writes and **in what order** (file first, then Redis — F90).
3. Say what survives a restart on **docker-compose** vs **k8s** (F87).
4. Give the two-object clobber, with the measured key that vanished (F86).
5. Explain why a *read* can lose data: `_read_all` reassigns the mirror (F85).
6. Contrast with `ConversationMemory`: one key per conversation, and `_resolve_conversation` re-keys rather
   than rebinds.
7. Say what the 50-message cap does to the model's context, and whether anything marks it (F89).
8. Say where fact history lives and whether any route exposes it (F88).

---

## §11 Explain-back questions

**Q1.** `/remember` succeeds, the pod restarts, `/facts` is empty. Name every store the fact could have
reached, say which one it actually reached, and give the two deployment facts that decide whether it
survives. Then: why does the PostgreSQL `facts` table not save you?

**Q2.** Two objects call `FactMemory.remember()` in the same process. Give the measured sequence in which
one fact erases the other, name the line that writes, and say why `api.py:570`'s rebind does not prevent it.

**Q3.** `get_all_facts()` is a read. Explain, with the measured state transitions, how calling it can cause a
fact to be deleted later. Name the assignment and the function that later flushes it.

**Q4.** `ConversationMemory` and `FactMemory` store per-user data. One of them is safe against ownership
collisions and one is not. Show the difference in one call each, measured, and name the storage decision
that separates them.

**Q5.** A conversation has 60 messages. Say what `get_messages()` returns, what `get_context_string()` puts
in the prompt, and what the model can and cannot know about the missing ten.

**Q6.** The fact-history feature exists so *"why does the agent think my name is X?"* has an answer. Say
where history is written, where it is read, and what happens to it on restart **on the JSON backend** and
**on the Redis backend in k8s**. Name the finding.

# SESSION 14 REFERENCE — The WebSocket, the LLM wrapper, and why auth leaves the header

`llama_agent1.py` (865) · `modules/websocket.py` (398) · `api.py` `/ws/ticket` **1163**,
`/ws/{user_id}` **1173**, ticket store **1121–1160**, `/chat/stream` streaming call **2256**

**Baseline:** `pytest tests/test_s14_probe.py tests/test_s14_stream.py tests/test_s14_twostore.py -q`
→ **6 passed**. Everything below measured through the real app (`TestClient`) or the real functions.

---

## §0 The answer

> **Q: why can a WebSocket not simply send the JWT in an `Authorization` header like every other endpoint?**
>
> **A: because the browser gives you no way to set one.** The `WebSocket` constructor is
> `new WebSocket(url, protocols)` — two positional arguments, a URL and a *subprotocol* string
> (`frontend/src/lib/api.ts:433`). There is no third argument and no options object. The
> `Authorization` header is set by `fetch`/`XMLHttpRequest`/the browser itself, and none of them
> constructs a WebSocket. So of the three channels an HTTP request has, the socket has **one**:
> the URL. That is why every WebSocket auth scheme in the wild is a URL-shaped credential —
> `?token=`, a cookie that rides along, or the ticket this repo chose.
>
> The repo's choice is the interesting half: instead of putting the **long-lived** JWT in the URL
> (where it lands in proxy logs, browser history and `Referer`), it puts a
> **60-second, single-use, 122-bit random** value there (`api.py:1121–1146`), exchanged for it by an
> ordinary authenticated `POST`. The socket's only channel is a URL, so the design shrinks what a
> leaked URL is worth.

**The mechanism error to avoid:** saying *"WebSockets don't support custom headers."* On the **server**
side they do — this endpoint could read `websocket.headers["authorization"]` today. The constraint is
the **browser client API**, and it is a one-way constraint: a native client (Python, Go, an app) *can*
set the header, and then this backend has no handler for it. That is the real trade, and it is a real
gap: see F95.

---

## §1 The channel table — why the URL is all that is left

| what an HTTP request can carry | how the browser sets it | does it exist on a browser WebSocket? |
|---|---|---|
| `Authorization: Bearer …` | `fetch(url, {headers})` | **no** — the constructor takes no headers |
| `Cookie` | browser attaches automatically | **yes** — same-site, so it rides the handshake |
| URL query / path | `new WebSocket("wss://…/ws/x?ticket=…")` | **yes** — the only channel you control |
| body | `fetch(url, {body})` | **no** — the handshake has no body |

The repo has code for all three survivors. `api.py:1197–1219`:

| branch | line | identity |
|---|---|---|
| `?ticket=` | 1197–1200 | the ticket's user — **preferred** |
| `?token=` | 1204–1209 | the JWT's user — **kept, but it is the leak the ticket exists to avoid** |
| `access_token` cookie | 1212–1216 | the JWT's user |
| neither | 1218 | `guest-<uuid>` |

The cookie branch is worth noticing: **the JWT already reaches this endpoint without any query
parameter at all**, because `httponly` + `samesite=lax` (`api.py:826–835`) means the browser sends it on
the upgrade request. So the ticket is not solving "how does the socket learn who I am" — the cookie
already answers that. It is solving "how does a client that has no cookie (or a cross-origin one) get
an identity onto the socket **without minting a long-lived URL credential**."

---

## §2 The ticket, measured end to end

Run through the real app:

```
POST /ws/ticket  (no auth)          -> 401
POST /ws/ticket  (logged in)        -> 200 {"ticket": "36b4de74-…", "expires_in": 60}
WS /ws/x?ticket=<ticket>            -> "Welcome ws_df5cbcd8! You are connected."
WS /ws/x?ticket=<same ticket>       -> WebSocketDisconnect   (single use)
WS /ws/x?ticket=garbage             -> WebSocketDisconnect   (WS 1008)
```

Properties, each one a deliberate choice:

| property | implementation | why |
|---|---|---|
| unguessable | `uuid.uuid4()` (`api.py:1132`) | 122 bits of randomness; not a signed claim, so no key to leak |
| short-lived | `_WS_TICKET_TTL = 60` (`1122`) | bounds the window a leaked URL is useful for |
| single-use | in-memory `pop` (`1152`) / Redis `GETDEL` (`redis_store.py:128`) | a ticket found in a log tomorrow is worthless |
| cross-replica | Redis when configured (`1137`); in-memory fallback | a ticket minted on pod A redeems on pod B |

`redis_store.pop_json` (`119–137`) is the part that is actually correct under concurrency: it uses
`GETDEL`, falling back to a `MULTI/EXEC GET+DEL` pipeline, so two replicas redeeming the same ticket
cannot both win. The in-memory branch uses `dict.pop`, which is atomic under the GIL. **This is a rare
check-and-consume that is genuinely atomic** — contrast Session 12's check-then-act limits (F66).

But the two branches are not equally strong, and the gap is F93 below.

---

## §3 The socket is not real-time — measured

`modules/websocket.py` is described as "real-time chat" (`api.py:1173`, module docstring at 1). It is
not. Measured counts in that file:

```
'stream' occurrences: 0
'chunk'  occurrences: 0
'delta'  occurrences: 0
```

`_handle_chat` (`315`) calls `await run_agent(...)` — **the non-streaming agent** — and sends the
finished string in one frame:

```
frames received: ['agent_status', 'response']
content in the single frame: "first second third fourth fifth"
kwargs the WS passed to run_agent: ['conversation_id', 'user_id']
```

So the only "real-time" part is the `agent_status: thinking` frame (`330–334`) sent before the wait.
The whole answer then arrives at once. A socket that is `await`ing `run_agent` for 30 s has delivered
two frames: "thinking" and the answer.

**The irony is precise:** `run_agent_stream` exists (`llama_agent1.py:730`), yields tokens
(`786–794`), and is what `/chat/stream` uses (`api.py:2256`). The WebSocket — the transport *built* for
server push — does not call it, and the SSE endpoint — a workaround for one-way HTTP — does.

---

## §4 The WebSocket and the HTTP routes do not share conversation memory — measured

The WS handler calls `run_agent` **without** `memory=` or `facts=` (`modules/websocket.py:353–357`),
so `run_agent` falls back to its module globals (`llama_agent1.py:635–636`). Every HTTP route passes
`app.state.memory` / `app.state.facts` explicitly (`api.py:1681–1682`, `1892–1893`).

`api.py` rebinds two of the three at startup:

```
api.py:570   _llama_agent1.rag   = app.state.rag      ✅ rebound
api.py:571   _llama_agent1.facts = app.state.facts    ✅ rebound
             _llama_agent1.memory                      ❌ never rebound (grep count: 0)
```

Measured in the booted app:

```
llama_agent1.memory: 0x7f3a960915d0
app.state.memory   : 0x7f3a877f77d0
SAME OBJECT? False
files: memory/conversation_memory.json | memory/conversation_memory.json   ← same file

WS writes -> HTTP store sees: []
WS writes -> WS   store sees: ['via WEBSOCKET']
HTTP writes -> HTTP store sees: ['via HTTP']
HTTP writes -> WS   store sees: ['via WEBSOCKET']
```

**A conversation held over the WebSocket is invisible to every HTTP endpoint, and vice versa** — the
`GET /conversations/{id}` route cannot see a socket conversation, and the socket cannot see an HTTP
one. They are also pointed at the same file, which makes it worse rather than better:

```
A created: 36caaebb | B created: e3fd67e3
disk now: ['e3fd67e3-…']                    ← B's create already destroyed A's

after A writes a message:
  conversations on disk: ['36caaebb-…']
  bob's conversation still there? False
  fresh process sees: ['36caaebb-…']
  bob's conversation found by fresh process: None
```

Both objects were constructed at import (`llama_agent1.py:216`, `api.py:533`), so **both loaded the
same empty file and both hold a snapshot from before either wrote.** Every `_save()` (`memory.py:97`)
dumps the whole `self.data` — so each write erases the other object's conversations. Measured, and
order-dependent:

```
B boots AFTER A has written -> B loads A's data -> both survive   (safe)
both boot before either writes -> loser's conversations vanish   (the live case)
```

`app.state.memory` never being handed to the agent is the one-line omission; the clobber is its
consequence, and it is the same whole-dict `_save` defect as Session 13's F86 — here with real users
attached rather than a class-contract hazard.

---

## §5 Anonymous sockets get a fresh identity every connection — measured

`api.py:1218` sends an unauthenticated socket down `_guest_uid(websocket.cookies.get(GUEST_COOKIE))`.
Measured:

```
no cookie, two connections: guest-553d4218…  guest-c42e4034…   | same? False
a real guest cookie      -> guest-0f8ef405…                    (stable)
```

Two anonymous connections from a client that does not persist cookies get **different identities and
therefore different histories**. For a browser this is mostly covered — the cookie middleware sets
`GUEST_COOKIE` on the HTTP response (`api.py:826–835`), so a browser that has made one HTTP request
arrives with a cookie. A bare WS client (a script, a non-browser shell) has no such request, and each
reconnect becomes a new user. The docstring at `1187–1189` states this as intended behaviour ("a fresh
random id — can't read anyone's history"); the cost is that it also cannot read *its own* history.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F92** | `api.py:1174` vs browser `WebSocket` | **The `?ticket=` design is forced by the client, not by the protocol.** The server *can* read an `Authorization` header on the upgrade; the browser cannot set one (`frontend/src/lib/api.ts:433`). The URL is the only client-controlled channel, so the design minimizes what a URL leak is worth: 60 s (`1122`), single-use (`1152`), 122-bit random (`1132`). |
| **F93** | `api.py:1121` + `redis_store.py:128` | **The single-use guarantee is only atomic on the Redis path.** Redis uses `GETDEL`/`MULTI-EXEC GET+DEL`; the in-memory path uses `dict.pop` — fine in one process, but with `REDIS_URL` unset there is **one ticket store per replica**, so a ticket minted on pod A is *unknown* (not rejected for being reused) on pod B. Correctness of the control depends on a deployment variable. |
| **F94** | `api.py:1141–1144` | **The in-memory ticket store is unbounded on the Redis-configured path.** The expired-ticket purge is only reached when `set_json` returns False. Measured with Redis "configured but failing": **400 tickets → 400 entries**, no purge, ever. Every `/ws/ticket` call while Redis is down leaks a dict entry for the process lifetime. |
| **F95** | `api.py:1204` | **The `?token=` branch re-introduces exactly the leak the ticket exists to prevent.** `api.py:1113–1116` documents that the JWT must not go in the WS URL because it leaks via proxy logs, browser history and `Referer` — and then accepts it there as a documented fallback. The mitigation is present and the hole it closes is still open, by design. |
| **F96** | `modules/websocket.py:315–390` | **The "real-time" socket does not stream.** `stream`/`chunk`/`delta` occur **0 times** in the module; `_handle_chat` awaits the non-streaming `run_agent` and sends one `response` frame. Measured: `['agent_status','response']` — two frames for a whole answer. `run_agent_stream` (`llama_agent1.py:730`) exists and is used by the *SSE* route (`api.py:2256`) instead. |
| **F97** | `api.py:571` + `llama_agent1.py:216` | **The WebSocket and the HTTP routes use two different `ConversationMemory` objects that share one file.** `_llama_agent1.memory` is never rebound (grep count **0**; `rag` and `facts` are). Measured: WS write visible only to the WS store, HTTP write visible only to the HTTP store, same JSON path. |
| **F98** | `memory.py:97` + the two objects above | **Each `_save()` writes the whole conversation dict, so the two stores erase each other.** Measured: A creates a conversation, B creates one, A's is gone; then A writes a message and B's is gone. Both objects are built at import, so both load before either writes — the losing case is the live one. Same whole-dict defect as F86, now with users attached. |
| **F99** | `api.py:1218` + `modules/doc_identity.py:61` | **A coookie-less anonymous socket gets a new identity per connection.** Measured: `guest-553d…` vs `guest-c42e…` for two connections. Also inverts Session 13's guest-isolation design: separation is achieved by *forgetting* the user. |
| **F100** | `llama_agent1.py:259–266` | **`LLMMetadata` hardcodes `context_window=8192`, `num_output=1024` for every model.** `grep` finds no reader: its only occurrence in the repo is the literal. llama-index's `ChatResponse`/span export would carry this, so a 128 k-context model is described as an 8 k model to anything that reads the metadata. `fit_messages` (`786–787`) uses `llm_guard.input_budget`, not this — so the two budgets are independent and only one is enforced. |
| **F101** | `llama_agent1.py:234–256` + `1434–1449` of S12 | **`GroqLLM` selects a provider at construction and never revisits it.** `provider_for_model(self.model)` runs in `__init__`; `self._client` is fixed for the object's life. Combined with the module-level singletons (`llm` 382, `stream_llm` 388), the provider and model are frozen at import — the same freeze documented for tiers in S12 F73, one layer lower. |

**Positive, measured:** the ticket lifecycle itself is correct — 401 without auth, single-use enforced,
expiry enforced (`_consume_ws_ticket` returns `None` for an expired record), garbage rejected with WS
1008, and `pop_json`'s `GETDEL` makes cross-replica redemption atomic when Redis is up.

---

## §7 Interview phrasing

> *"It's a browser limitation, not a protocol one. `new WebSocket(url, protocols)` takes two arguments
> and neither is a header bag — the browser gives you no way to attach an Authorization header to the
> handshake. It will attach cookies for you, and you control the URL. So the socket's only credential
> channel is a URL, and that's a terrible place for a long-lived JWT: proxy access logs, browser
> history, Referer headers. The fix here is to trade a long-lived secret for a short-lived one — an
> authenticated POST mints a random ticket with a 60-second TTL, single-use, consumed with Redis GETDEL
> so two replicas can't both redeem it, and only the ticket goes in the URL. The honest caveats: the
> in-memory fallback is per-replica, so without Redis the single-use guarantee is per-pod rather than
> global; the endpoint still accepts the raw JWT as `?token=`, which is the exact leak the ticket
> avoids; and with the ticket solved, the socket doesn't actually stream — the handler awaits the
> non-streaming agent and sends one frame, while the SSE route uses the streaming path."*

---

## §8 Redraw checklist

1. Start from `new WebSocket(url, protocols)` — **two** arguments, no header bag.
2. Say the constraint is the **browser client**, not the server or the protocol.
3. Give the three surviving channels and note that the cookie already authenticates the upgrade.
4. Name the four properties of a ticket: random, 60 s, single-use, cross-replica.
5. Say *what makes it single-use atomically* — `GETDEL`, not get-then-delete.
6. Say where that guarantee **stops** (in-memory, per-replica — F93).
7. Name the two half-open doors: `?token=` (F95) and the unbounded store (F94).
8. Say what the socket does *after* auth: buffers, does not stream (F96).
9. Say which memory the socket writes to, and who can read it (F97/F98).

---

## §9 Explain-back questions

**Q1.** A candidate says: *"WebSockets can't carry an `Authorization` header, so we use a ticket."*
Correct them precisely — what can't, what can, and name the constructor that proves it. Then say why the
ticket is still the right design even though the server *could* have accepted the header.

**Q2.** The ticket is single-use. Name the exact mechanism that makes that true, say which Redis
command it relies on and why a `GET` followed by a `DEL` would be wrong, and say what the guarantee
degrades to when `REDIS_URL` is unset on a two-replica deployment.

**Q3.** `api.py:1113–1116` explains that a JWT in the WS URL leaks via proxy logs and `Referer` — and
`api.py:1204` accepts it there anyway. Is that a bug or a documented trade? Give the argument for each
side, then say which you would ship and what you would change.

**Q4.** A user chats over the WebSocket, then reloads the page and calls `GET /conversations`. What do
they see, and why? Name the two objects, the one line that was *not* written at startup, and the
function that makes the two objects destructive to each other.

**Q5.** The module is called real-time chat. Give the measurement that refutes the name, name the
function that would fix it, and say which route already calls it.

**Q6.** An anonymous client opens ten sockets and sends a message on each. How many conversations exist,
what are the identities, and where does that behaviour come from? Then say what a browser does
differently and why the answer is not the same.

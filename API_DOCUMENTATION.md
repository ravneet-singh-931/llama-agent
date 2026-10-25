# Llama Agent API Documentation

The interactive OpenAPI docs are always the source of truth:
`/docs` (Swagger UI) and `/openapi.json` on the API (through Nginx: `https://<host>/docs`).

## Base URL

| How you run it | Base URL |
|---|---|
| API directly (dev) | `http://localhost:8000` |
| Behind Nginx (Compose) / Ingress (k8s) | `https://<host>/api` (the `/api` prefix is stripped) |
| Next.js frontend | same-origin `/api` (rewritten to the API) |

## Authentication

Browsers use **HttpOnly cookies**. `POST /auth/login` and `POST /auth/register` set
`access_token` (short-lived, `JWT_EXPIRE_MINUTES`, default 30) and `refresh_token`
(`JWT_REFRESH_EXPIRE_DAYS`, default 7). An expired access cookie is renewed
automatically from the refresh cookie on the next request. You can also call
`POST /auth/refresh` yourself.

Scripts and other non-browser clients can send `Authorization: Bearer <access_token>`.

Auth levels used below:

| Level | Meaning |
|---|---|
| **public** | No identity needed |
| **optional** | Works anonymously. Anonymous visitors get a per-browser `guest_id` cookie, so each guest has private history, facts and documents. When logged in, data belongs to your user. |
| **login** | Requires a logged-in user (`401` otherwise) |
| **admin** | Requires a user with `is_admin = true` (`403` otherwise) |

To make an admin, set the flag in Postgres:
`UPDATE users SET is_admin = true WHERE username = 'alice';`

Every user's data is isolated: history, facts, uploaded documents, RAG and semantic caches, and WebSocket sessions.

---

## Endpoints

### Health & info

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | public | Name, version, feature list |
| GET | `/health` | public | `{status, uptime, version, modules}` |
| GET | `/tools` | public | Tools available to the ReAct agent |
| GET | `/ws/status` | public | `{total_connections, total_users}` (no identities) |

### Auth

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| POST | `/auth/register` | public | `{username, email, password, full_name?}` | `{status:"ok", user}` + cookies |
| POST | `/auth/login` | public | `{username, password}` (username **or** email) | `{status:"ok", user}` + cookies |
| POST | `/auth/refresh` | public | `{}` or `{refresh_token}` (cookie used by default) | `{status:"ok"}` + new access cookie |
| POST | `/auth/logout` | public | – | clears cookies |
| GET | `/auth/me` | login | – | `{user: {id, username, email, is_admin, ...}}` |

Validation:
- `username`: 3–50 characters from `[A-Za-z0-9_-]`.
- `email`: a valid email.
- `password`: 8–128 characters.

Login is brute-force protected: 5 failures lock the account for 15 minutes (`429`).

### Chat

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/chat` | optional | One-shot answer: `{response, timestamp, tools_used, response_time}` |
| POST | `/chat/stream` | optional | Server-Sent Events stream (used by the frontend) |

Request body (`ChatRequest`):

```json
{
  "message": "Plan a product launch",
  "conversation_id": "optional-id",
  "agent": "planner",
  "use_critic": true,
  "use_reflection": false,
  "use_parallel": true
}
```

- `agent` selects the agent explicitly. It is one of `research`, `coder`, `writer`, `analyzer`, `planner`, or `team` (the whole team collaborates). When omitted, the message is routed automatically.
- A message starting with `@team ...` also runs the whole team.
- `@Coder Agent ...` (and the other agent names) selects a single agent.
- `use_*` override the server's DSA defaults.

**SSE format** (`/chat/stream`): each event is a line `data: <payload>` followed by a blank line.
- Plain-text payloads are answer tokens. Newlines are escaped as `\n`.
- JSON payloads are structured events:
  - `{"type":"config","critic":..,"reflection":..,"parallel":..,"agent":..}`: the configuration that actually ran.
  - `{"type":"reasoning","stage":"route|critic|...","content":"..."}`: a reasoning step.
  - `{"type":"agent_start","agent":"Research","step":"s1"}` and `{"type":"agent_done",...}`: team progress.
- `data: [DONE]` ends the stream.

Special chat commands: `remember key = value`, `recall key`, `show facts`, `search <query>`.

### Facts & memory

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/remember` | optional | `{key, value}`: store a fact |
| POST | `/recall` | optional | `{key}` → `{key, value}` |
| GET | `/facts` | optional | `{facts: {key: value}}` |
| DELETE | `/facts/{key}` | optional | Delete one fact (URL-encode the key) |
| GET | `/memory?limit=50` | optional | `{stats, recent_messages}` |
| DELETE | `/memory` | optional | Clear your conversation memory |

### Conversations (sidebar history)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/conversations?limit=50&offset=0` | optional | `{conversations: [...], total, limit, offset}` |
| GET | `/conversations/{id}` | optional | Conversation with `messages` |
| POST | `/conversations` | optional | `{title?}`: create a conversation |
| DELETE | `/conversations/{id}` | optional | Delete a conversation |

### Documents (RAG)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/documents/upload` | optional | multipart `file`; indexed for **you only** |
| GET | `/documents` | optional | `{documents: ["report.pdf", ...]}` |
| POST | `/documents/search` | optional | `{query, top_k (1–20, default 3)}` → `{query, results}` |
| POST | `/documents/ask` | optional | `{query}` → `{query, answer}` (RAG + LLM) |
| POST | `/documents/read?file_path=report.pdf` | optional | Index one of *your* uploads by name |

Upload limits:
- Maximum size is 10 MB.
- Allowed types: `.txt .md .py .json .csv .pdf .docx`.
- Executables and scripts are rejected.

There is no document-delete endpoint. Re-uploading a file with the same name replaces it.

### Agents & MCP

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/agents/stats` | optional | Tasks handled per agent + API health |
| GET | `/mcp/status` | optional | MCP server info + tool list |
| POST | `/mcp/tools` | **login** | `{name, arguments}`: run an MCP tool (LLM cost) |

The MCP server itself (`mcp-server:8001`) is internal-only. Nginx and the Ingress do not expose it.

### WebSocket

1. `POST /ws/ticket` (**login**) returns `{ticket}`, which is single-use and valid for 60 s.
2. Connect to `wss://<host>/ws/<anything>?ticket=<ticket>`.

Identity is **never** taken from the path. Without a ticket, the server uses the access cookie if present, and otherwise the browser's guest id. An invalid ticket or token closes the socket with code `1008`.

- Client → server: `{"type": "chat", "data": {"content": "Hello"}}`, `{"type": "typing"}`, `{"type": "heartbeat"}`, `{"type": "history"}`
- Server → client: `connect` (welcome), `response` (`{"data": {"content": "..."}}`), `typing`, `agent_status`, `history`, `heartbeat`, `error`

With more than one API replica, tickets require Redis (`REDIS_URL`); otherwise each pod only knows its own tickets.

### Monitoring

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/metrics` | public, **internal only** | Prometheus format. Returns `404` when the request came through a proxy (`X-Forwarded-For` present), so it is never public. Set `METRICS_ALLOW_PROXIED=1` to opt out. |
| GET | `/metrics/json` | admin | JSON metrics |
| GET | `/monitoring/dashboard` | admin | Business metrics, LLM cost, SLA, security counters |

Useful Prometheus series:
- `llama_agent_requests_total{method,endpoint}`: `endpoint` is the route template, e.g. `/conversations/{conversation_id}`.
- `llama_agent_response_time_seconds_bucket`: until the last byte of the body (for streams, the end of the stream).
- `llama_agent_chat_seconds_bucket{route,outcome}`: what a user waits for a `/chat/stream` answer; the SLO metric (see [docs/SLO.md](docs/SLO.md)).
- `llama_agent_chat_first_token_seconds_bucket{route}`: time to the first answer text.
- `llama_agent_llm_tokens_total{model,kind}`
- `llama_agent_llm_cost_usd_total{model}`

---

## Errors

```json
{ "detail": "Error message" }
```

| Code | Meaning |
|---|---|
| 400 | Bad request (e.g. rejected upload) |
| 401 | Not logged in / token invalid |
| 403 | Not allowed (admin only) |
| 404 | Not found |
| 422 | Validation error |
| 429 | Rate limited (`retry_after` seconds) or login locked |
| 500 | Server error |

## Rate limits

Rate limits apply per client IP, across all endpoints except `/`, `/health` and `/metrics`:
- 60 requests per minute.
- 1000 requests per hour.

With `REDIS_URL` set, the limits are shared across replicas.

---

## Examples

### cURL (cookie jar)

```bash
# Register (or /auth/login) and keep the cookies
curl -c jar.txt -H "Content-Type: application/json" \
  -d '{"username":"ravneet","email":"r@example.com","password":"a-long-password"}' \
  http://localhost:8000/auth/register

# Chat
curl -b jar.txt -H "Content-Type: application/json" \
  -d '{"message":"What is the refund policy?"}' http://localhost:8000/chat

# Upload, then ask
curl -b jar.txt -F file=@refund_policy.pdf http://localhost:8000/documents/upload
curl -b jar.txt -H "Content-Type: application/json" \
  -d '{"query":"How many days do I have to return an item?"}' \
  http://localhost:8000/documents/ask
```

### Python

```python
import requests

s = requests.Session()                       # keeps the auth cookies
s.post("http://localhost:8000/auth/login",
       json={"username": "ravneet", "password": "a-long-password"}).raise_for_status()
print(s.post("http://localhost:8000/chat",
             json={"message": "@team plan a product launch"}).json()["response"])
```

### JavaScript (browser, same origin)

```javascript
await fetch("/api/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "ravneet", password: "a-long-password" }),
});
const res = await fetch("/api/chat", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "What is the refund policy?", agent: "research" }),
});
console.log((await res.json()).response);
```

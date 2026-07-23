# Llama Agent API Documentation

## Base URL

```
http://localhost:8000
```

## Authentication

All endpoints (except `/health` and `/auth/*`) require a JWT token in the header:

```
Authorization: Bearer <your_jwt_token>
```

---

## Endpoints

### 1. Health Check

Check if the API is running.

```
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-07-23T10:00:00Z"
}
```

**Status Codes:**
- `200` — API is healthy
- `503` — API is down

---

### 2. Register User

Create a new account.

```
POST /auth/register
```

**Request Body:**
```json
{
  "username": "ravneet",
  "password": "securepassword123"
}
```

**Response:**
```json
{
  "message": "User created successfully",
  "user_id": 1
}
```

**Status Codes:**
- `201` — Account created
- `400` — Username already exists
- `422` — Invalid input (username too short, password too weak)

**Validation Rules:**
- Username: 3-50 characters, alphanumeric + underscore
- Password: 8-100 characters

---

### 3. Login

Get a JWT token for authentication.

```
POST /auth/login
```

**Request Body:**
```json
{
  "username": "ravneet",
  "password": "securepassword123"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

**Status Codes:**
- `200` — Login successful
- `401` — Invalid username or password

**Usage:**
```bash
# Save token
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Use in subsequent requests
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/chat
```

---

### 4. Send Chat Message

Send a message to the AI and get a response.

```
POST /chat
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "message": "What is the refund policy?",
  "context": {
    "document_ids": ["doc_123", "doc_456"]
  }
}
```

**Response:**
```json
{
  "response": "Based on the uploaded documents, our refund policy allows returns within 30 days of purchase...",
  "sources": [
    {
      "document": "refund_policy.pdf",
      "similarity": 0.85,
      "chunk": "Refund Policy: All products can be returned within 30 days..."
    }
  ],
  "metadata": {
    "model": "llama3-8b-8192",
    "tokens_used": 256,
    "processing_time_ms": 1200
  }
}
```

**Status Codes:**
- `200` — Success
- `401` — Invalid or expired token
- `422` — Invalid input

---

### 5. WebSocket Chat

Real-time streaming chat connection.

```
WebSocket: ws://localhost:8000/ws/{token}
```

**Connection:**
```javascript
const token = "your_jwt_token";
const ws = new WebSocket(`ws://localhost:8000/ws/${token}`);

ws.onopen = () => {
  console.log("Connected!");
  ws.send(JSON.stringify({
    message: "What is the refund policy?"
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.response); // Streaming response chunks
};

ws.onclose = () => {
  console.log("Disconnected");
};
```

**Message Format (Client → Server):**
```json
{
  "message": "What is the refund policy?",
  "context": {
    "document_ids": ["doc_123"]
  }
}
```

**Message Format (Server → Client):**
```json
{
  "type": "chunk",
  "content": "Based on "
}
```
```json
{
  "type": "chunk",
  "content": "the uploaded "
}
```
```json
{
  "type": "complete",
  "full_response": "Based on the uploaded documents...",
  "metadata": {
    "tokens_used": 256
  }
}
```

**Status Codes:**
- `101` — Connection upgraded (success)
- `4001` — Invalid token
- `4002` — Token expired

---

### 6. Upload Document

Upload a file for the AI to read.

```
POST /documents/upload
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**Request Body (Form Data):**
```
file: @refund_policy.pdf
```

**Response:**
```json
{
  "message": "Document uploaded and processed",
  "document_id": "doc_123",
  "filename": "refund_policy.pdf",
  "chunks_created": 15,
  "file_size_kb": 245
}
```

**Status Codes:**
- `201` — Document uploaded
- `400` — Invalid file type
- `413` — File too large (max 50MB)

**Supported File Types:**
- PDF (`.pdf`)
- Text (`.txt`)
- CSV (`.csv`)
- Markdown (`.md`)
- JSON (`.json`)

---

### 7. List Documents

Get all uploaded documents for the current user.

```
GET /documents
Authorization: Bearer <token>
```

**Response:**
```json
{
  "documents": [
    {
      "id": "doc_123",
      "filename": "refund_policy.pdf",
      "file_type": "pdf",
      "file_size_kb": 245,
      "chunks_created": 15,
      "uploaded_at": "2026-07-23T10:00:00Z"
    },
    {
      "id": "doc_456",
      "filename": "product_manual.txt",
      "file_type": "txt",
      "file_size_kb": 120,
      "chunks_created": 8,
      "uploaded_at": "2026-07-23T11:00:00Z"
    }
  ],
  "total": 2
}
```

---

### 8. Delete Document

Remove a document from the system.

```
DELETE /documents/{document_id}
Authorization: Bearer <token>
```

**Response:**
```json
{
  "message": "Document deleted",
  "document_id": "doc_123"
}
```

**Status Codes:**
- `200` — Document deleted
- `404` — Document not found

---

### 9. Get Chat History

Retrieve conversation history for the current user.

```
GET /history
Authorization: Bearer <token>
Query Parameters:
  - limit: Number of messages (default: 50)
  - offset: Pagination offset (default: 0)
```

**Response:**
```json
{
  "messages": [
    {
      "id": 1,
      "role": "user",
      "content": "What is the refund policy?",
      "timestamp": "2026-07-23T10:00:00Z"
    },
    {
      "id": 2,
      "role": "assistant",
      "content": "Based on the uploaded documents...",
      "timestamp": "2026-07-23T10:00:02Z",
      "metadata": {
        "tokens_used": 256
      }
    }
  ],
  "total": 2,
  "has_more": false
}
```

---

### 10. Get Metrics

Prometheus metrics endpoint (no auth required).

```
GET /metrics
```

**Response:**
```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="POST",endpoint="/chat",status="200"} 150

# HELP http_request_duration_seconds Request duration
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.1"} 120
http_request_duration_seconds_bucket{le="0.5"} 145
...
```

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message explaining what went wrong"
}
```

**Common Error Codes:**

| Code | Meaning |
|------|---------|
| `400` | Bad request - check your input |
| `401` | Unauthorized - login again |
| `403` | Forbidden - you don't have access |
| `404` | Not found - resource doesn't exist |
| `413` | Payload too large - file is too big |
| `422` | Validation error - check field constraints |
| `429` | Rate limited - slow down |
| `500` | Server error - try again later |

---

## Rate Limits

| Endpoint | Limit | Window |
|----------|-------|--------|
| `/auth/login` | 5 requests | 1 minute |
| `/auth/register` | 3 requests | 1 minute |
| `/chat` | 30 requests | 1 minute |
| `/documents/upload` | 10 requests | 1 minute |

**Rate Limit Headers:**
```
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 25
X-RateLimit-Reset: 1690108800
```

---

## WebSocket Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `connect` | Client → Server | Establish connection |
| `message` | Client → Server | Send chat message |
| `chunk` | Server → Client | Streaming response chunk |
| `complete` | Server → Client | Response finished |
| `error` | Server → Client | Error occurred |
| `ping` | Both | Keep-alive |

---

## Examples

### Python
```python
import requests

# Login
response = requests.post("http://localhost:8000/auth/login", json={
    "username": "ravneet",
    "password": "securepassword123"
})
token = response.json()["access_token"]

# Chat
response = requests.post("http://localhost:8000/chat",
    headers={"Authorization": f"Bearer {token}"},
    json={"message": "What is the refund policy?"}
)
print(response.json()["response"])
```

### JavaScript
```javascript
// Login
const loginRes = await fetch("http://localhost:8000/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "ravneet", password: "securepassword123" })
});
const { access_token } = await loginRes.json();

// Chat
const chatRes = await fetch("http://localhost:8000/chat", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${access_token}`
  },
  body: JSON.stringify({ message: "What is the refund policy?" })
});
const { response } = await chatRes.json();
console.log(response);
```

### cURL
```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"ravneet","password":"securepassword123"}' \
  | jq -r '.access_token')

# Chat
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the refund policy?"}'
```

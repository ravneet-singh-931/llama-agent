// All API calls go through the same-origin proxy (NEXT_PUBLIC_API_URL defaults
// to "/api", which Next.js rewrites to the FastAPI backend). Because the calls
// are same-origin, the HttpOnly auth cookies set by the backend are sent
// automatically - we never touch tokens from JavaScript (no localStorage, no
// XSS-token-theft surface).
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// SSE event types streamed by /chat/stream that are NOT plain text tokens.
const SSE_EVENT_TYPES = new Set([
  "config",
  "reasoning",
  "agent_start",
  "agent_done",
  "final",
  "tool",
]);

export interface DsaOptions {
  use_critic?: boolean;
  use_reflection?: boolean;
  use_parallel?: boolean;
  agent?: string;
}

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

export class UnauthorizedError extends Error {
  status = 401;
  constructor(message = "Not authenticated") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

class ApiClient {
  // A single in-flight refresh shared by concurrent 401s.
  private refreshing: Promise<boolean> | null = null;

  private refreshSession(): Promise<boolean> {
    if (!this.refreshing) {
      this.refreshing = fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      })
        .then((r) => r.ok)
        .catch(() => false)
        .finally(() => {
          this.refreshing = null;
        });
    }
    return this.refreshing;
  }

  // fetch() that, on a 401, refreshes the access cookie once via the refresh
  // cookie and retries. (The backend middleware also renews transparently;
  // this covers the edge cases, e.g. the access cookie was evicted.)
  private async fetchAuth(path: string, init: RequestInit): Promise<Response> {
    const doFetch = () =>
      fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
    const res = await doFetch();
    if (res.status !== 401 || path.startsWith("/auth/")) return res;
    const ok = await this.refreshSession();
    return ok ? doFetch() : res;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const res = await this.fetchAuth(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      const err = await res.json().catch(() => ({ detail: "Not authenticated" }));
      throw new UnauthorizedError(err.detail || "Not authenticated");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // Auth (cookies are set by the backend; nothing to store client-side)
  async login(username: string, password: string) {
    return this.request<{ status: string; user?: unknown }>(
      "POST",
      "/auth/login",
      { username, password }
    );
  }

  async register(username: string, email: string, password: string) {
    return this.request<{ status: string; user?: unknown }>(
      "POST",
      "/auth/register",
      { username, email, password }
    );
  }

  async logout() {
    try {
      await this.request("POST", "/auth/logout");
    } catch {
      // Even if the server call fails, the local session is cleared by the store.
    }
  }

  async getMe() {
    return this.request<{ user: { id: string; username: string } }>(
      "GET",
      "/auth/me"
    );
  }

  // Chat
  async chat(message: string) {
    return this.request<{
      response: string;
      timestamp: string;
      response_time: number;
    }>("POST", "/chat", { message });
  }

  // Streaming chat via SSE. `options` carries the DSA feature toggles from the
  // control panel; `onEvent` receives structured events (config / reasoning /
  // agent_start / agent_done) so the UI can render a live reasoning view.
  chatStream(
    message: string,
    onToken: (token: string) => void,
    onDone: () => void,
    onError: (err: Error) => void,
    conversationId?: string | null,
    options?: DsaOptions | null,
    onEvent?: (event: StreamEvent) => void
  ): AbortController {
    const controller = new AbortController();

    const body: Record<string, unknown> = conversationId
      ? { message, conversation_id: conversationId }
      : { message };
    if (options) {
      for (const [k, v] of Object.entries(options)) {
        if (v !== undefined && v !== null) body[k] = v;
      }
    }

    this.fetchAuth("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const err = await response
            .json()
            .catch(() => ({ detail: "Stream error" }));
          onError(new Error(err.detail || `HTTP ${response.status}`));
          return;
        }
        const reader = response.body?.getReader();
        if (!reader) {
          onError(new Error("No response body"));
          return;
        }
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data.trim() === "[DONE]") {
              onDone();
              return;
            }
            // Structured SSE event vs. plain text token.
            const trimmed = data.trim();
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
              try {
                const parsed = JSON.parse(trimmed) as StreamEvent;
                if (parsed && SSE_EVENT_TYPES.has(parsed.type)) {
                  onEvent?.(parsed);
                  continue;
                }
              } catch {
                // Not JSON - fall through to treat as a text token.
              }
            }
            // Unescape newlines (the backend escapes \r\n in tokens).
            const text = data.replace(/\\n/g, "\n");
            onToken(text);
          }
        }
        onDone();
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          onError(err);
        }
      });

    return controller;
  }

  // Memory & Facts
  async getFacts() {
    return this.request<{ facts: Record<string, string> }>("GET", "/facts");
  }

  async remember(key: string, value: string) {
    return this.request("POST", "/remember", { key, value });
  }

  async recall(key: string) {
    return this.request<{ key: string; value: string }>("POST", "/recall", {
      key,
    });
  }

  async deleteFact(key: string) {
    return this.request("DELETE", `/facts/${encodeURIComponent(key)}`);
  }

  async clearMemory() {
    return this.request("DELETE", "/memory");
  }

  async getMemory() {
    return this.request<{
      stats: Record<string, number>;
      recent_messages: unknown[];
    }>("GET", "/memory");
  }

  // Conversations (per-user chat history, used by the sidebar)
  async getConversations() {
    return this.request<{
      conversations: {
        id: string;
        user_id: string;
        title: string;
        created_at: string;
        updated_at: string;
        message_count: number;
        last_message: string;
      }[];
    }>("GET", "/conversations");
  }

  async getConversation(id: string) {
    return this.request<{
      id: string;
      user_id: string;
      title: string;
      created_at: string;
      updated_at: string;
      message_count: number;
      last_message: string;
      messages: {
        role: "user" | "assistant";
        content: string;
        timestamp: string;
        tool_used?: string | null;
      }[];
    }>("GET", `/conversations/${encodeURIComponent(id)}`);
  }

  async createConversation(title?: string) {
    return this.request<{
      id: string;
      user_id: string;
      title: string;
      created_at: string;
      updated_at: string;
      message_count: number;
      last_message: string;
    }>("POST", "/conversations", title ? { title } : {});
  }

  async deleteConversation(id: string) {
    return this.request<{ status: string; conversation_id: string }>(
      "DELETE",
      `/conversations/${encodeURIComponent(id)}`
    );
  }

  // Documents
  async uploadDocument(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await this.fetchAuth("/documents/upload", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async searchDocuments(query: string, top_k = 3) {
    return this.request<{ query: string; results: unknown[] }>(
      "POST",
      "/documents/search",
      { query, top_k }
    );
  }

  async askDocuments(query: string) {
    return this.request<{ query: string; answer: string }>(
      "POST",
      "/documents/ask",
      { query }
    );
  }

  async listDocuments() {
    return this.request<{ documents: string[] }>("GET", "/documents");
  }

  // Tools
  async listTools() {
    return this.request<{ tools: { name: string; description: string }[] }>(
      "GET",
      "/tools"
    );
  }

  // MCP (Model Context Protocol) - talks to the MCP Agent Server via the API
  async mcpStatus() {
    return this.request<{
      connected: boolean;
      server: { name: string; version: string; protocolVersion: string };
      tools: { name: string; description: string }[];
    }>("GET", "/mcp/status");
  }

  async mcpToolCall(name: string, args: Record<string, string>) {
    return this.request<{ tool: string; isError: boolean; result: string }>(
      "POST",
      "/mcp/tools",
      { name, arguments: args }
    );
  }

  // Real Agent Team statistics (tasks handled per agent + live API health)
  async getAgentStats() {
    return this.request<{
      agents: {
        key: string;
        name: string;
        tasks_handled: number;
        last_used: string | null;
      }[];
      health: {
        status: string;
        uptime_seconds: number;
        uptime_human: string;
        total_requests: number;
        total_errors: number;
        error_rate: string;
      };
      metrics: {
        total_agents: number;
        tasks_handled_total: number;
      };
      generated_at: string;
    }>("GET", "/agents/stats");
  }

  // Health & Metrics
  async health() {
    return this.request<{
      status: string;
      uptime: string;
      version: string;
      modules: Record<string, string>;
    }>("GET", "/health");
  }

  // WebSocket: fetch a short-lived single-use ticket, then connect with it
  // (the JWT is never placed in the URL). Returns a same-origin ws/wss URL.
  async getWsUrl(userId: string): Promise<string> {
    let ticket = "";
    try {
      const res = await this.request<{ ticket: string }>("POST", "/ws/ticket");
      ticket = res.ticket;
    } catch {
      ticket = "";
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const base = `${proto}://${window.location.host}`;
    return ticket
      ? `${base}/ws/${userId}?ticket=${encodeURIComponent(ticket)}`
      : `${base}/ws/${userId}`;
  }
}

export const api = new ApiClient();

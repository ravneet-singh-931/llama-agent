// In production builds NEXT_PUBLIC_API_URL="/api" so all calls go through
// Nginx (same-origin, no CORS). In local dev it stays http://localhost:8000.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

class ApiClient {
  private token: string | null = null;

  constructor() {
    if (typeof window !== "undefined") {
      this.token = localStorage.getItem("access_token");
    }
  }

  setToken(token: string | null) {
    this.token = token;
    if (typeof window !== "undefined") {
      if (token) {
        localStorage.setItem("access_token", token);
      } else {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
      }
    }
  }

  getToken(): string | null {
    return this.token;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      h["Authorization"] = `Bearer ${this.token}`;
    }
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // Auth
  async login(username: string, password: string) {
    const data = await this.request<{
      access_token: string;
      refresh_token: string;
      token_type: string;
    }>("POST", "/auth/login", { username, password });
    this.setToken(data.access_token);
    return data;
  }

  async register(username: string, email: string, password: string) {
    const data = await this.request<{
      access_token: string;
      refresh_token: string;
    }>("POST", "/auth/register", { username, email, password });
    this.setToken(data.access_token);
    return data;
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

  // Streaming chat via SSE
  chatStream(
    message: string,
    onToken: (token: string) => void,
    onDone: () => void,
    onError: (err: Error) => void
  ): AbortController {
    const controller = new AbortController();
    const url = `${API_BASE}/chat/stream`;

    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      },
      body: JSON.stringify({ message }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const err = await response.json().catch(() => ({ detail: "Stream error" }));
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
            if (line.startsWith("data: ")) {
              const data = line.slice(6);
              if (data.trim() === "[DONE]") {
                onDone();
                return;
              }
              // Unescape newlines
              const text = data.replace(/\\n/g, "\n");
              onToken(text);
            }
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
    return this.request<{ key: string; value: string }>("POST", "/recall", { key });
  }

  async deleteFact(key: string) {
    return this.request("DELETE", `/facts/${key}`);
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

  // Documents
  async uploadDocument(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/documents/upload`, {
      method: "POST",
      headers: this.token
        ? { Authorization: `Bearer ${this.token}` }
        : {},
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

  // Health & Metrics
  async health() {
    return this.request<{
      status: string;
      uptime: string;
      version: string;
      modules: Record<string, string>;
    }>("GET", "/health");
  }

  async metrics() {
    return this.request("GET", "/metrics/json");
  }

  // WebSocket
  getWsUrl(userId: string): string {
    if (API_BASE.startsWith("http")) {
      // Local dev: ws://localhost:8000/ws/{user_id}
      return `${API_BASE.replace(/^http/, "ws")}/ws/${userId}?token=${this.token || ""}`;
    }
    // Production (same-origin via Nginx): wss://localhost/ws/{user_id}
    return `/ws/${userId}?token=${this.token || ""}`;
  }
}

export const api = new ApiClient();

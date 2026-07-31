export type MessageRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  agent?: string;
  timestamp: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  username: string;
  is_admin?: boolean;
}

export interface AgentInfo {
  name: string;
  role: string;
  status: "idle" | "thinking" | "working" | "done" | "error";
  icon: string;
  color: string;
}

export interface DocumentInfo {
  name: string;
  size?: number;
  type?: string;
  added_at?: string;
}

export interface Fact {
  key: string;
  value: string;
}

export interface HealthStatus {
  status: string;
  uptime: string;
  version: string;
  modules: Record<string, string>;
}

export interface MetricsData {
  health: HealthStatus;
  metrics: {
    total_requests: number;
    total_errors: number;
    avg_response_time: number;
    requests_last_hour: number;
  };
}

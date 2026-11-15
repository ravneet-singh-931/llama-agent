"use client";

// Agent Team dashboard backed by REAL data only.
// - Per-agent tasks handled / last used: counted by the API from actual
//   dispatches (chat multi-agent routing + MCP tool calls), served by
//   GET /agents/stats.
// - Health cards (requests, error rate, uptime): live PerformanceMonitor.
// - "Trigger Team Coordination" actually runs the MCP `collaborate` tool.
// No simulated values, no random mutations.

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Loader2,
  Bot,
  Zap,
  Brain,
  Search,
  FileText,
  Code,
  Workflow,
  Users,
  Activity,
  RefreshCw,
  AlertCircle,
  Database,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

// Static team definitions (names match multi_agent.py / the agents page).
// Runtime stats below come from the API.
const AGENT_DEFS = [
  { key: "research", name: "Research Agent", role: "Gathers and summarizes information", icon: Search },
  { key: "coder", name: "Coder Agent", role: "Writes, debugs, and explains code", icon: Code },
  { key: "writer", name: "Writer Agent", role: "Creates articles, docs, and content", icon: FileText },
  { key: "analyzer", name: "Analyzer Agent", role: "Finds patterns and insights in data", icon: Workflow },
  { key: "planner", name: "Planner Agent", role: "Builds plans, roadmaps, and strategies", icon: Brain },
] as const;

interface AgentStat {
  key: string;
  name: string;
  tasks_handled: number;
  last_used: string | null;
}
interface TeamStats {
  agents: AgentStat[];
  health: {
    // Process health only. Request/error counts used to live here too, but
    // they were per-pod and only counted chat + MCP traffic; Prometheus owns
    // them now (see modules/monitoring.py). "tasks_handled_total" is the
    // replicated counter this dashboard should show.
    status: string;
    uptime_human: string;
  };
  metrics: { total_agents: number; tasks_handled_total: number };
  generated_at: string;
}

type DerivedStatus = "active" | "idle" | "ready";

const ACTIVE_WINDOW_MS = 5 * 60 * 1000; // 5 minutes

function deriveStatus(lastUsed: string | null): DerivedStatus {
  if (!lastUsed) return "ready";
  return Date.now() - new Date(lastUsed).getTime() < ACTIVE_WINDOW_MS
    ? "active"
    : "idle";
}

function formatLastUsed(lastUsed: string | null): string {
  if (!lastUsed) return "Never";
  const minutes = Math.max(
    0,
    Math.floor((Date.now() - new Date(lastUsed).getTime()) / 60000)
  );
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ago`;
}

const statusStyles: Record<
  DerivedStatus,
  { dot: string; badge: string; label: string }
> = {
  active: { dot: "bg-emerald-500", badge: "bg-emerald-100 text-emerald-700", label: "Active" },
  idle: { dot: "bg-amber-500", badge: "bg-amber-100 text-amber-700", label: "Idle" },
  ready: { dot: "bg-gray-400", badge: "bg-gray-100 text-gray-700", label: "Ready" },
};

export function AgentCollaborationDashboard() {
  const [stats, setStats] = useState<TeamStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [collabTask, setCollabTask] = useState("");
  const [collabLoading, setCollabLoading] = useState(false);
  const [collabResult, setCollabResult] = useState<string | null>(null);

  // Promise-callback style (matches agents/page.tsx) so no setState runs
  // synchronously from the polling effect below.
  const refresh = useCallback(() => {
    api
      .getAgentStats()
      .then((data) => {
        setStats(data);
        setError(null);
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, []);

  // Load immediately, then poll every 10s so the dashboard stays live.
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const runCollaborate = async () => {
    const task =
      collabTask.trim() ||
      "Plan, research, and summarize the best approach to improve this product's user onboarding.";
    setCollabLoading(true);
    setCollabResult(null);
    try {
      const res = await api.mcpToolCall("collaborate", { task });
      setCollabResult(res.isError ? `Error: ${res.result}` : res.result);
      // A collaborate run used every agent — refresh stats right away.
      refresh();
    } catch (err) {
      setCollabResult(`Error: ${(err as Error).message}`);
    } finally {
      setCollabLoading(false);
    }
  };

  const totalHandled = stats?.metrics.tasks_handled_total ?? 0;
  const healthStatus = stats?.health.status ?? "unknown";

  return (
    <div className="space-y-6">
      {/* Metrics Overview (real data from the API) */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Agents</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {loading ? "—" : stats?.metrics.total_agents ?? "—"}
            </div>
            <p className="text-xs text-muted-foreground">Registered agents</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Tasks Handled</CardTitle>
            <Zap className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-600">
              {loading ? "—" : totalHandled}
            </div>
            <p className="text-xs text-muted-foreground">Real dispatches since API start</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Data Store</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-600">
              {loading ? "—" : healthStatus}
            </div>
            <p className="text-xs text-muted-foreground">
              Shared across replicas
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Agents</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div
              className={cn(
                "text-2xl font-bold",
                healthStatus === "healthy"
                  ? "text-emerald-600"
                  : "text-red-600"
              )}
            >
              {loading
                ? "—"
                : stats?.agents.filter((a) => a.last_used).length ?? 0}
            </div>
            <p className="text-xs text-muted-foreground">
              Agents used at least once
            </p>
          </CardContent>
        </Card>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-red-500">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>Could not load agent stats: {error}</span>
          <Button size="sm" variant="outline" className="ml-auto" onClick={refresh}>
            <RefreshCw className="mr-1 h-3 w-3" /> Retry
          </Button>
        </div>
      )}

      {/* Agent Status Grid (real tasks handled / last used) */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            Agent Status & Collaboration
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Active = handled a task in the last 5 minutes. Tasks and timestamps
            are counted from real dispatches by the API.
          </p>
        </CardHeader>
        <CardContent>
          {loading && !stats ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading real agent stats…
            </div>
          ) : (
            <div className="space-y-4">
              {AGENT_DEFS.map((def) => {
                const stat = stats?.agents.find((a) => a.key === def.key);
                const status = deriveStatus(stat?.last_used ?? null);
                const style = statusStyles[status];
                const Icon = def.icon;
                return (
                  <div
                    key={def.key}
                    className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 p-4 bg-muted/50 rounded-lg border"
                  >
                    <div className="flex items-center gap-4">
                      <div
                        className={cn(
                          "w-3 h-3 rounded-full",
                          style.dot
                        )}
                      />
                      <div className="flex size-8 items-center justify-center rounded-lg bg-secondary">
                        <Icon className="size-4 text-muted-foreground" />
                      </div>
                      <div>
                        <div className="font-medium">{def.name}</div>
                        <div className="text-sm text-muted-foreground">
                          {def.role}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <span
                        className={cn(
                          "flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium",
                          style.badge
                        )}
                      >
                        {style.label}
                      </span>
                      <span>Tasks: {stat?.tasks_handled ?? 0}</span>
                      <span>Last: {formatLastUsed(stat?.last_used ?? null)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {stats && (
            <p className="mt-4 text-right text-xs text-muted-foreground">
              Updated {formatLastUsed(stats.generated_at)} · Uptime{" "}
              {stats.health.uptime_human}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Collaboration Controls (real MCP actions only) */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Team Collaboration Controls
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <Input
                value={collabTask}
                onChange={(e) => setCollabTask(e.target.value)}
                placeholder="Task for the team (optional)…"
                className="max-w-md text-sm"
                disabled={collabLoading}
              />
              <Button onClick={runCollaborate} disabled={collabLoading} className="gap-2">
                {collabLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Zap className="h-4 w-4" />
                )}
                {collabLoading ? "Coordinating…" : "Trigger Team Coordination"}
              </Button>
              <Button variant="outline" onClick={refresh} disabled={loading} className="gap-2">
                <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                Refresh Statistics
              </Button>
            </div>
            {collabResult && (
              <pre className="max-h-48 overflow-auto rounded-lg border border-border bg-secondary/30 p-3 text-xs leading-relaxed text-muted-foreground whitespace-pre-wrap">
                {collabResult}
              </pre>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

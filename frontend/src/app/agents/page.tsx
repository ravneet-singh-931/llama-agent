"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Bot,
  Search,
  Code,
  FileText,
  Workflow,
  Brain,
  Activity,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Plug,
  Server,
  XCircle,
  Zap,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { AgentCollaborationDashboard } from "@/components/chat/AgentCollaborationDashboard";
import { Markdown } from "@/components/markdown/markdown";

const agents = [
  {
    name: "Research Agent",
    role: "Gathers and summarizes information from documents and the web",
    icon: Search,
    color: "from-cyan-500 to-blue-600",
    bgColor: "bg-cyan-500/10",
    textColor: "text-cyan-400",
    borderColor: "border-cyan-500/30",
    status: "ready" as const,
    tools: ["web_search", "read_document", "summarize", "extract_keywords"],
  },
  {
    name: "Coder Agent",
    role: "Writes, debugs, and explains code in Python, JavaScript, and more",
    icon: Code,
    color: "from-emerald-500 to-green-600",
    bgColor: "bg-emerald-500/10",
    textColor: "text-emerald-400",
    borderColor: "border-emerald-500/30",
    status: "ready" as const,
    tools: ["write_code", "debug", "explain", "refactor"],
  },
  {
    name: "Writer Agent",
    role: "Creates articles, documentation, emails, and creative content",
    icon: FileText,
    color: "from-amber-500 to-orange-600",
    bgColor: "bg-amber-500/10",
    textColor: "text-amber-400",
    borderColor: "border-amber-500/30",
    status: "ready" as const,
    tools: ["write_article", "draft_email", "create_doc", "rewrite"],
  },
  {
    name: "Analyzer Agent",
    role: "Analyzes data, finds patterns, and generates insights",
    icon: Workflow,
    color: "from-purple-500 to-violet-600",
    bgColor: "bg-purple-500/10",
    textColor: "text-purple-400",
    borderColor: "border-purple-500/30",
    status: "ready" as const,
    tools: ["analyze_data", "find_patterns", "generate_report", "visualize"],
  },
  {
    name: "Planner Agent",
    role: "Breaks down complex tasks into step-by-step action plans",
    icon: Brain,
    color: "from-rose-500 to-pink-600",
    bgColor: "bg-rose-500/10",
    textColor: "text-rose-400",
    borderColor: "border-rose-500/30",
    status: "ready" as const,
    tools: ["create_plan", "set_milestones", "assign_tasks", "track_progress"],
  },
];

const statusConfig = {
  ready: { icon: CheckCircle2, label: "Ready", className: "text-emerald-400" },
  thinking: {
    icon: Loader2,
    label: "Thinking",
    className: "text-amber-400 animate-spin",
  },
  error: { icon: AlertCircle, label: "Error", className: "text-red-400" },
};

export default function AgentsPage() {
  // MCP server status, fetched live from the API's /mcp/status endpoint
  // (the API connects to the MCP Agent Server over the MCP SSE protocol).
  const [mcpStatus, setMcpStatus] = useState<{
    connected: boolean;
    server: { name: string; version: string; protocolVersion: string };
    tools: { name: string; description: string }[];
  } | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [connectLoading, setConnectLoading] = useState(false);
  const [teamStatus, setTeamStatus] = useState<string | null>(null);
  const [teamStatusLoading, setTeamStatusLoading] = useState(false);
  const [toolLoading, setToolLoading] = useState<string | null>(null);
  const [toolInputs, setToolInputs] = useState<Record<string, string>>({});
  const [toolResults, setToolResults] = useState<Record<string, string>>({});

  useEffect(() => {
    api
      .mcpStatus()
      .then(setMcpStatus)
      .catch((err: Error) => setMcpError(err.message))
      .finally(() => setMcpLoading(false));
  }, []);

  // "Connect MCP": run the MCP handshake + tools/list again and unlock the
  // multi-agent tools. The MCP client opens a session per call, so "connect"
  // re-establishes the session and refreshes the exposed tools.
  const connectMcp = async () => {
    setConnectLoading(true);
    setMcpError(null);
    try {
      const status = await api.mcpStatus();
      setMcpStatus(status);
    } catch (err) {
      setMcpStatus(null);
      setMcpError((err as Error).message);
    } finally {
      setConnectLoading(false);
    }
  };

  // Tools that take a free-form "task" argument (all except team_status)
  const taskTools = new Set([
    "research_agent",
    "coder_agent",
    "writer_agent",
    "analyzer_agent",
    "planner_agent",
    "collaborate",
  ]);

  const runMcpTool = async (name: string) => {
    const task = (toolInputs[name] || "").trim();
    const args: Record<string, string> = taskTools.has(name)
      ? { task: task || "Analyze this topic and give a detailed, well-structured answer." }
      : {};
    setToolLoading(name);
    try {
      const res = await api.mcpToolCall(name, args);
      setToolResults((prev) => ({
        ...prev,
        [name]: res.isError ? `Error: ${res.result}` : res.result,
      }));
    } catch (err) {
      setToolResults((prev) => ({
        ...prev,
        [name]: `Error: ${(err as Error).message}`,
      }));
    } finally {
      setToolLoading(null);
    }
  };

  const runTeamStatus = async () => {
    setTeamStatusLoading(true);
    setTeamStatus(null);
    try {
      const res = await api.mcpToolCall("team_status", {});
      setTeamStatus(res.isError ? `Error: ${res.result}` : res.result);
    } catch (err) {
      setTeamStatus(`Error: ${(err as Error).message}`);
    } finally {
      setTeamStatusLoading(false);
    }
  };

  return (
    <AppShell>
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <div className="border-b border-border px-6 py-4">
          <div className="flex items-center gap-2">
            <Bot className="size-5 text-primary" />
            <h1 className="text-lg font-semibold">Agent Team</h1>
            <Badge variant="outline" className="ml-2 text-xs">
              MCP Protocol
            </Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Five specialized AI agents that collaborate to solve complex tasks
          </p>
        </div>

        {/* Agent Grid */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* MCP Server Status (live, via the Model Context Protocol) */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="mx-auto mb-6 max-w-5xl"
          >
            <Card className="overflow-hidden">
              <div className="flex items-start justify-between gap-4 border-b border-border p-5">
                <div className="flex items-start gap-4">
                  <div className="flex size-11 items-center justify-center rounded-xl bg-primary/10">
                    <Plug className="size-5 text-primary" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold">MCP Server Connection</h3>
                      {mcpLoading ? (
                        <Badge variant="outline" className="text-xs">
                          <Loader2 className="mr-1 size-3 animate-spin" />
                          Checking…
                        </Badge>
                      ) : mcpStatus?.connected ? (
                        <Badge className="bg-emerald-500/15 text-emerald-400 text-xs">
                          <CheckCircle2 className="mr-1 size-3" />
                          Connected
                        </Badge>
                      ) : (
                        <Badge variant="destructive" className="text-xs">
                          <XCircle className="mr-1 size-3" />
                          Disconnected
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      This page talks to the MCP Agent Server over the Model
                      Context Protocol (SSE). The API opens a live MCP session,
                      runs the{" "}
                      <code className="rounded bg-secondary px-1 py-0.5 text-xs">
                        initialize
                      </code>{" "}
                      handshake and discovers tools via{" "}
                      <code className="rounded bg-secondary px-1 py-0.5 text-xs">
                        tools/list
                      </code>
                      .
                    </p>
                  </div>
                </div>
                <Server className="size-5 shrink-0 text-muted-foreground" />
              </div>

              <div className="p-5">
                {mcpLoading ? (
                  <p className="text-sm text-muted-foreground">
                    Contacting MCP server…
                  </p>
                ) : mcpError ? (
                  <div className="flex items-center gap-2 text-sm text-red-400">
                    <AlertCircle className="size-4" />
                    <span>MCP server unreachable: {mcpError}</span>
                  </div>
                ) : mcpStatus ? (
                  <div className="grid gap-5 md:grid-cols-2">
                    <div>
                      <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                        Server
                      </p>
                      <p className="text-sm">
                        {mcpStatus.server.name}{" "}
                        <span className="text-muted-foreground">
                          v{mcpStatus.server.version}
                        </span>
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Protocol: {mcpStatus.server.protocolVersion} · MCP over
                        SSE
                      </p>
                    </div>
                    <div>
                      <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                        Exposed Tools ({mcpStatus.tools.length}) — click Run to
                        call them via MCP
                      </p>
                      <div className="space-y-2">
                        {mcpStatus.tools.map((tool) => (
                          <div
                            key={tool.name}
                            className="rounded-lg border border-border bg-secondary/20 p-2"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium capitalize">
                                {tool.name.replace(/_/g, " ")}
                              </span>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs"
                                onClick={() => runMcpTool(tool.name)}
                                disabled={toolLoading !== null}
                              >
                                {toolLoading === tool.name ? (
                                  <Loader2 className="mr-1 size-3 animate-spin" />
                                ) : (
                                  <Terminal className="mr-1 size-3" />
                                )}
                                Run
                              </Button>
                            </div>
                            {taskTools.has(tool.name) && (
                              <Input
                                value={toolInputs[tool.name] || ""}
                                onChange={(e) =>
                                  setToolInputs((prev) => ({
                                    ...prev,
                                    [tool.name]: e.target.value,
                                  }))
                                }
                                placeholder={`Task for ${tool.name
                                  .replace(/_/g, " ")
                                  .toLowerCase()}…`}
                                className="mt-1.5 h-7 text-xs"
                              />
                            )}
                            {toolResults[tool.name] && (
                              <div className="mt-1.5 max-h-40 overflow-auto rounded bg-background p-2 text-xs leading-relaxed text-muted-foreground">
                                <Markdown className="text-xs">
                                  {toolResults[tool.name]}
                                </Markdown>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : null}

                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Button
                    size="sm"
                    onClick={connectMcp}
                    disabled={connectLoading}
                  >
                    {connectLoading ? (
                      <Loader2 className="mr-2 size-3.5 animate-spin" />
                    ) : mcpStatus?.connected ? (
                      <RefreshCw className="mr-2 size-3.5" />
                    ) : (
                      <Plug className="mr-2 size-3.5" />
                    )}
                    {mcpStatus?.connected
                      ? "Refresh MCP Connection"
                      : "Connect MCP"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={runTeamStatus}
                    disabled={teamStatusLoading || !mcpStatus?.connected}
                  >
                    {teamStatusLoading ? (
                      <Loader2 className="mr-2 size-3.5 animate-spin" />
                    ) : (
                      <Zap className="mr-2 size-3.5" />
                    )}
                    Run team_status via MCP
                  </Button>
                  {teamStatus && (
                    <pre className="max-h-48 w-full overflow-auto rounded-lg border border-border bg-secondary/30 p-3 text-xs text-muted-foreground">
                      {teamStatus}
                    </pre>
                  )}
                </div>
              </div>
            </Card>
          </motion.div>

          <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent, i) => (
              <motion.div
                key={agent.name}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
              >
                <Card
                  className={cn(
                    "group relative overflow-hidden border bg-card p-5 transition-all hover:shadow-lg",
                    agent.borderColor
                  )}
                >
                  {/* Gradient background */}
                  <div
                    className={cn(
                      "pointer-events-none absolute inset-0 bg-gradient-to-br opacity-0 transition-opacity group-hover:opacity-5",
                      agent.color
                    )}
                  />

                  {/* Header */}
                  <div className="mb-4 flex items-start justify-between">
                    <div
                      className={cn(
                        "flex size-10 items-center justify-center rounded-lg",
                        agent.bgColor
                      )}
                    >
                      <agent.icon className={cn("size-5", agent.textColor)} />
                    </div>
                    <StatusBadge status={agent.status} />
                  </div>

                  <h3 className="mb-1 font-semibold">{agent.name}</h3>
                  <p className="mb-4 text-sm text-muted-foreground">
                    {agent.role}
                  </p>

                  {/* Tools */}
                  <div className="space-y-1.5">
                    <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      Tools
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {agent.tools.map((tool) => (
                        <Badge
                          key={tool}
                          variant="secondary"
                          className="text-[10px] font-normal"
                        >
                          {tool.replace(/_/g, " ")}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </Card>
              </motion.div>
            ))}
          </div>

          {/* Collaboration Section */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6 }}
            className="mx-auto mt-8 max-w-5xl"
          >
            <Card className="border-dashed p-6">
              <div className="flex items-start gap-4">
                <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10">
                  <Activity className="size-6 text-primary" />
                </div>
                <div>
                  <h3 className="mb-1 font-semibold">Multi-Agent Collaboration</h3>
                  <p className="mb-3 text-sm text-muted-foreground">
                    Agents work together via the Model Context Protocol (MCP).
                    When you ask a question, the system intelligently routes it
                    to the right agent or orchestrates multiple agents for
                    complex tasks.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {["Research → Analyze", "Code → Review", "Plan → Execute", "Write → Polish"].map(
                      (workflow) => (
                        <Badge
                          key={workflow}
                          variant="outline"
                          className="bg-secondary/50 text-xs"
                        >
                          {workflow}
                        </Badge>
                      )
                    )}
                  </div>
                </div>
              </div>
            </Card>
          </motion.div>

          {/* Enhanced Agent Collaboration Dashboard */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.8 }}
            className="mx-auto mt-8 max-w-5xl"
          >
            <AgentCollaborationDashboard />
          </motion.div>
        </div>
      </div>
    </AppShell>
  );
}

function StatusBadge({ status }: { status: "ready" | "thinking" | "error" }) {
  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <div className="flex items-center gap-1.5">
      <Icon className={cn("size-3", config.className)} />
      <span className="text-xs text-muted-foreground">{config.label}</span>
    </div>
  );
}

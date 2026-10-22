"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import {
  Send,
  Square,
  Bot,
  User,
  Loader2,
  Sparkles,
  Brain,
  Zap,
  Search,
  FileText,
  Check,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/markdown/markdown";
import { useChatStore } from "@/store/chat-store";
import { DsaControlPanel } from "@/components/chat/DsaControlPanel";
import { ReasoningView } from "@/components/chat/ReasoningView";

// Agent quick-picks. `key` is what the API expects in ChatRequest.agent
// (research/coder/writer/planner/analyzer, or "team" for the whole team).
const AGENTS = [
  { key: "research", name: "Research Agent", description: "Gathers and synthesizes information", icon: Search },
  { key: "coder", name: "Coder Agent", description: "Writes, debugs, and reviews code", icon: Zap },
  { key: "writer", name: "Writer Agent", description: "Articles, documentation, creative writing", icon: FileText },
  { key: "planner", name: "Planner Agent", description: "Strategies, roadmaps, project plans", icon: Brain },
  { key: "analyzer", name: "Analyzer Agent", description: "Data, trends, patterns, insights", icon: Sparkles },
  { key: "team", name: "Whole Team", description: "All agents collaborate on the task", icon: Users },
] as const;

const WELCOME =
  "Hello! I'm your AI assistant with multi-agent collaboration. I can help with " +
  "research, coding, writing, planning, and analysis. Pick an agent above (or " +
  "type `@team ...` to involve everyone), or just ask.";

export function EnhancedChatInterface() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const reasoning = useChatStore((s) => s.reasoning);
  const conversationId = useChatStore((s) => s.currentConversationId);
  const dsaOptions = useChatStore((s) => s.dsaOptions);
  const setDsaOptions = useChatStore((s) => s.setDsaOptions);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const cancelStream = useChatStore((s) => s.cancelStream);

  const lastDuration = useChatStore((s) => s.lastResponseTime);
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const selectedAgent = dsaOptions.agent ?? null;
  const selected = AGENTS.find((a) => a.key === selectedAgent) ?? null;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const selectAgent = (key: string | null) =>
    setDsaOptions({ ...dsaOptions, agent: key ?? undefined });

  const handleSend = () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput("");
    void sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  return (
    <div className="flex h-full flex-col">
      {/* Header with agent selection */}
      <Card className="mb-4">
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              Chat
            </CardTitle>
            <div className="flex items-center gap-2">
              {conversationId && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Check className="h-3 w-3 text-emerald-500" />
                  Conversation saved
                </Badge>
              )}
              {selected && (
                <Badge className="gap-1 border-primary/20 bg-primary/10 text-primary">
                  <Zap className="h-3 w-3" />
                  Active: {selected.name}
                </Badge>
              )}
              <div className="flex items-center gap-1">
                {AGENTS.map((agent) => (
                  <Button
                    key={agent.key}
                    variant="ghost"
                    size="icon"
                    className={cn("h-8 w-8", selectedAgent === agent.key && "bg-primary/10")}
                    onClick={() => selectAgent(selectedAgent === agent.key ? null : agent.key)}
                    title={`${agent.name}: ${agent.description}`}
                    aria-pressed={selectedAgent === agent.key}
                  >
                    <agent.icon className="h-4 w-4" />
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </CardHeader>
        {/* DSA toggles (critic / reflection / parallel + agent) */}
        <DsaControlPanel />
      </Card>

      {/* Messages */}
      <Card className="flex flex-1 flex-col">
        <ReasoningView events={reasoning} />
        <ScrollArea className="h-[500px] flex-1 pr-4">
          <div className="space-y-4 p-4">
            {messages.length === 0 && (
              <div className="flex gap-3">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-muted">
                  <Bot className="h-4 w-4" />
                </div>
                <div className="max-w-[70%] rounded-2xl rounded-tl-none bg-muted px-4 py-2">
                  <Markdown>{WELCOME}</Markdown>
                </div>
              </div>
            )}
            {messages.map((message, idx) => (
              <div
                key={message.id}
                className={cn("flex gap-3", message.role === "user" && "flex-row-reverse")}
              >
                <div
                  className={cn(
                    "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full",
                    message.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                  )}
                >
                  {message.role === "user" ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <div className={cn("flex max-w-[70%] flex-col gap-1", message.role === "user" && "items-end")}>
                  <div
                    className={cn(
                      "rounded-2xl px-4 py-2",
                      message.role === "user"
                        ? "rounded-tr-none bg-primary text-primary-foreground"
                        : "rounded-tl-none bg-muted"
                    )}
                  >
                    {message.role === "user" ? (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    ) : message.content ? (
                      <Markdown>{message.content}</Markdown>
                    ) : (
                      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                    <span suppressHydrationWarning>
                      {new Date(message.timestamp).toLocaleTimeString()}
                    </span>
                    {message.agent && (
                      <Badge variant="secondary" className="gap-1">
                        <Zap className="h-3 w-3" />
                        {message.agent}
                      </Badge>
                    )}
                    {idx === lastAssistantIdx && !isStreaming && lastDuration !== null && (
                      <span>⏱️ {lastDuration.toFixed(1)}s</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        {isStreaming && (
          <div className="flex items-center gap-2 px-4 py-2 text-muted-foreground">
            <Bot className="h-5 w-5 animate-pulse" />
            <span className="animate-pulse">
              {selected ? `${selected.name} is working...` : "Agent is thinking..."}
            </span>
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        )}
      </Card>

      {/* Input */}
      <Card className="mt-4">
        <CardContent className="pt-4">
          <div className="flex gap-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isStreaming
                  ? "Agent is thinking..."
                  : "Type your message... (Enter to send, @team to involve every agent)"
              }
              disabled={isStreaming}
            />
            {isStreaming ? (
              <Button onClick={cancelStream} size="icon" variant="outline" className="h-10 w-10" title="Stop">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button onClick={handleSend} disabled={!input.trim()} size="icon" className="h-10 w-10" title="Send">
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>

          {/* Quick actions: select an agent for the next messages */}
          <div className="mt-3 flex flex-wrap gap-2">
            {AGENTS.map((agent) => (
              <Button
                key={agent.key}
                variant={selectedAgent === agent.key ? "default" : "outline"}
                size="sm"
                className="gap-1"
                onClick={() => selectAgent(selectedAgent === agent.key ? null : agent.key)}
              >
                <agent.icon className="h-3 w-3" />
                {agent.name}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

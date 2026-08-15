"use client";

import { memo } from "react";
import { motion } from "framer-motion";
import { User, Bot, Code, FileText, Search, Workflow } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/types";
import { formatTime } from "@/lib/utils";
import { Markdown } from "@/components/markdown/markdown";

const agentIcons: Record<string, typeof Bot> = {
  research: Search,
  coder: Code,
  writer: FileText,
  analyzer: Workflow,
  planner: Workflow,
};

const agentColors: Record<string, string> = {
  research: "border-l-cyan-500 bg-cyan-500/5",
  coder: "border-l-emerald-500 bg-emerald-500/5",
  writer: "border-l-amber-500 bg-amber-500/5",
  analyzer: "border-l-purple-500 bg-purple-500/5",
  planner: "border-l-rose-500 bg-rose-500/5",
  single_agent: "border-l-primary bg-primary/5",
  collaborate: "border-l-blue-500 bg-blue-500/5",
};

const agentLabels: Record<string, string> = {
  research: "Research Agent",
  coder: "Coder Agent",
  writer: "Writer Agent",
  analyzer: "Analyzer Agent",
  planner: "Planner Agent",
  single_agent: "Llama Agent",
  collaborate: "All Agents",
};

interface ChatMessageProps {
  message: ChatMessage;
  isStreaming?: boolean;
}

export const ChatMessageBubble = memo(function ChatMessageBubble({
  message,
  isStreaming,
}: ChatMessageProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="message-enter flex items-start gap-3 px-4 py-3"
      >
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/20">
          <User className="size-4 text-primary" />
        </div>
        <div className="flex-1 space-y-1 overflow-hidden">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">You</span>
            <span className="text-xs text-muted-foreground">
              {formatTime(message.timestamp)}
            </span>
          </div>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <p className="text-sm leading-relaxed">{message.content}</p>
          </div>
        </div>
      </motion.div>
    );
  }

  const agent = message.agent || "single_agent";
  const AgentIcon = agentIcons[agent] || Bot;
  const agentColor = agentColors[agent] || agentColors.single_agent;
  const agentLabel = agentLabels[agent] || agent;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="message-enter flex items-start gap-3 px-4 py-3"
    >
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-lg",
          agent === "coder"
            ? "bg-emerald-500/20 text-emerald-400"
            : agent === "research"
              ? "bg-cyan-500/20 text-cyan-400"
              : agent === "writer"
                ? "bg-amber-500/20 text-amber-400"
                : agent === "analyzer"
                  ? "bg-purple-500/20 text-purple-400"
                  : "bg-primary/20 text-primary"
        )}
      >
        <AgentIcon className="size-4" />
      </div>
      <div className="flex-1 space-y-1 overflow-hidden">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{agentLabel}</span>
          <span className="text-xs text-muted-foreground">
            {formatTime(message.timestamp)}
          </span>
        </div>
        <div
          className={cn(
            "rounded-lg border-l-2 p-3 text-sm leading-relaxed",
            agentColor
          )}
        >
          {message.content ? (
            <Markdown>{message.content}</Markdown>
          ) : isStreaming ? (
            <span className="typing-cursor" />
          ) : null}
        </div>
      </div>
    </motion.div>
  );
});

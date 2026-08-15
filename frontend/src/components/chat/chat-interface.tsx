"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { Bot, MessageSquare } from "lucide-react";
import { ChatMessageBubble } from "./chat-message";
import { ChatInput } from "./chat-input";
import { DsaControlPanel } from "./DsaControlPanel";
import { ReasoningView } from "./ReasoningView";
import { useChatStore } from "@/store/chat-store";
import { ScrollArea } from "@/components/ui/scroll-area";

export function ChatInterface() {
  const {
    messages,
    isStreaming,
    sendMessage,
    cancelStream,
    reasoning,
  } = useChatStore();

  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      const el = scrollRef.current;
      requestAnimationFrame(() => {
        el.scrollTo({
          top: el.scrollHeight,
          behavior: "smooth",
        });
      });
    }
  }, [messages, isStreaming]);

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <div className="flex size-7 items-center justify-center rounded-md bg-primary/20">
          <Bot className="size-4 text-primary" />
        </div>
        <div>
          <h2 className="text-sm font-medium">Llama Agent</h2>
          <p className="text-[10px] text-muted-foreground">
            {isStreaming ? "Generating response..." : "Online"}
          </p>
        </div>
        {/* Online indicator */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="relative flex size-2">
            <span
              className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${
                isStreaming ? "bg-amber-400" : "bg-emerald-400"
              }`}
            />
            <span
              className={`relative inline-flex size-2 rounded-full ${
                isStreaming ? "bg-amber-400" : "bg-emerald-400"
              }`}
            />
          </span>
          <span className="text-xs text-muted-foreground">
            {isStreaming ? "Thinking..." : "Ready"}
          </span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <DsaControlPanel />
        <ReasoningView events={reasoning} />
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="max-w-md text-center"
            >
              <div className="mx-auto mb-6 flex size-16 items-center justify-center rounded-2xl bg-primary/10 glow-cyan">
                <MessageSquare className="size-8 text-primary" />
              </div>
              <h3 className="mb-2 text-lg font-semibold">
                Start a Conversation
              </h3>
              <p className="mb-6 text-sm text-muted-foreground">
                Ask me anything! I can help with research, coding, writing,
                analysis, planning, and more. Upload documents for deeper insights.
              </p>
              <div className="grid gap-2 text-left">
                {[
                  "Write a Python function to analyze CSV data",
                  "Research the latest trends in AI in 2026",
                  "Plan a strategy for launching a new product",
                  "Analyze this dataset and give me insights",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => sendMessage(suggestion)}
                    className="rounded-lg border border-border bg-card px-4 py-2.5 text-left text-sm text-muted-foreground transition-all hover:border-primary/30 hover:text-foreground"
                  >
                    <span className="mr-2 text-primary">→</span>
                    {suggestion}
                  </button>
                ))}
              </div>
            </motion.div>
          </div>
        ) : (
          <ScrollArea ref={scrollRef} className="h-full">
            <div className="py-4">
              {messages.map((msg, i) => (
                <ChatMessageBubble
                  key={msg.id}
                  message={msg}
                  isStreaming={
                    isStreaming &&
                    i === messages.length - 1 &&
                    msg.role === "assistant"
                  }
                />
              ))}
            </div>
          </ScrollArea>
        )}
      </div>

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        onStop={cancelStream}
        isStreaming={isStreaming}
      />
    </div>
  );
}

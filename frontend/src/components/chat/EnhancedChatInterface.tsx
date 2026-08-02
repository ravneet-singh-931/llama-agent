"use client";

import { useState, useRef, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Send, Bot, User, Loader2, Sparkles, Brain, Zap, Search, FileText, Copy, Check, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { Markdown } from "@/components/markdown/markdown";

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: Date;
  agentUsed?: string;
  toolsUsed?: string[];
  responseTime?: number;
  citations?: Citation[];
}

interface Citation {
  documentId: string;
  excerpt: string;
  relevanceScore: number;
}

interface AgentSuggestion {
  name: string;
  description: string;
  confidence: number;
}

interface EnhancedChatInterfaceProps {
  conversationId?: string | null;
  initialMessages?: Message[];
}

export function EnhancedChatInterface({
  conversationId = null,
  initialMessages = [],
}: EnhancedChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>(
    initialMessages.length > 0
      ? initialMessages
      : [
          {
            id: "1",
            role: "assistant",
            content: "Hello! I'm your enhanced AI assistant with multi-agent collaboration capabilities. I can help you with research, coding, writing, planning, and analysis tasks. How can I assist you today?",
            timestamp: new Date(),
          },
        ]
  );
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [agentSuggestions, setAgentSuggestions] = useState<AgentSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  const agents = [
    { name: "Research Agent", description: "Gathers and synthesizes information from multiple sources", icon: Search },
    { name: "Coder Agent", description: "Writes, debugs, and reviews code across multiple languages", icon: Zap },
    { name: "Writer Agent", description: "Creates content, articles, documentation, and creative writing", icon: FileText },
    { name: "Planner Agent", description: "Creates strategies, roadmaps, and project plans", icon: Brain },
    { name: "Analyzer Agent", description: "Analyzes data, trends, patterns, and provides insights", icon: Sparkles },
  ];

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    };
    const assistantId = (Date.now() + 1).toString();

    setMessages(prev => [
      ...prev,
      userMessage,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
      },
    ]);
    const currentInput = input;
    setInput("");
    setIsLoading(true);
    setShowSuggestions(false);
    setSelectedAgent(null);

    // Stream a real response from the backend over SSE
    const startTime = Date.now();
    let fullContent = "";

    api.chatStream(
      currentInput,
      (token) => {
        fullContent += token;
        setMessages(prev =>
          prev.map(m => (m.id === assistantId ? { ...m, content: fullContent } : m))
        );
      },
      () => {
        setIsLoading(false);
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId
              ? {
                  ...m,
                  content: fullContent.trim() || "No response received.",
                  responseTime: (Date.now() - startTime) / 1000,
                }
              : m
          )
        );
      },
      (err) => {
        setIsLoading(false);
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId
              ? { ...m, content: `⚠️ Stream error: ${err.message}` }
              : m
          )
        );
      },
      conversationId
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header with Agent Selection */}
      <Card className="mb-4">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              Enhanced Chat Interface
            </CardTitle>
            <div className="flex items-center gap-2">
              {conversationId && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Check className="h-3 w-3 text-emerald-500" />
                  Conversation saved
                </Badge>
              )}
              {selectedAgent && (
                <Badge className="bg-primary/10 text-primary border-primary/20 gap-1">
                  <Zap className="h-3 w-3" />
                  Active: {selectedAgent}
                </Badge>
              )}
              <div className="flex items-center gap-1">
                {agents.map(agent => (
                  <Button
                    key={agent.name}
                    variant="ghost"
                    size="icon"
                    className={cn("h-8 w-8", selectedAgent === agent.name && "bg-primary/10")}
                    onClick={() => setSelectedAgent(selectedAgent === agent.name ? null : agent.name)}
                    title={`Select ${agent.name}`}
                  >
                    <agent.icon className="h-4 w-4" />
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* Chat Messages */}
      <Card className="flex-1 flex flex-col">
        <ScrollArea className="flex-1 h-[500px] pr-4" ref={scrollAreaRef}>
          <div className="space-y-4 p-4">
            {messages.map((message) => (
              <div key={message.id} className={cn("flex gap-3", message.role === "user" && "flex-row-reverse")}>
                <div className={cn("w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0", 
                  message.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                )}>
                  {message.role === "user" ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <div className={cn("max-w-[70%] flex flex-col gap-1", message.role === "user" && "items-end")}>
                  <div className={cn("px-4 py-2 rounded-2xl", 
                    message.role === "user" ? "bg-primary text-primary-foreground rounded-tr-none" : "bg-muted rounded-tl-none"
                  )}>
                    {message.role === "user" ? (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    ) : (
                      <Markdown>{message.content}</Markdown>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
                    <span suppressHydrationWarning>
                      {message.timestamp.toLocaleTimeString()}
                    </span>
                    {message.agentUsed && (
                      <Badge variant="secondary" className="gap-1">
                        <Zap className="h-3 w-3" />
                        {message.agentUsed}
                      </Badge>
                    )}
                    {message.responseTime && (
                      <span>⏱️ {message.responseTime.toFixed(1)}s</span>
                    )}
                    {message.toolsUsed && message.toolsUsed.length > 0 && (
                      <Badge variant="outline" className="gap-1">
                        <Sparkles className="h-3 w-3" />
                        {message.toolsUsed.length} tools
                      </Badge>
                    )}
                  </div>
                  {message.citations && message.citations.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-muted/50">
                      <p className="text-xs font-medium text-muted-foreground mb-1">Sources:</p>
                      <div className="space-y-1">
                        {message.citations.map((citation, idx) => (
                          <div key={idx} className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 p-2 rounded">
                            <FileText className="h-3 w-3" />
                            <span className="flex-1 truncate">{citation.excerpt}</span>
                            <Badge variant="secondary" className="text-xs">
                              {(citation.relevanceScore * 100).toFixed(0)}%
                            </Badge>
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => copyToClipboard(citation.excerpt)}>
                              <Copy className="h-3 w-3" />
                            </Button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>
        
        {/* Typing Indicator */}
        {isLoading && (
          <div className="flex items-center gap-2 px-4 py-2 text-muted-foreground">
            <Bot className="h-5 w-5 animate-pulse" />
            <span className="animate-pulse">Agent is thinking...</span>
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        )}
      </Card>

      {/* Input Area */}
      <Card className="mt-4">
        <CardContent className="pt-4">
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={isLoading ? "Agent is thinking..." : "Type your message... (Enter to send, Shift+Enter for new line)"}
                disabled={isLoading}
                className="pr-12"
              />
              <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-1">
                {showSuggestions && agentSuggestions.length > 0 && (
                  <div className="absolute bottom-full right-0 mb-2 w-64 bg-popover border rounded-lg shadow-lg p-2 z-10">
                    {agentSuggestions.map((suggestion, idx) => (
                      <Button key={idx} variant="ghost" className="w-full justify-start gap-2 text-xs" onClick={() => setInput(suggestion.name)}>
                        <Sparkles className="h-3 w-3" />
                        {suggestion.name}
                      </Button>
                    ))}
                  </div>
                )}
                <Button 
                  onClick={handleSend} 
                  disabled={!input.trim() || isLoading}
                  size="icon"
                  className="h-10 w-10"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
          
          {/* Quick Actions */}
          <div className="flex flex-wrap gap-2 mt-3">
            {agents.map(agent => (
              <Button key={agent.name} variant="outline" size="sm" className="gap-1" onClick={() => setInput(`@${agent.name} `)}>
                <agent.icon className="h-3 w-3" />
                {agent.name}
              </Button>
            ))}
            <Button variant="outline" size="sm" className="gap-1" onClick={() => setInput("@team ")}> 
              <Users className="h-3 w-3" />
              Team
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
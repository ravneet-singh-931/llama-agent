"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  MessageSquare,
  Bot,
  FileText,
  Settings,
  LogOut,
  Plus,
  History,
  Brain,
  Trash2,
  BarChart3,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/auth-store";
import { useChatStore } from "@/store/chat-store";
import { ThemeToggle } from "./theme-toggle";

const navItems = [
  { href: "/dashboard", label: "Chat", icon: MessageSquare },
  { href: "/agents", label: "Agent Team", icon: Bot },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const { user, logout } = useAuthStore();
  const {
    conversations,
    currentConversationId,
    fetchConversations,
    loadConversation,
    deleteConversation,
    newConversation,
  } = useChatStore();

  useEffect(() => {
    fetchConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleNewChat = () => {
    newConversation();
    router.push("/dashboard");
  };

  const handleOpenConversation = (id: string) => {
    loadConversation(id);
    router.push("/dashboard");
  };

  const handleDeleteConversation = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    deleteConversation(id);
  };

  return (
    <div className="flex h-full flex-col border-r border-border bg-sidebar">
      {/* Logo */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
          <Brain className="size-4 text-primary-foreground" />
        </div>
        <span className="font-semibold tracking-tight">Llama Agent</span>
      </div>

      {/* New Chat */}
      <div className="p-3">
        <Button
          variant="outline"
          className="w-full justify-start gap-2"
          onClick={handleNewChat}
        >
          <Plus className="size-4" />
          New Chat
        </Button>
      </div>

      {/* Navigation */}
      <nav className="space-y-1 px-2">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Button
              key={item.href}
              variant="ghost"
              className={cn(
                "w-full justify-start gap-3 font-normal",
                isActive && "bg-accent/10 text-accent font-medium"
              )}
              onClick={() => router.push(item.href)}
            >
              <item.icon className="size-4" />
              {item.label}
              {isActive && (
                <motion.div
                  layoutId="sidebar-indicator"
                  className="ml-auto size-1.5 rounded-full bg-accent"
                />
              )}
            </Button>
          );
        })}
      </nav>

      {/* Previous Chats */}
      <div className="mt-4 flex min-h-0 flex-1 flex-col px-2">
        <div className="flex items-center gap-2 px-2 pb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          <History className="size-3.5" />
          Previous Chats
        </div>
        <div className="flex-1 overflow-y-auto space-y-0.5 pb-2">
          {conversations.length === 0 ? (
            <p className="px-2 py-1 text-xs text-muted-foreground/70">
              No chats yet — start a new one above.
            </p>
          ) : (
            conversations.map((conv) => {
              const isActive = conv.id === currentConversationId;
              return (
                <div
                  key={conv.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleOpenConversation(conv.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleOpenConversation(conv.id);
                  }}
                  className={cn(
                    "group flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent/10",
                    isActive && "bg-accent/10"
                  )}
                  title={conv.last_message || conv.title}
                >
                  <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate text-muted-foreground">
                    {conv.title || "Untitled chat"}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5 opacity-0 transition-opacity group-hover:opacity-100"
                    onClick={(e) => handleDeleteConversation(e, conv.id)}
                    title="Delete chat"
                  >
                    <Trash2 className="size-3" />
                  </Button>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Bottom */}
      <div className="border-t border-border p-3 space-y-2">
        <ThemeToggle />
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="flex size-6 items-center justify-center rounded-full bg-primary/20 text-xs font-medium text-primary">
            {user?.username?.charAt(0).toUpperCase() || "G"}
          </div>
          <span className="flex-1 truncate text-sm text-muted-foreground">
            {user?.username || "Guest"}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            onClick={() => {
              logout();
              router.push("/login");
            }}
          >
            <LogOut className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

export function MobileSidebar() {
  // Simplified mobile sidebar - just a minimal version
  return <Sidebar />;
}

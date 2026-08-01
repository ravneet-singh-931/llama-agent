"use client";

import { AppShell } from "@/components/layout/app-shell";
import { EnhancedChatInterface } from "@/components/chat/EnhancedChatInterface";
import { useChatStore } from "@/store/chat-store";

export default function DashboardPage() {
  const { messages, currentConversationId } = useChatStore();

  return (
    <AppShell>
      {/* key forces a fresh chat component when switching conversations */}
      <EnhancedChatInterface
        key={currentConversationId || "new"}
        conversationId={currentConversationId}
        initialMessages={messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.timestamp),
        }))}
      />
    </AppShell>
  );
}

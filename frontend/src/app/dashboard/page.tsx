"use client";

import { AppShell } from "@/components/layout/app-shell";
import { EnhancedChatInterface } from "@/components/chat/EnhancedChatInterface";

export default function DashboardPage() {
  // The chat reads messages / conversation / DSA options from the shared
  // chat store, so switching conversations in the sidebar updates it live.
  return (
    <AppShell>
      <EnhancedChatInterface />
    </AppShell>
  );
}

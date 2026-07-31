"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { EnhancedChatInterface } from "@/components/chat/EnhancedChatInterface";
import { useAuthStore } from "@/store/auth-store";

export default function DashboardPage() {
  const router = useRouter();
  const { isLoggedIn, user } = useAuthStore();

  useEffect(() => {
    // Remove the redirect - allow guest access
    // The AppShell will show login prompt if needed
  }, []);

  return (
    <AppShell>
      <EnhancedChatInterface />
    </AppShell>
  );
}

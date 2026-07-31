"use client";

import { useEffect } from "react";
import { useAuthStore } from "@/store/auth-store";

/**
 * Restores the logged-in user on page load (SSR never knows about the JWT,
 * which lives in localStorage). Without this, a hard refresh always shows
 * "Guest" even though the session token is still valid.
 */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const checkAuth = useAuthStore((s) => s.checkAuth);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  return <>{children}</>;
}

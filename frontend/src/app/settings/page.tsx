"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Settings,
  Shield,
  Palette,
  Key,
  Activity,
  RefreshCw,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useTheme } from "next-themes";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [health, setHealth] = useState<{
    status: string;
    uptime: string;
    version: string;
    modules: Record<string, string>;
  } | null>(null);
  // Starts true: the initial check runs on mount.
  const [checking, setChecking] = useState(true);

  // Resolves to the health payload (or an "unreachable" stand-in); state is
  // only set in the promise callbacks, never synchronously in an effect.
  const fetchHealth = () =>
    api.health().catch(() => ({
      status: "error",
      uptime: "N/A",
      version: "N/A",
      modules: { api: "unreachable" } as Record<string, string>,
    }));

  const checkHealth = () => {
    setChecking(true);
    fetchHealth()
      .then(setHealth)
      .finally(() => setChecking(false));
  };

  useEffect(() => {
    let alive = true;
    fetchHealth()
      .then((h) => {
        if (alive) setHealth(h);
      })
      .finally(() => {
        if (alive) setChecking(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <AppShell>
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <div className="border-b border-border px-6 py-4">
          <div className="flex items-center gap-2">
            <Settings className="size-5 text-primary" />
            <h1 className="text-lg font-semibold">Settings</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Configure your Llama Agent experience
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto grid max-w-3xl gap-6">
            {/* Theme */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <Card className="p-5">
                <div className="flex items-start gap-4">
                  <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Palette className="size-5 text-primary" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold">Appearance</h3>
                    <p className="mb-3 text-sm text-muted-foreground">
                      Choose between dark and light mode
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant={theme === "dark" ? "default" : "outline"}
                        size="sm"
                        onClick={() => setTheme("dark")}
                      >
                        Dark Mode
                      </Button>
                      <Button
                        variant={theme === "light" ? "default" : "outline"}
                        size="sm"
                        onClick={() => setTheme("light")}
                      >
                        Light Mode
                      </Button>
                    </div>
                  </div>
                </div>
              </Card>
            </motion.div>

            {/* API Configuration */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
            >
              <Card className="p-5">
                <div className="flex items-start gap-4">
                  <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Key className="size-5 text-primary" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold">API Configuration</h3>
                    <p className="text-sm text-muted-foreground">
                      The frontend connects to the FastAPI backend at{" "}
                      <code className="rounded bg-secondary px-1.5 py-0.5 text-xs">
                        {process.env.NEXT_PUBLIC_API_URL || "/api"}
                      </code>
                    </p>
                    <div className="mt-3 flex items-center gap-2 text-sm">
                      <Badge
                        variant="outline"
                        className={
                          health?.status === "healthy"
                            ? "border-emerald-500/30 text-emerald-400"
                            : "border-red-500/30 text-red-400"
                        }
                      >
                        {health?.status === "healthy" ? "Connected" : "Checking..."}
                      </Badge>
                      <span className="text-muted-foreground">
                        v{health?.version || "..."}
                      </span>
                    </div>
                  </div>
                </div>
              </Card>
            </motion.div>

            {/* System Health */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
            >
              <Card className="p-5">
                <div className="flex items-start gap-4">
                  <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Activity className="size-5 text-primary" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold">System Health</h3>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={checkHealth}
                        disabled={checking}
                      >
                        <RefreshCw
                          className={`mr-1 size-3 ${checking ? "animate-spin" : ""}`}
                        />
                        Refresh
                      </Button>
                    </div>
                    {health && (
                      <div className="mt-3 space-y-2">
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Status</span>
                          <Badge
                            variant="outline"
                            className={
                              health.status === "healthy"
                                ? "border-emerald-500/30 text-emerald-400"
                                : "border-red-500/30 text-red-400"
                            }
                          >
                            {health.status}
                          </Badge>
                        </div>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Uptime</span>
                          <span>{health.uptime}</span>
                        </div>
                        <Separator />
                        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                          Modules
                        </p>
                        {Object.entries(health.modules).map(([mod, status]) => (
                          <div
                            key={mod}
                            className="flex items-center justify-between text-sm"
                          >
                            <span className="capitalize text-muted-foreground">
                              {mod.replace(/_/g, " ")}
                            </span>
                            <span
                              className={
                                status === "active"
                                  ? "text-emerald-400"
                                  : "text-muted-foreground"
                              }
                            >
                              {status === "active" ? (
                                <CheckCircle2 className="inline size-3.5" />
                              ) : (
                                <XCircle className="inline size-3.5" />
                              )}
                              <span className="ml-1 text-xs">{status}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            </motion.div>

            {/* Security Info */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
            >
              <Card className="p-5">
                <div className="flex items-start gap-4">
                  <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Shield className="size-5 text-primary" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold">Security</h3>
                    <p className="mb-3 text-sm text-muted-foreground">
                      Security features are active
                    </p>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      {[
                        "JWT Authentication",
                        "Rate Limiting",
                        "SQL Injection Protection",
                        "XSS Prevention",
                        "CSRF Protection",
                        "Audit Logging",
                        "File Upload Validation",
                        "CORS Restrictions",
                      ].map((feature) => (
                        <div
                          key={feature}
                          className="flex items-center gap-2 rounded-lg bg-secondary/50 px-3 py-2"
                        >
                          <CheckCircle2 className="size-3.5 text-emerald-400" />
                          <span className="text-xs">{feature}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Card>
            </motion.div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

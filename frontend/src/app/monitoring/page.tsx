"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  BarChart3,
  Activity,
  Server,
  Shield,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCw,
  Database,
  DollarSign,
  Clock,
  TrendingUp,
  Zap,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  delay = 0,
}: {
  icon: any;
  label: string;
  value: string | number;
  sub?: string;
  delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay }}
    >
      <Card className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10">
            <Icon className="size-4.5 text-primary" />
          </div>
          <div className="flex-1">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {label}
            </p>
            <p className="text-xl font-semibold">{value}</p>
            {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
          </div>
        </div>
      </Card>
    </motion.div>
  );
}

export default function MonitoringPage() {
  const [dashboard, setDashboard] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, d, m] = await Promise.all([
        api.health(),
        api.getMonitoringDashboard(),
        api.getMetrics(),
      ]);
      setHealth(h);
      setDashboard(d);
      setMetrics(m);
    } catch (e: any) {
      setError(e.message || "Failed to load monitoring data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sla = dashboard?.sla;
  const compliance = sla?.compliance || {};
  const dbHealth = dashboard?.db_health?.checks || {};

  return (
    <AppShell>
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <div className="border-b border-border px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BarChart3 className="size-5 text-primary" />
              <h1 className="text-lg font-semibold">Monitoring</h1>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={refresh}
              disabled={loading}
            >
              <RefreshCw className={`mr-1 size-3 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            System health, business metrics, costs, SLA compliance, and
            observability — auto-refreshes every 30s.
          </p>
          {error && (
            <p className="mt-2 text-sm text-red-400">{error}</p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto grid max-w-6xl gap-6">
            {/* System Health */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <Card className="p-5">
                <div className="flex items-center gap-3 mb-4">
                  <Server className="size-5 text-primary" />
                  <h3 className="font-semibold">System Health</h3>
                </div>
                {health ? (
                  <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Status
                      </p>
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
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Uptime
                      </p>
                      <p className="text-sm">{health.uptime}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Version
                      </p>
                      <p className="text-sm">v{health.version}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Last updated
                      </p>
                      <p className="text-sm">
                        {new Date(dashboard?.timestamp).toLocaleTimeString()}
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Loading…</p>
                )}
                {health && (
                  <div className="mt-4 grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
                    {Object.entries(
                      (health.modules || {}) as Record<string, string>
                    ).map(([mod, status]) => (
                        <div
                          key={mod}
                          className="flex items-center justify-between rounded-lg bg-secondary/30 px-3 py-2"
                        >
                          <span className="capitalize text-muted-foreground">
                            {mod}
                          </span>
                          <span className="flex items-center gap-1 text-emerald-400">
                            <CheckCircle2 className="size-3" />
                            {status}
                          </span>
                        </div>
                      )
                    )}
                  </div>
                )}
              </Card>
            </motion.div>

            {/* Business Metrics + Performance */}
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
              <StatCard
                icon={Activity}
                label="Total Questions"
                value={dashboard?.business?.total_questions ?? "—"}
                delay={0}
              />
              <StatCard
                icon={TrendingUp}
                label="Success Rate"
                value={
                  dashboard?.business?.success_rate
                    ? `${dashboard.business.success_rate}%`
                    : "—"
                }
                delay={0.1}
              />
              <StatCard
                icon={Clock}
                label="Avg Response"
                value={
                  metrics?.metrics?.avg_response_time
                    ? `${metrics.metrics.avg_response_time.toFixed(2)}s`
                    : "—"
                }
                delay={0.2}
              />
              <StatCard
                icon={Zap}
                label="Total Queries"
                value={metrics?.metrics?.total_queries ?? "—"}
                delay={0.3}
              />
            </div>

            {/* SLA Compliance */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
            >
              <Card className="p-5">
                <div className="flex items-center gap-3 mb-4">
                  <Shield className="size-5 text-primary" />
                  <h3 className="font-semibold">SLA Compliance</h3>
                </div>
                {sla ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">
                        Overall: {sla.overall_status}
                      </span>
                      <Badge
                        variant="outline"
                        className={
                          sla.overall_status === "COMPLIANT"
                            ? "border-emerald-500/30 text-emerald-400"
                            : "border-red-500/30 text-red-400"
                        }
                      >
                        {sla.overall_status}
                      </Badge>
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                      {Object.entries(compliance).map(
                        ([key, val]: [string, any]) => (
                          <div
                            key={key}
                            className="flex items-center justify-between rounded-lg bg-secondary/30 px-3 py-2"
                          >
                            <span className="text-muted-foreground">
                              {key.replace(/_/g, " ")}
                            </span>
                            <span
                              className={
                                val.met
                                  ? "text-emerald-400"
                                  : "text-red-400"
                              }
                            >
                              {val.current}
                              {val.target && ` / ${val.target}`}
                              {val.met ? " ✓" : " ✗"}
                            </span>
                          </div>
                        )
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Loading…</p>
                )}
              </Card>
            </motion.div>

            {/* Costs */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
            >
              <Card className="p-5">
                <div className="flex items-center gap-3 mb-4">
                  <DollarSign className="size-5 text-primary" />
                  <h3 className="font-semibold">Costs & Token Usage</h3>
                </div>
                {dashboard?.costs ? (
                  <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Total Cost
                      </p>
                      <p className="font-semibold">
                        ${dashboard.costs.total_cost.toFixed(4)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Today
                      </p>
                      <p className="font-semibold">
                        ${dashboard.costs.today_cost.toFixed(4)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Total Tokens
                      </p>
                      <p className="font-semibold">
                        {dashboard.costs.total_tokens.toLocaleString()}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        Input / Output
                      </p>
                      <p className="font-semibold">
                        {dashboard.costs.total_input_tokens.toLocaleString()} /{" "}
                        {dashboard.costs.total_output_tokens.toLocaleString()}
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Loading…</p>
                )}
              </Card>
            </motion.div>

            {/* Alerts */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.4 }}
            >
              <Card className="p-5">
                <div className="flex items-center gap-3 mb-4">
                  <AlertCircle className="size-5 text-primary" />
                  <h3 className="font-semibold">Alerts</h3>
                </div>
                {dashboard?.alerts ? (
                  dashboard.alerts.length === 0 ? (
                    <div className="flex items-center gap-2 text-sm text-emerald-400">
                      <CheckCircle2 className="size-4" />
                      No active alerts
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {dashboard.alerts.map((a: any, i: number) => (
                        <div
                          key={i}
                          className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm"
                        >
                          {a.message || JSON.stringify(a)}
                        </div>
                      ))}
                    </div>
                  )
                ) : (
                  <p className="text-sm text-muted-foreground">Loading…</p>
                )}
              </Card>
            </motion.div>

            {/* Feature Flags + Database Health */}
            <div className="grid gap-6 lg:grid-cols-2">
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.5 }}
              >
                <Card className="p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <Zap className="size-5 text-primary" />
                    <h3 className="font-semibold">Feature Flags</h3>
                  </div>
                  {dashboard?.feature_flags ? (
                    <div className="space-y-2 text-sm">
                      {Object.entries(dashboard.feature_flags).map(
                        ([name, flag]: [string, any]) => (
                          <div
                            key={name}
                            className="flex items-center justify-between rounded-lg bg-secondary/30 px-3 py-2"
                          >
                            <span className="text-muted-foreground">
                              {name}
                            </span>
                            <Badge
                              variant="outline"
                              className={
                                flag.enabled
                                  ? "border-emerald-500/30 text-emerald-400"
                                  : "border-muted text-muted-foreground"
                              }
                            >
                              {flag.enabled ? "enabled" : "disabled"}
                            </Badge>
                          </div>
                        )
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Loading…</p>
                  )}
                </Card>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.6 }}
              >
                <Card className="p-5">
                  <div className="flex items-center gap-3 mb-4">
                    <Database className="size-5 text-primary" />
                    <h3 className="font-semibold">Database Health</h3>
                  </div>
                  {dashboard?.db_health ? (
                    <div className="space-y-2 text-sm">
                      <div className="flex items-center justify-between">
                        <span className="text-muted-foreground">Status</span>
                        <span
                          className={
                            dashboard.db_health.status === "healthy"
                              ? "text-emerald-400"
                              : "text-red-400"
                          }
                        >
                          {dashboard.db_health.status}
                        </span>
                      </div>
                      {Object.entries(dbHealth).map(
                        ([check, val]: [string, any]) => (
                          <div
                            key={check}
                            className="flex items-center justify-between rounded-lg bg-secondary/30 px-3 py-2"
                          >
                            <span className="text-muted-foreground">
                              {check}
                            </span>
                            <span
                              className={
                                val === "healthy" || val === true
                                  ? "text-emerald-400"
                                  : "text-red-400"
                              }
                            >
                              {typeof val === "object"
                                ? val?.status || JSON.stringify(val)
                                : String(val)}
                            </span>
                          </div>
                        )
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Loading…</p>
                  )}
                </Card>
              </motion.div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

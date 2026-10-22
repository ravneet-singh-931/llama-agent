"use client";

import { useChatStore } from "@/store/chat-store";
import type { DsaOptions } from "@/lib/api";

const AGENTS = [
  { key: "", label: "Auto (route)" },
  { key: "research", label: "Research" },
  { key: "coder", label: "Coder" },
  { key: "writer", label: "Writer" },
  { key: "analyzer", label: "Analyzer" },
  { key: "planner", label: "Planner" },
  { key: "team", label: "Whole team" },
];

const TOGGLES: { key: keyof DsaOptions; label: string }[] = [
  { key: "use_critic", label: "Critic" },
  { key: "use_reflection", label: "Reflect" },
  { key: "use_parallel", label: "Parallel" },
];

// Exported so other controls (agent quick-picks) stay in sync with the panel.
export const DSA_AGENTS = AGENTS;

export function DsaControlPanel() {
  const dsaOptions = useChatStore((s) => s.dsaOptions);
  const setDsaOptions = useChatStore((s) => s.setDsaOptions);

  const toggle = (key: keyof DsaOptions) =>
    setDsaOptions({ ...dsaOptions, [key]: !dsaOptions[key] });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs">
      <span className="font-medium text-muted-foreground">DSA:</span>
      {TOGGLES.map((t) => {
        const active = dsaOptions[t.key] === true;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => toggle(t.key)}
            className={`rounded-full border px-2.5 py-1 transition-colors ${
              active
                ? "border-primary bg-primary/10 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        );
      })}
      <select
        value={dsaOptions.agent ?? ""}
        onChange={(e) =>
          setDsaOptions({
            ...dsaOptions,
            agent: e.target.value || undefined,
          })
        }
        aria-label="Agent"
        className="rounded-full border border-border bg-background px-2.5 py-1 text-xs text-foreground"
      >
        {AGENTS.map((a) => (
          <option key={a.key} value={a.key}>
            {a.label}
          </option>
        ))}
      </select>
    </div>
  );
}

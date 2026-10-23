"use client";

import { useEffect, useState } from "react";
import { useChatStore } from "@/store/chat-store";
import { api } from "@/lib/api";
import type { ChatOptions, DsaDefaults, DsaOptions } from "@/lib/api";

const AGENTS = [
  { key: "", label: "Auto (route)" },
  { key: "research", label: "Research" },
  { key: "coder", label: "Coder" },
  { key: "writer", label: "Writer" },
  { key: "analyzer", label: "Analyzer" },
  { key: "planner", label: "Planner" },
  { key: "team", label: "Whole team" },
];

type ToggleKey = "use_critic" | "use_reflection" | "use_parallel";

const TOGGLES: { key: ToggleKey; label: string; server: keyof DsaDefaults }[] = [
  { key: "use_critic", label: "Critic", server: "critic" },
  { key: "use_reflection", label: "Reflect", server: "reflection" },
  { key: "use_parallel", label: "Parallel", server: "parallel" },
];

// Exported so other controls (agent quick-picks) stay in sync with the panel.
export const DSA_AGENTS = AGENTS;

export function DsaControlPanel() {
  const dsaOptions = useChatStore((s) => s.dsaOptions);
  const setDsaOptions = useChatStore((s) => s.setDsaOptions);

  // Server defaults: an untouched toggle shows what the server will
  // actually do, instead of looking "off" while the feature runs.
  const [options, setOptions] = useState<ChatOptions | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getChatOptions()
      .then((o) => {
        if (alive) setOptions(o);
      })
      .catch(() => {
        /* older backend: fall back to "off" display */
      });
    return () => {
      alive = false;
    };
  }, []);

  const effective = (t: (typeof TOGGLES)[number]) =>
    dsaOptions[t.key] ?? options?.defaults?.[t.server] ?? false;

  const toggle = (t: (typeof TOGGLES)[number]) =>
    setDsaOptions({ ...dsaOptions, [t.key]: !effective(t) } as DsaOptions);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs">
      <span className="font-medium text-muted-foreground">DSA:</span>
      {TOGGLES.map((t) => {
        const active = effective(t);
        const scope = options?.applies_to?.[t.server];
        const isDefault = dsaOptions[t.key] === undefined;
        return (
          <button
            key={t.key}
            type="button"
            aria-pressed={active}
            title={`${t.label}: ${active ? "on" : "off"}${
              isDefault && options ? " (server default)" : ""
            }${scope ? ` - applies to ${scope}` : ""}`}
            onClick={() => toggle(t)}
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

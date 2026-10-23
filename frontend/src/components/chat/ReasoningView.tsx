"use client";

import { useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";
import type { StreamEvent } from "@/lib/api";

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
      {children}
    </span>
  );
}

export function ReasoningView({ events }: { events: StreamEvent[] }) {
  const [open, setOpen] = useState(true);
  if (!events || events.length === 0) return null;

  // The backend sends one `config` event per route describing what actually
  // ran (chips); use the last one in case several arrive.
  const config = [...events].reverse().find((e) => e.type === "config") as
    | (StreamEvent & {
        critic?: boolean;
        reflection?: boolean;
        parallel?: boolean;
        agent?: string;
      })
    | undefined;

  // Reasoning stages plus agent-team progress (agent_start / agent_done).
  const reasoning = events.filter((e) =>
    ["reasoning", "agent_start", "agent_done"].includes(e.type)
  );
  const label = (e: StreamEvent) =>
    e.type === "agent_start"
      ? "agent"
      : e.type === "agent_done"
        ? "done"
        : String(e.stage ?? e.type);
  const text = (e: StreamEvent) =>
    e.type === "agent_start"
      ? `${String(e.agent ?? "Agent")} started`
      : e.type === "agent_done"
        ? `${String(e.agent ?? "Agent")} finished`
        : String(e.content ?? "");

  return (
    <div className="mx-4 mb-3 rounded-lg border border-border bg-secondary/40 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-muted-foreground"
      >
        <Sparkles className="size-3.5 text-primary" />
        <span className="font-medium">Reasoning &amp; DSA</span>
        {config && (
          <span className="ml-2 flex flex-wrap gap-1">
            {config.critic ? <Chip>Critic</Chip> : null}
            {config.reflection ? <Chip>Reflect</Chip> : null}
            {config.parallel ? <Chip>Parallel</Chip> : null}
            {config.agent ? <Chip>Agent:{config.agent}</Chip> : null}
          </span>
        )}
        <ChevronDown
          className={`ml-auto size-4 transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>
      {open && reasoning.length > 0 && (
        <div className="space-y-1.5 border-t border-border px-3 py-2">
          {reasoning.map((e, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-primary">
                {label(e)}
              </span>
              <span className="whitespace-pre-wrap text-muted-foreground">
                {text(e)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

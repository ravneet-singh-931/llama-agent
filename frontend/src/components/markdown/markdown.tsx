"use client";

// Generic, reusable Markdown renderer for AI-generated content (chat
// messages, agent tool results, document answers). Renders CommonMark + GFM
// (tables, strikethrough, task lists) with explicit Tailwind styling, so it
// does not depend on a typography plugin. Raw HTML is not rendered and URLs
// are sanitized by react-markdown's defaults.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface MarkdownProps {
  children: string;
  className?: string;
}

export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={cn("text-sm leading-relaxed", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-2 mt-3 text-xl font-bold tracking-tight first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-3 text-lg font-semibold tracking-tight first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-2.5 text-base font-semibold first:mt-0">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="mb-1.5 mt-2 text-sm font-semibold first:mt-0">
              {children}
            </h4>
          ),
          p: ({ children }) => (
            <p className="mb-2 whitespace-pre-wrap last:mb-0">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-primary/40 pl-3 italic text-muted-foreground last:mb-0">
              {children}
            </blockquote>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-primary underline underline-offset-2 hover:opacity-80"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-3 border-border" />,
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-border bg-muted px-2 py-1 text-left font-medium">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-border px-2 py-1 align-top">
              {children}
            </td>
          ),
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded-md border border-border bg-background/70 p-3 font-mono text-xs leading-relaxed last:mb-0">
              {children}
            </pre>
          ),
          code: ({ className, children }) => {
            const text = Array.isArray(children)
              ? children.map(String).join("")
              : String(children ?? "");
            // Fenced blocks carry a language class or span multiple lines;
            // inline code is single-line without a language class.
            const isBlock = Boolean(className) || text.includes("\n");
            if (isBlock) {
              return <code className="font-mono">{text}</code>;
            }
            return (
              <code className="rounded border border-border bg-background/70 px-1.5 py-0.5 font-mono text-[0.85em]">
                {text}
              </code>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

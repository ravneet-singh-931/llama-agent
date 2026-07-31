"use client";

import { motion } from "framer-motion";
import {
  Bot,
  MessageSquare,
  FileText,
  Search,
  Shield,
  BarChart3,
  Cpu,
  Globe,
  Brain,
  Database,
  Workflow,
  Lock,
} from "lucide-react";

const features = [
  {
    icon: Bot,
    title: "Multi-Agent Team",
    description: "5 specialized agents (Research, Coder, Writer, Analyzer, Planner) that collaborate on complex tasks.",
    color: "from-cyan-500/20 to-cyan-500/5",
    iconColor: "text-cyan-400",
  },
  {
    icon: MessageSquare,
    title: "Real-time Chat",
    description: "Streaming responses with WebSocket support for live multi-user conversations.",
    color: "from-emerald-500/20 to-emerald-500/5",
    iconColor: "text-emerald-400",
  },
  {
    icon: Search,
    title: "Advanced RAG",
    description: "Vector search with ChromaDB, re-ranking, sub-question decomposition, and router queries.",
    color: "from-purple-500/20 to-purple-500/5",
    iconColor: "text-purple-400",
  },
  {
    icon: FileText,
    title: "Document Intelligence",
    description: "Upload PDF, TXT, CSV, DOCX — the AI reads, understands, and answers from your documents.",
    color: "from-amber-500/20 to-amber-500/5",
    iconColor: "text-amber-400",
  },
  {
    icon: Brain,
    title: "Persistent Memory",
    description: "Conversation history stored in PostgreSQL with fact memory for long-term context.",
    color: "from-rose-500/20 to-rose-500/5",
    iconColor: "text-rose-400",
  },
  {
    icon: Workflow,
    title: "MCP Protocol",
    description: "Model Context Protocol enables standardized communication between AI agents and tools.",
    color: "from-blue-500/20 to-blue-500/5",
    iconColor: "text-blue-400",
  },
  {
    icon: Shield,
    title: "Enterprise Security",
    description: "JWT auth, API keys, SQL injection protection, XSS prevention, rate limiting, audit logs.",
    color: "from-green-500/20 to-green-500/5",
    iconColor: "text-green-400",
  },
  {
    icon: Database,
    title: "PostgreSQL + Redis",
    description: "Production database with async support, connection pooling, and Redis caching layer.",
    color: "from-indigo-500/20 to-indigo-500/5",
    iconColor: "text-indigo-400",
  },
  {
    icon: BarChart3,
    title: "Full Monitoring",
    description: "Prometheus metrics, Grafana dashboards (13 panels), Loki logs, custom alerts.",
    color: "from-orange-500/20 to-orange-500/5",
    iconColor: "text-orange-400",
  },
  {
    icon: Globe,
    title: "REST + WebSocket API",
    description: "Full FastAPI with auto-generated docs, WebSocket endpoints, and Prometheus metrics.",
    color: "from-cyan-500/20 to-cyan-500/5",
    iconColor: "text-cyan-400",
  },
  {
    icon: Cpu,
    title: "Docker Orchestration",
    description: "13 containers managed via docker-compose with health checks, secrets, and networks.",
    color: "from-sky-500/20 to-sky-500/5",
    iconColor: "text-sky-400",
  },
  {
    icon: Lock,
    title: "CI/CD Pipeline",
    description: "GitHub Actions with 8 jobs: matrix testing, security scanning, Docker build, deploy.",
    color: "from-violet-500/20 to-violet-500/5",
    iconColor: "text-violet-400",
  },
];

export function Features() {
  return (
    <section className="relative px-4 py-20 md:py-28">
      <div className="mx-auto max-w-6xl">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          className="mb-12 text-center"
        >
          <h2 className="mb-4 text-3xl font-bold tracking-tight md:text-4xl">
            Everything You Need
          </h2>
          <p className="mx-auto max-w-2xl text-muted-foreground">
            A production-ready AI agent platform with enterprise-grade features
            built for real-world use.
          </p>
        </motion.div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {features.map((feature, i) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ delay: i * 0.05 }}
              className="group relative overflow-hidden rounded-xl border border-border bg-card p-6 transition-all hover:border-primary/30"
            >
              {/* Background gradient */}
              <div
                className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${feature.color} opacity-0 transition-opacity group-hover:opacity-100`}
              />

              <div className="relative z-10">
                <div className={`mb-4 flex size-10 items-center justify-center rounded-lg bg-secondary ${feature.iconColor}`}>
                  <feature.icon className="size-5" />
                </div>
                <h3 className="mb-2 font-semibold">{feature.title}</h3>
                <p className="text-sm text-muted-foreground">
                  {feature.description}
                </p>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

"use client";

import { motion } from "framer-motion";
import { Hero } from "@/components/landing/hero";
import { Features } from "@/components/landing/features";
import { Footer } from "@/components/landing/footer";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background">
      {/* Minimal nav */}
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="fixed top-0 right-0 left-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur-md"
      >
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <div className="flex items-center gap-2 font-semibold">
            <div className="flex size-7 items-center justify-center rounded-md bg-primary">
              <span className="text-xs font-bold text-primary-foreground">LA</span>
            </div>
            <span>Llama Agent</span>
          </div>
          <nav className="flex items-center gap-4 text-sm">
            <a
              href="/login"
              className="text-muted-foreground transition-colors hover:text-foreground"
            >
              Sign In
            </a>
          </nav>
        </div>
      </motion.header>

      <Hero />
      <Features />
      <Footer />
    </div>
  );
}

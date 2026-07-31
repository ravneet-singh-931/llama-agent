"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  Upload,
  Search,
  File,
  FileSpreadsheet,
  FileCode,
  Loader2,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const fileIcons: Record<string, typeof File> = {
  pdf: FileText,
  csv: FileSpreadsheet,
  txt: FileText,
  py: FileCode,
  md: FileText,
  json: FileCode,
  default: File,
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResult, setSearchResult] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);

  // Load previously uploaded documents when the page mounts
  useEffect(() => {
    api
      .listDocuments()
      .then((res) => setDocuments(res.documents))
      .catch(() => {
        // Keep the list empty; uploading a new file still works
      });
  }, []);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);

    try {
      const result = await api.uploadDocument(file);
      setDocuments((prev) => [...prev, result.file || file.name]);
      setUploadSuccess(`Loaded: ${file.name}`);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setUploadError(message);
    } finally {
      setUploading(false);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const result = await api.searchDocuments(searchQuery);
      setSearchResult(
        Array.isArray(result.results)
          ? result.results.map((r: unknown) => 
              typeof r === "object" ? JSON.stringify(r, null, 2) : String(r)
            ).join("\n\n---\n\n")
          : JSON.stringify(result.results, null, 2)
      );
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Search failed";
      setSearchResult(`Error: ${message}`);
    }
  };

  return (
    <AppShell>
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <div className="border-b border-border px-6 py-4">
          <div className="flex items-center gap-2">
            <FileText className="size-5 text-primary" />
            <h1 className="text-lg font-semibold">Documents</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload and search through documents using AI-powered RAG
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-2">
            {/* Upload */}
            <motion.div
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
            >
              <Card className="p-5">
                <h3 className="mb-1 font-semibold">Upload Document</h3>
                <p className="mb-4 text-sm text-muted-foreground">
                  Supported: PDF, TXT, MD, PY, JSON, CSV, DOCX (max 10MB)
                </p>

                <label className="flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed border-border bg-secondary/30 p-8 transition-all hover:border-primary/50">
                  <Upload className="size-8 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">
                    Drop a file here or click to browse
                  </span>
                  <input
                    type="file"
                    className="hidden"
                    accept=".pdf,.txt,.md,.py,.json,.csv,.docx"
                    onChange={handleUpload}
                    disabled={uploading}
                  />
                </label>

                <AnimatePresence>
                  {uploading && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"
                    >
                      <Loader2 className="size-4 animate-spin" />
                      Processing file...
                    </motion.div>
                  )}
                  {uploadSuccess && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-3 flex items-center gap-2 text-sm text-emerald-400"
                    >
                      <CheckCircle2 className="size-4" />
                      {uploadSuccess}
                    </motion.div>
                  )}
                  {uploadError && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-3 flex items-center gap-2 text-sm text-red-400"
                    >
                      <AlertCircle className="size-4" />
                      {uploadError}
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>

              {/* Loaded Documents */}
              <Card className="mt-4 p-5">
                <h3 className="mb-3 font-semibold">Loaded Documents</h3>
                {documents.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No documents loaded yet
                  </p>
                ) : (
                  <div className="space-y-2">
                    {documents.map((doc) => {
                      const ext = doc.split(".").pop() || "default";
                      const Icon = fileIcons[ext] || fileIcons.default;
                      return (
                        <div
                          key={doc}
                          className="flex items-center gap-3 rounded-lg border border-border bg-secondary/30 p-3"
                        >
                          <Icon className="size-5 text-primary" />
                          <span className="flex-1 text-sm">{doc}</span>
                          <Badge variant="outline" className="text-[10px]">
                            {ext.toUpperCase()}
                          </Badge>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            </motion.div>

            {/* Search */}
            <motion.div
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
            >
              <Card className="p-5">
                <h3 className="mb-1 font-semibold">Search Documents</h3>
                <p className="mb-4 text-sm text-muted-foreground">
                  Ask a question and the AI will find relevant information
                </p>

                <div className="flex gap-2">
                  <Input
                    placeholder="What are you looking for?"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                    className="bg-secondary"
                  />
                  <Button onClick={handleSearch} disabled={!searchQuery.trim()}>
                    <Search className="size-4" />
                  </Button>
                </div>

                <AnimatePresence>
                  {searchResult && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="mt-4 rounded-lg border border-border bg-secondary/30 p-4"
                    >
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <pre className="whitespace-pre-wrap text-sm text-muted-foreground">
                          {searchResult}
                        </pre>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>

              {/* Tips */}
              <Card className="mt-4 border-dashed p-5">
                <h3 className="mb-2 font-semibold">Tips</h3>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-primary">•</span>
                    Upload CSV files for data analysis
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-primary">•</span>
                    PDF documents are automatically parsed
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-primary">•</span>
                    Use the chat to ask questions about your documents
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-primary">•</span>
                    The AI uses RAG with re-ranking for accurate answers
                  </li>
                </ul>
              </Card>
            </motion.div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

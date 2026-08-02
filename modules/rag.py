"""
RAG Module - Reads documents and answers questions about them
RAG = Retrieval Augmented Generation
This lets your agent READ files and ANSWER questions about them!
"""

import os
import re
from typing import List, Dict
from pathlib import Path


# Common English stopwords - these carry no retrieval signal and otherwise
# let irrelevant chunks win on generic words like "what" or "the".
STOPWORDS = frozenset(
    """
    a an and are as at be by do does for from has have how i in is it of on
    that the this to what when where which who why with you your its can will
    not or but if about into over under then so too very just was were been
    we they them their there here than also more most our out up down any all
    some such only own same get got make made use used ask tell
    """.split()
)


class DocumentReader:
    """
    Reads different file types and extracts text.
    The agent can read PDFs, text files, markdown, etc.
    """
    
    @staticmethod
    def read_text(file_path: str) -> str:
        """Read a plain text file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"
    
    @staticmethod
    def read_csv(file_path: str) -> str:
        """Read a CSV file smartly — extract headers, row count, and sample rows"""
        try:
            import csv
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers:
                    return "CSV file is empty."
                
                # Read up to 10 sample rows
                rows = []
                total = 0
                for row in reader:
                    total += 1
                    if len(rows) < 10:
                        rows.append(row)
                
                # Build a readable summary
                result = f"CSV File: {Path(file_path).name}\n"
                result += f"Columns ({len(headers)}): {', '.join(headers)}\n"
                result += f"Total rows: {total}\n\n"
                result += "Sample data (first 10 rows):\n\n"
                
                # Format as a readable table
                result += " | ".join(headers) + "\n"
                result += "-" * 80 + "\n"
                for row in rows:
                    # Pad row to match header count
                    padded = row + [""] * (len(headers) - len(row))
                    result += " | ".join(padded[:len(headers)]) + "\n"
                
                return result
        except Exception as e:
            # Fallback to plain text reading
            return DocumentReader.read_text(file_path)
    
    @staticmethod
    def read_markdown(file_path: str) -> str:
        """Read a markdown file"""
        return DocumentReader.read_text(file_path)
    
    @staticmethod
    def read_pdf(file_path: str) -> str:
        """Read a PDF file"""
        try:
            import pypdf  # maintained successor of PyPDF2 (CVE fixes)
            with open(file_path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
                return text
        except ImportError:
            return "PDF reading not available. Install pypdf: pip install pypdf"
        except Exception as e:
            return f"Error reading PDF: {str(e)}"
    
    @staticmethod
    def read_docx(file_path: str) -> str:
        """Read a Word document"""
        try:
            import docx
            doc = docx.Document(file_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except ImportError:
            return "DOCX reading not available. Install python-docx: pip install python-docx"
        except Exception as e:
            return f"Error reading DOCX: {str(e)}"
    
    @staticmethod
    def read_any(file_path: str) -> str:
        """Read any supported file type"""
        ext = Path(file_path).suffix.lower()
        
        readers = {
            '.txt': DocumentReader.read_text,
            '.md': DocumentReader.read_markdown,
            '.py': DocumentReader.read_text,
            '.json': DocumentReader.read_text,
            '.csv': DocumentReader.read_csv,
            '.pdf': DocumentReader.read_pdf,
            '.docx': DocumentReader.read_docx,
        }
        
        reader = readers.get(ext)
        if reader:
            return reader(file_path)
        return f"Unsupported file type: {ext}"


class SimpleRAG:
    """
    Simple RAG (Retrieval Augmented Generation)
    Stores document chunks and finds relevant ones for questions.
    """
    
    def __init__(self):
        self.documents: List[Dict] = []
        self.chunk_size = 500  # Characters per chunk
    
    def add_document(self, file_path: str) -> str:
        """Add a document to the knowledge base"""
        text = DocumentReader.read_any(file_path)
        
        if text.startswith("Error"):
            return text
        
        # Split into chunks
        chunks = self._split_text(text)
        
        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": file_path,
                "chunk_index": i,
                "content": chunk,
                "length": len(chunk)
            })
        
        return f"Added {len(chunks)} chunks from {file_path}"
    
    def add_text(self, text: str, source: str = "direct_input") -> str:
        """Add raw text to the knowledge base"""
        chunks = self._split_text(text)
        
        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": source,
                "chunk_index": i,
                "content": chunk,
                "length": len(chunk)
            })
        
        return f"Added {len(chunks)} chunks from direct input"
    
    def _split_text(self, text: str) -> List[str]:
        """Split text into chunks"""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i:i + self.chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks
    
    def _query_tokens(self, text: str) -> set:
        """Lowercase alphanumeric tokens, minus stopwords and single chars."""
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return {t for t in tokens if t not in STOPWORDS and len(t) > 1}

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """Search for relevant chunks (keyword matching).

        Scoring: each query token found in the chunk content scores 1; a
        token matching the document FILE NAME (exact token) scores 2, so a
        user asking about a specific file (e.g. "the Captainassistant file")
        retrieves that file even when its content never mentions the name.
        """
        query_words = self._query_tokens(query)

        results = []
        for doc in self.documents:
            content_lower = doc["content"].lower()
            # Tokens in the file path (exact match, e.g. "captainassistant").
            filename_tokens = self._query_tokens(doc["file"])
            content_score = sum(1 for word in query_words if word in content_lower)
            filename_score = sum(1 for word in query_words if word in filename_tokens)
            score = content_score + 2 * filename_score
            if score > 0:
                results.append({
                    **doc,
                    "relevance_score": score
                })

        # Deterministic: best score first, then insertion order.
        results.sort(key=lambda x: (-x["relevance_score"], x["chunk_index"]))
        return results[:top_k]
    
    def get_context(self, query: str, top_k: int = 3) -> str:
        """Get relevant context as a string for the AI to read.
        If no keyword matches but documents exist, return all documents anyway."""
        results = self.search(query, top_k)
        
        # If no keyword matches, but we have documents, return ALL of them
        if not results and self.documents:
            context = "Uploaded documents (full content):\n\n"
            for doc in self.documents:
                context += f"--- Source: {doc['file']} (chunk {doc['chunk_index']}) ---\n"
                context += f"{doc['content']}\n\n"
            # Truncate if too long (stay under ~8000 chars for LLM context)
            if len(context) > 8000:
                context = context[:8000] + "\n\n[...truncated, file is too large to include fully...]"
            return context
        
        if not results:
            return "No documents have been uploaded."
        
        context = "Relevant document excerpts:\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result['file']} (chunk {result['chunk_index']}) ---\n"
            context += f"{result['content']}\n\n"
        
        return context
    
    def get_context_for_document(self, query: str, filename: str, top_k: int = 4) -> str:
        """Retrieve context from a SINGLE uploaded document (used for
        multi-document comparison).

        Filename resolution is lenient (exact path, basename, or basename
        without extension). Returns labeled excerpts the LLM can cite, or ''
        when the document is not in the store.

        Scoring is frequency-based (not just presence): a chunk that mentions
        the topic several times beats chunks that mention it once, so the
        section that REALLY discusses the topic wins over incidental mentions.
        Chunks adjacent to a match are also included, because a topic usually
        spans a run of chunks and the surrounding context improves citations.
        """
        target = None
        for path in self.list_documents():
            base = os.path.basename(path)
            if filename == path or filename == base or filename == os.path.splitext(base)[0]:
                target = path
                break
        if target is None:
            return ""
        docs = [d for d in self.documents if d["file"] == target]
        if not docs:
            return ""

        query_words = self._query_tokens(query)
        if not query_words:
            picked = docs[:top_k]
        else:
            scored = []
            for doc in docs:
                content_lower = doc["content"].lower()
                freq = 0
                matched = 0
                for w in query_words:
                    c = content_lower.count(w)
                    if c:
                        freq += c
                        matched += 1
                # Frequency dominates (topic density); matched count breaks
                # ties between chunks covering several query aspects.
                scored.append((freq + matched, doc))
            # Deterministic: best score first, then insertion (chunk) order.
            scored.sort(key=lambda x: (-x[0], x[1]["chunk_index"]))
            best = [d for s, d in scored if s > 0][:top_k]
            if not best:
                picked = docs[:top_k]
            else:
                picked = list(best)
                picked_idx = {d["chunk_index"] for d in picked}
                # Context window: pull in the neighbors of matched chunks so
                # the section around the topic is available for citing.
                for _s, d in scored:
                    if len(picked) >= top_k + 2:
                        break
                    if d["chunk_index"] in picked_idx:
                        continue
                    if (d["chunk_index"] - 1 in picked_idx
                            or d["chunk_index"] + 1 in picked_idx):
                        picked.append(d)
                        picked_idx.add(d["chunk_index"])
                # Deterministic output: document order, not score order.
                picked.sort(key=lambda x: x["chunk_index"])

        lines = []
        for d in picked:
            lines.append(
                f"--- Source: {d['file']} (chunk {d['chunk_index']}) ---\n{d['content']}"
            )
        return "\n\n".join(lines)

    def list_documents(self) -> List[str]:
        """List all loaded documents"""
        files = set(doc["file"] for doc in self.documents)
        return list(files)
    
    def get_stats(self) -> Dict:
        """Get RAG statistics"""
        files = self.list_documents()
        return {
            "total_chunks": len(self.documents),
            "total_documents": len(files),
            "documents": files
        }
    
    def clear(self):
        """Clear all documents"""
        self.documents = []

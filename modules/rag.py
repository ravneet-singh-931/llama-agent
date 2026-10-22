"""
RAG Module - Reads documents and answers questions about them
RAG = Retrieval Augmented Generation
This lets your agent READ files and ANSWER questions about them!
"""

import os
import re
from typing import List, Dict, Optional

from modules.doc_scope import format_chunks, rank_document_chunks, resolve_filename
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

    Owner-aware: every chunk carries an ``owner``; all read methods accept
    ``owner=None`` (no filter) or an owner id (only that owner's chunks).
    """

    def __init__(self):
        self.documents: List[Dict] = []
        self.chunk_size = 500  # Characters per chunk
        # (owner, normalized query) -> answer
        self._answer_cache: Dict[tuple, str] = {}

    # ---------------- helpers ----------------
    def _docs(self, owner: Optional[str] = None) -> List[Dict]:
        if owner is None:
            return self.documents
        return [d for d in self.documents if d.get("owner") == owner]

    @staticmethod
    def _cache_key(query: str, owner: Optional[str]) -> tuple:
        return (owner or "", " ".join((query or "").lower().split()))

    # ---------------- indexing ----------------
    def add_document(self, file_path: str, owner: Optional[str] = None) -> str:
        """Add a document to the knowledge base (re-adding replaces it)."""
        text = DocumentReader.read_any(file_path)

        if text.startswith("Error"):
            return text

        file_name = os.path.basename(file_path)
        # Replace any previous version of the same file for this owner
        self.documents = [
            d for d in self.documents
            if not (d["file"] == file_name and d.get("owner") == owner)
        ]

        chunks = self._split_text(text)

        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": file_name,
                "source": file_path,
                "owner": owner,
                "chunk_index": i,
                "content": chunk,
                "length": len(chunk)
            })

        self.invalidate_cache(owner)
        return f"Added {len(chunks)} chunks from {file_name}"

    def add_text(self, text: str, source: str = "direct_input",
                 owner: Optional[str] = None) -> str:
        """Add raw text to the knowledge base"""
        chunks = self._split_text(text)

        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": source,
                "owner": owner,
                "chunk_index": i,
                "content": chunk,
                "length": len(chunk)
            })

        self.invalidate_cache(owner)
        return f"Added {len(chunks)} chunks from direct input"

    def delete_document(self, filename: str, owner: Optional[str] = None) -> int:
        target = resolve_filename(filename, self.list_documents(owner))
        if target is None:
            return 0
        before = len(self.documents)
        self.documents = [
            d for d in self.documents
            if not (d["file"] == target and (owner is None or d.get("owner") == owner))
        ]
        self.invalidate_cache(owner)
        return before - len(self.documents)

    def has_document(self, filename: str, owner: Optional[str] = None) -> bool:
        return resolve_filename(filename, self.list_documents(owner)) is not None

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

    # ---------------- retrieval ----------------
    def search(self, query: str, top_k: int = 3, owner: Optional[str] = None) -> List[Dict]:
        """Search for relevant chunks (keyword matching).

        Scoring: each query token found in the chunk content scores 1; a
        token matching the document FILE NAME (exact token) scores 2, so a
        user asking about a specific file (e.g. "the Captainassistant file")
        retrieves that file even when its content never mentions the name.
        """
        query_words = self._query_tokens(query)

        results = []
        for doc in self._docs(owner):
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

    def get_context(self, query: str, top_k: int = 3, owner: Optional[str] = None) -> str:
        """Get relevant context as a string for the AI to read.
        If no keyword matches but documents exist, return all documents anyway."""
        results = self.search(query, top_k, owner=owner)
        docs = self._docs(owner)

        # If no keyword matches, but we have documents, return ALL of them
        if not results and docs:
            context = "Uploaded documents (full content):\n\n"
            for doc in docs:
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

    def get_document_chunks(self, filename: str, owner: Optional[str] = None) -> List[Dict]:
        """All chunks of one document (lenient filename match), in order."""
        target = resolve_filename(filename, self.list_documents(owner))
        if target is None:
            return []
        chunks = [d for d in self._docs(owner) if d["file"] == target]
        return sorted(chunks, key=lambda d: d["chunk_index"])

    def get_context_for_document(self, query: str, filename: str, top_k: int = 4,
                                 owner: Optional[str] = None) -> str:
        """Retrieve context from a SINGLE uploaded document (used for
        multi-document comparison).

        Filename resolution is lenient (exact path, basename, or basename
        without extension). Returns labeled excerpts the LLM can cite, or ''
        when the document is not in the store.

        Scoring is frequency-based (not just presence): a chunk that mentions
        the topic several times beats chunks that mention it once, and chunks
        adjacent to a match are also included.
        """
        docs = self.get_document_chunks(filename, owner=owner)
        if not docs:
            return ""
        return format_chunks(rank_document_chunks(query, docs, top_k))

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        """List loaded documents (optionally only one owner's)."""
        seen: Dict[str, None] = {}
        for doc in self._docs(owner):
            seen.setdefault(doc["file"], None)
        return list(seen)

    # ---------------- answer cache ----------------
    def get_cached_answer(self, query: str, owner: Optional[str] = None) -> Optional[str]:
        return self._answer_cache.get(self._cache_key(query, owner))

    def cache_answer(self, query: str, answer: str, owner: Optional[str] = None) -> None:
        if len(self._answer_cache) > 500:
            self._answer_cache.clear()
        self._answer_cache[self._cache_key(query, owner)] = answer

    def invalidate_cache(self, owner: Optional[str] = None) -> None:
        if owner is None:
            self._answer_cache.clear()
            return
        for key in [k for k in self._answer_cache if k[0] == owner]:
            del self._answer_cache[key]

    def get_stats(self, owner: Optional[str] = None) -> Dict:
        """Get RAG statistics"""
        files = self.list_documents(owner)
        return {
            "total_chunks": len(self._docs(owner)),
            "total_documents": len(files),
            "documents": files
        }

    def clear(self):
        """Clear all documents"""
        self.documents = []
        self._answer_cache.clear()

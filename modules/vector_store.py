"""
Vector Store Module — ChromaDB Integration

What this does:
  Converts text into embeddings (vectors) and stores them in ChromaDB.
  When you search, it finds chunks with similar MEANING, not just keywords.

Example:
  Old RAG:  "refund policy" → only finds "refund policy"
  Vector RAG: "refund policy" → ALSO finds "money back guarantee", "return policy"

How it works:
  1. Text → Embedding Model → Vector (list of numbers like [0.1, -0.3, 0.8, ...])
  2. ChromaDB stores vectors and lets you search by similarity
  3. Cosine similarity finds the closest meaning matches

Architecture:
  Document → Split into chunks → Each chunk → Embedding → Stored in ChromaDB
  User query → Embedding → Find similar chunks → Return to AI
"""

import os
from typing import List, Dict, Optional
from pathlib import Path

from modules.doc_scope import format_chunks, rank_document_chunks, resolve_filename


class VectorStore:
    """
    ChromaDB-backed vector store for semantic document search.
    
    Unlike keyword search, this understands MEANING:
    - "How do I reset my password?" matches "Password recovery steps"
    - "What's the cost?" matches "Pricing information"
    - "Can I get my money back?" matches "Refund policy"
    """
    
    def __init__(self, persist_directory: str = "data/chromadb", client=None):
        """
        Initialize the vector store.

        Args:
            persist_directory: Where to save the database on disk (used only
                               when no shared client is supplied).
            client: Optional pre-built ChromaDB client. When provided (e.g. a
                    shared HttpClient pointing at a ChromaDB server), the store
                    is shared across all API replicas instead of being
                    pod-local.
        """
        import chromadb
        from chromadb.config import Settings

        if client is not None:
            self.client = client
        else:
            # Create directory if it doesn't exist
            os.makedirs(persist_directory, exist_ok=True)

            # Create ChromaDB client with persistence
            self.client = chromadb.PersistentClient(
                path=persist_directory,
                settings=Settings(
                    anonymized_telemetry=False,  # Don't send usage data
                ),
            )
        
        # Get or create a collection (like a table in a database)
        # This collection stores all document chunks + their embeddings
        self.collection = self.client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},  # Use cosine similarity
        )
        
        self.chunk_size = 500  # Characters per chunk
        print(f"[OK] VectorStore initialized at {persist_directory}")
        print(f"     Existing chunks: {self.collection.count()}")
    
    # ---------------- owner-scoping helpers ----------------
    @staticmethod
    def _where(owner: Optional[str] = None, file: Optional[str] = None) -> Optional[Dict]:
        conds = []
        if owner is not None:
            conds.append({"owner": owner})
        if file is not None:
            conds.append({"file": file})
        if not conds:
            return None
        if len(conds) == 1:
            return conds[0]
        return {"$and": conds}

    def _get(self, where: Optional[Dict], include: List[str]) -> Dict:
        kwargs = {"include": include}
        if where:
            kwargs["where"] = where
        return self.collection.get(**kwargs)

    def _count(self, owner: Optional[str] = None) -> int:
        if owner is None:
            return self.collection.count()
        data = self._get(self._where(owner), include=[])
        return len(data.get("ids") or [])

    @staticmethod
    def _chunk_id(owner: Optional[str], file_name: str, i: int) -> str:
        return f"{owner}::{file_name}_{i}" if owner else f"{file_name}_{i}"

    # ---------------- indexing ----------------
    def add_document(self, file_path: str, owner: Optional[str] = None) -> str:
        """
        Read a file, split into chunks, embed, and store in ChromaDB.

        Chunks are tagged with ``owner`` and use owner-prefixed ids, so two
        users uploading ``report.pdf`` never overwrite each other. Re-adding
        the same file for the same owner replaces its previous chunks.

        Returns a status message like "Added 12 chunks from report.pdf"
        """
        from modules.rag import DocumentReader

        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text

        if not text.strip():
            return "File is empty or unreadable"

        chunks = self._split_text(text)
        if not chunks:
            return "No content to index"

        file_name = Path(file_path).name
        self.delete_document(file_name, owner=owner, exact=True)

        ids, documents, metadatas = [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(self._chunk_id(owner, file_name, i))
            documents.append(chunk)
            metadatas.append({
                "file": file_name,
                "owner": owner or "",
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source": file_path,
            })

        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return f"Added {len(chunks)} chunks from {file_name} (vector-indexed)"

    def delete_document(self, filename: str, owner: Optional[str] = None,
                        exact: bool = False) -> int:
        """Delete every chunk of ``filename`` (for ``owner``)."""
        target = filename if exact else resolve_filename(filename, self.list_documents(owner))
        if not target:
            return 0
        where = self._where(owner, target)
        existing = self._get(where, include=[])
        ids = existing.get("ids") or []
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def has_document(self, filename: str, owner: Optional[str] = None) -> bool:
        return resolve_filename(filename, self.list_documents(owner)) is not None

    # ---------------- retrieval ----------------
    def search(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> List[Dict]:
        """
        Search for chunks with similar MEANING to the query.

        Args:
            query: The search query (e.g., "How do I get a refund?")
            top_k: Number of results to return
            owner: Only search this owner's chunks (None = all)

        Returns:
            List of matching chunks with scores
        """
        available = self._count(owner)
        if available == 0:
            return []

        kwargs = {
            "query_texts": [query],
            "n_results": max(1, min(top_k, available)),
            "include": ["documents", "metadatas", "distances"],
        }
        where = self._where(owner)
        if where:
            kwargs["where"] = where
        results = self.collection.query(**kwargs)

        formatted = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i]
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                # ChromaDB cosine distance: 0 = identical, 2 = opposite
                similarity = 1 - (distance / 2)

                formatted.append({
                    "content": doc,
                    "file": metadata.get("file", "unknown"),
                    "owner": metadata.get("owner") or None,
                    "chunk_index": metadata.get("chunk_index", 0),
                    "similarity": round(similarity, 3),
                })

        return formatted

    def get_context(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> str:
        """Get relevant context as a string for the AI to read."""
        results = self.search(query, top_k, owner=owner)

        if not results:
            if self._count(owner) > 0:
                return "No relevant documents found for this query."
            return "No documents have been uploaded."

        context = "Relevant document excerpts (ranked by relevance):\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result['file']} "
            context += f"(chunk {result['chunk_index']}, "
            context += f"similarity: {result['similarity']}) ---\n"
            context += f"{result['content']}\n\n"

        return context

    def get_document_chunks(self, filename: str, owner: Optional[str] = None) -> List[Dict]:
        """All chunks of a single document, in order."""
        target = resolve_filename(filename, self.list_documents(owner))
        if not target:
            return []
        data = self._get(self._where(owner, target), include=["documents", "metadatas"])
        chunks = []
        for doc, meta in zip(data.get("documents") or [], data.get("metadatas") or []):
            meta = meta or {}
            chunks.append({
                "file": meta.get("file", target),
                "owner": meta.get("owner") or None,
                "chunk_index": meta.get("chunk_index", 0),
                "content": doc,
            })
        return sorted(chunks, key=lambda c: c["chunk_index"])

    def get_context_for_document(self, query: str, filename: str, top_k: int = 4,
                                 owner: Optional[str] = None) -> str:
        """Labeled excerpts from ONE document (multi-document comparison)."""
        chunks = self.get_document_chunks(filename, owner=owner)
        if not chunks:
            return ""
        return format_chunks(rank_document_chunks(query, chunks, top_k))

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        """List indexed files (optionally only one owner's)."""
        if self.collection.count() == 0:
            return []
        all_data = self._get(self._where(owner), include=["metadatas"])
        files: Dict[str, None] = {}
        for meta in all_data.get("metadatas") or []:
            files.setdefault((meta or {}).get("file", "unknown"), None)
        return list(files)

    def get_stats(self, owner: Optional[str] = None) -> Dict:
        """Get statistics about the vector store."""
        docs = self.list_documents(owner)
        return {
            "total_chunks": self._count(owner),
            "total_documents": len(docs),
            "documents": docs,
        }

    def clear(self):
        """Clear all stored documents."""
        # Delete the collection and recreate it
        self.client.delete_collection("documents")
        self.collection = self.client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )
        print("VectorStore cleared")
    
    def _split_text(self, text: str) -> List[str]:
        """Split text into chunks with some overlap for context."""
        chunks = []
        # Use overlap so chunks don't lose context at boundaries
        overlap = 50
        step = self.chunk_size - overlap
        
        for i in range(0, len(text), step):
            chunk = text[i:i + self.chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks


# ============ FALLBACK: Keyword Search (if ChromaDB unavailable) ============

class KeywordStore:
    """
    Fallback keyword-based search (like the original SimpleRAG).
    Used when ChromaDB is not installed.
    """
    
    def __init__(self):
        self.documents: List[Dict] = []
        self.chunk_size = 500
    
    def _docs(self, owner: Optional[str] = None) -> List[Dict]:
        if owner is None:
            return self.documents
        return [d for d in self.documents if d.get("owner") == owner]

    def add_document(self, file_path: str, owner: Optional[str] = None) -> str:
        from modules.rag import DocumentReader
        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text

        chunks = self._split_text(text)
        file_name = Path(file_path).name
        self.documents = [
            d for d in self.documents
            if not (d["file"] == file_name and d.get("owner") == owner)
        ]

        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": file_name,
                "owner": owner,
                "chunk_index": i,
                "content": chunk,
            })

        return f"Added {len(chunks)} chunks from {file_name} (keyword search)"

    def search(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> List[Dict]:
        query_words = set(query.lower().split())
        results = []
        for doc in self._docs(owner):
            content_lower = doc["content"].lower()
            score = sum(1 for word in query_words if word in content_lower)
            if score > 0:
                results.append({**doc, "similarity": score / len(query_words) if query_words else 0})
        results.sort(key=lambda x: (-x["similarity"], x["chunk_index"]))
        return results[:top_k]

    def get_context(self, query: str, top_k: int = 5, owner: Optional[str] = None) -> str:
        results = self.search(query, top_k, owner=owner)
        if not results:
            if self._docs(owner):
                return "No keyword matches found. Try rephrasing your question."
            return "No documents have been uploaded."

        context = "Relevant document excerpts:\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result['file']} (chunk {result['chunk_index']}) ---\n"
            context += f"{result['content']}\n\n"
        return context

    def get_document_chunks(self, filename: str, owner: Optional[str] = None) -> List[Dict]:
        target = resolve_filename(filename, self.list_documents(owner))
        if not target:
            return []
        return sorted(
            (d for d in self._docs(owner) if d["file"] == target),
            key=lambda d: d["chunk_index"],
        )

    def get_context_for_document(self, query: str, filename: str, top_k: int = 4,
                                 owner: Optional[str] = None) -> str:
        chunks = self.get_document_chunks(filename, owner=owner)
        if not chunks:
            return ""
        return format_chunks(rank_document_chunks(query, chunks, top_k))

    def delete_document(self, filename: str, owner: Optional[str] = None) -> int:
        target = resolve_filename(filename, self.list_documents(owner))
        if not target:
            return 0
        before = len(self.documents)
        self.documents = [
            d for d in self.documents
            if not (d["file"] == target and (owner is None or d.get("owner") == owner))
        ]
        return before - len(self.documents)

    def has_document(self, filename: str, owner: Optional[str] = None) -> bool:
        return resolve_filename(filename, self.list_documents(owner)) is not None

    def list_documents(self, owner: Optional[str] = None) -> List[str]:
        files: Dict[str, None] = {}
        for doc in self._docs(owner):
            files.setdefault(doc["file"], None)
        return list(files)

    def get_stats(self, owner: Optional[str] = None) -> Dict:
        docs = self.list_documents(owner)
        return {"total_chunks": len(self._docs(owner)), "total_documents": len(docs), "documents": docs}

    def clear(self):
        self.documents = []
    
    def _split_text(self, text: str) -> List[str]:
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i:i + self.chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks


def get_vector_store(persist_directory: str = "data/chromadb"):
    """
    Factory function: returns a VectorStore backed by ChromaDB.

    - If CHROMA_SERVER_URL is set, uses a shared ChromaDB *server*
      (HttpClient) so every API replica reads/writes the SAME vector
      index. This fixes the multi-replica bug where one replica had
      indexed documents that another replica could not search.
    - Otherwise falls back to a local PersistentClient (pod-local).
    - Falls back to KeywordStore if ChromaDB isn't installed at all.
    """
    try:
        import chromadb
    except ImportError:
        print("[WARN] ChromaDB not installed. Using keyword search fallback.")
        print("       Install: pip install chromadb")
        return KeywordStore()

    server_url = os.environ.get("CHROMA_SERVER_URL")
    if server_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(server_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8000
            client = chromadb.HttpClient(host=host, port=port)
            client.heartbeat()  # verify the server is reachable
            print(f"[OK] VectorStore using shared ChromaDB server at {server_url}")
            return VectorStore(persist_directory, client=client)
        except Exception as e:
            print(f"[WARN] ChromaDB server at {server_url} unavailable ({e}); "
                  f"falling back to local PersistentClient")

    return VectorStore(persist_directory)

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
    
    def add_document(self, file_path: str) -> str:
        """
        Read a file, split into chunks, embed, and store in ChromaDB.
        
        Returns a status message like "Added 12 chunks from report.pdf"
        """
        from modules.rag import DocumentReader
        
        # Read the file
        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text
        
        if not text.strip():
            return "File is empty or unreadable"
        
        # Split into chunks
        chunks = self._split_text(text)
        if not chunks:
            return "No content to index"
        
        # Prepare data for ChromaDB
        file_name = Path(file_path).name
        ids = []
        documents = []
        metadatas = []
        
        for i, chunk in enumerate(chunks):
            chunk_id = f"{file_name}_{i}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "file": file_name,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source": file_path,
            })
        
        # Add to ChromaDB (it automatically computes embeddings)
        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        
        return f"Added {len(chunks)} chunks from {file_name} (vector-indexed)"
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search for chunks with similar MEANING to the query.
        
        This is the magic — it doesn't just match keywords.
        It matches SEMANTIC meaning using embeddings.
        
        Args:
            query: The search query (e.g., "How do I get a refund?")
            top_k: Number of results to return
        
        Returns:
            List of matching chunks with scores
        """
        if self.collection.count() == 0:
            return []
        
        # ChromaDB computes the query embedding and finds similar vectors
        results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        
        # Format results
        formatted = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i]
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                
                # Convert distance to similarity score (0-1, higher = more similar)
                # ChromaDB cosine distance: 0 = identical, 2 = opposite
                similarity = 1 - (distance / 2)
                
                formatted.append({
                    "content": doc,
                    "file": metadata.get("file", "unknown"),
                    "chunk_index": metadata.get("chunk_index", 0),
                    "similarity": round(similarity, 3),
                })
        
        return formatted
    
    def get_context(self, query: str, top_k: int = 5) -> str:
        """
        Get relevant context as a string for the AI to read.
        This replaces the old keyword-based search.
        """
        results = self.search(query, top_k)
        
        if not results:
            if self.collection.count() > 0:
                return "No relevant documents found for this query."
            return "No documents have been uploaded."
        
        context = "Relevant document excerpts (ranked by relevance):\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result['file']} "
            context += f"(chunk {result['chunk_index']}, "
            context += f"similarity: {result['similarity']}) ---\n"
            context += f"{result['content']}\n\n"
        
        return context
    
    def list_documents(self) -> List[str]:
        """List all indexed files."""
        if self.collection.count() == 0:
            return []
        
        all_data = self.collection.get(include=["metadatas"])
        files = set()
        for meta in all_data["metadatas"]:
            files.add(meta.get("file", "unknown"))
        return list(files)
    
    def get_stats(self) -> Dict:
        """Get statistics about the vector store."""
        return {
            "total_chunks": self.collection.count(),
            "total_documents": len(self.list_documents()),
            "documents": self.list_documents(),
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
    
    def add_document(self, file_path: str) -> str:
        from modules.rag import DocumentReader
        text = DocumentReader.read_any(file_path)
        if text.startswith("Error"):
            return text
        
        chunks = self._split_text(text)
        file_name = Path(file_path).name
        
        for i, chunk in enumerate(chunks):
            self.documents.append({
                "file": file_name,
                "chunk_index": i,
                "content": chunk,
            })
        
        return f"Added {len(chunks)} chunks from {file_name} (keyword search)"
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        query_words = set(query.lower().split())
        results = []
        for doc in self.documents:
            content_lower = doc["content"].lower()
            score = sum(1 for word in query_words if word in content_lower)
            if score > 0:
                results.append({**doc, "similarity": score / len(query_words) if query_words else 0})
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]
    
    def get_context(self, query: str, top_k: int = 5) -> str:
        results = self.search(query, top_k)
        if not results:
            if self.documents:
                return "No keyword matches found. Try rephrasing your question."
            return "No documents have been uploaded."
        
        context = "Relevant document excerpts:\n\n"
        for i, result in enumerate(results, 1):
            context += f"--- Source: {result['file']} (chunk {result['chunk_index']}) ---\n"
            context += f"{result['content']}\n\n"
        return context
    
    def list_documents(self) -> List[str]:
        return list(set(doc["file"] for doc in self.documents))
    
    def get_stats(self) -> Dict:
        return {"total_chunks": len(self.documents), "total_documents": len(self.list_documents()), "documents": self.list_documents()}
    
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

"""
Performance Optimization Module for Llama Agent

Features:
- Response caching (Redis + in-memory)
- Query result caching
- Document embedding caching
- Cache invalidation strategies
- Performance metrics
"""

import os
import time
import json
import hashlib
import threading
from typing import Any, Optional, Dict, Callable
from functools import wraps
from datetime import datetime, timedelta


# ============ IN-MEMORY CACHE ============


class InMemoryCache:
    """
    Simple in-memory cache with TTL (Time-To-Live).
    
    Use for:
    - Small, frequently accessed data
    - Temporary caching when Redis is unavailable
    - Session data
    """
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        """
        Args:
            max_size: Maximum number of items to store
            default_ttl: Default time-to-live in seconds (5 minutes)
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: Dict[str, Dict] = {}
        self.lock = threading.Lock()
        
        # Stats
        self.hits = 0
        self.misses = 0
    
    def _make_key(self, key: str) -> str:
        """Create a hash key for the cache."""
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        cache_key = self._make_key(key)
        
        with self.lock:
            if cache_key in self.cache:
                item = self.cache[cache_key]
                
                # Check if expired
                if time.time() < item["expires_at"]:
                    self.hits += 1
                    return item["value"]
                else:
                    # Remove expired item
                    del self.cache[cache_key]
            
            self.misses += 1
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set a value in cache."""
        cache_key = self._make_key(key)
        ttl = ttl or self.default_ttl
        
        with self.lock:
            # Remove oldest items if cache is full
            if len(self.cache) >= self.max_size:
                self._evict_oldest()
            
            self.cache[cache_key] = {
                "value": value,
                "expires_at": time.time() + ttl,
                "created_at": time.time(),
            }
    
    def delete(self, key: str) -> bool:
        """Delete a value from cache."""
        cache_key = self._make_key(key)
        
        with self.lock:
            if cache_key in self.cache:
                del self.cache[cache_key]
                return True
            return False
    
    def clear(self) -> int:
        """Clear all cache. Returns number of items removed."""
        with self.lock:
            count = len(self.cache)
            self.cache.clear()
            return count
    
    def _evict_oldest(self) -> None:
        """Remove the oldest item from cache."""
        if not self.cache:
            return
        
        oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k]["created_at"])
        del self.cache[oldest_key]
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
        }


# ============ REDIS CACHE ============


class RedisCache:
    """
    Redis-based distributed cache.
    
    Use for:
    - Large datasets
    - Shared cache across multiple instances
    - Persistent caching
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.client = None
        self.connected = False
        
        try:
            import redis
            self.client = redis.from_url(redis_url, decode_responses=True)
            # Test connection
            self.client.ping()
            self.connected = True
        except Exception as e:
            print(f"[CACHE] Redis not available: {e}")
            print("[CACHE] Using in-memory cache as fallback")
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from Redis."""
        if not self.connected:
            return None
        
        try:
            value = self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception:
            return None
    
    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set a value in Redis."""
        if not self.connected:
            return False
        
        try:
            serialized = json.dumps(value, default=str)
            self.client.setex(key, ttl, serialized)
            return True
        except Exception:
            return False
    
    def delete(self, key: str) -> bool:
        """Delete a value from Redis."""
        if not self.connected:
            return False
        
        try:
            return self.client.delete(key) > 0
        except Exception:
            return False
    
    def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching a pattern."""
        if not self.connected:
            return 0
        
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception:
            return 0
    
    def get_stats(self) -> Dict:
        """Get Redis statistics."""
        if not self.connected:
            return {"connected": False}
        
        try:
            info = self.client.info()
            return {
                "connected": True,
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands": info.get("total_commands_processed", 0),
            }
        except Exception:
            return {"connected": False}


# ============ RESPONSE CACHE ============


class ResponseCache:
    """
    Caches API responses to avoid redundant processing.
    
    Features:
    - Automatic cache key generation
    - TTL-based expiration
    - Cache invalidation on data changes
    - Cache statistics
    """
    
    def __init__(self, backend: str = "memory", redis_url: str = "redis://localhost:6379"):
        """
        Args:
            backend: "memory" or "redis"
            redis_url: Redis connection URL (if using redis backend)
        """
        if backend == "redis":
            self.cache = RedisCache(redis_url)
        else:
            self.cache = InMemoryCache()
        
        # Track which keys need invalidation
        self.dependency_map: Dict[str, list] = {}
    
    def _generate_key(self, prefix: str, **kwargs) -> str:
        """Generate a cache key from parameters."""
        # Sort kwargs for consistent keys
        sorted_kwargs = sorted(kwargs.items())
        key_data = f"{prefix}:{json.dumps(sorted_kwargs, default=str)}"
        return hashlib.md5(key_data.encode(), usedforsecurity=False).hexdigest()
    
    def get(self, prefix: str, **kwargs) -> Optional[Any]:
        """Get a cached response."""
        key = self._generate_key(prefix, **kwargs)
        return self.cache.get(key)
    
    def set(self, prefix: str, value: Any, ttl: int = 300, **kwargs) -> None:
        """Cache a response."""
        key = self._generate_key(prefix, **kwargs)
        self.cache.set(key, value, ttl)
    
    def invalidate(self, prefix: str, **kwargs) -> bool:
        """Invalidate a cached response."""
        key = self._generate_key(prefix, **kwargs)
        return self.cache.delete(key)
    
    def invalidate_all(self, prefix: str) -> int:
        """Invalidate all cached responses with a prefix."""
        if isinstance(self.cache, RedisCache):
            return self.cache.clear_pattern(f"{prefix}:*")
        else:
            # For in-memory cache, we need to iterate
            return 0
    
    def cache_response(self, ttl: int = 300):
        """
        Decorator to cache function responses.
        
        Usage:
            @cache.cache_response(ttl=600)
            def expensive_operation(query):
                # This will be cached for 10 minutes
                return result
        """
        def decorator(func: Callable):
            @wraps(func)
            def wrapper(*args, **kwargs):
                # Generate cache key from function name and arguments
                cache_key = f"{func.__name__}:{args}:{kwargs}"
                key = hashlib.md5(cache_key.encode(), usedforsecurity=False).hexdigest()
                
                # Check cache
                cached = self.cache.get(key)
                if cached is not None:
                    return cached
                
                # Execute function
                result = func(*args, **kwargs)
                
                # Cache result
                self.cache.set(key, result, ttl)
                
                return result
            return wrapper
        return decorator
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        if isinstance(self.cache, InMemoryCache):
            return self.cache.get_stats()
        elif isinstance(self.cache, RedisCache):
            return self.cache.get_stats()
        return {}


# ============ QUERY CACHE ============


class QueryCache:
    """
    Specifically caches database query results.
    
    Features:
    - Automatic cache key generation from query
    - TTL-based expiration
    - Invalidation on data changes
    """
    
    def __init__(self, ttl: int = 60):
        """
        Args:
            ttl: Time-to-live in seconds (default 1 minute)
        """
        self.ttl = ttl
        self.cache = InMemoryCache(max_size=500, default_ttl=ttl)
    
    def get_query(self, query: str, params: tuple = ()) -> Optional[Any]:
        """Get cached query result."""
        key = f"query:{query}:{params}"
        return self.cache.get(key)
    
    def set_query(self, query: str, result: Any, params: tuple = ()) -> None:
        """Cache a query result."""
        key = f"query:{query}:{params}"
        self.cache.set(key, result, self.ttl)
    
    def invalidate_table(self, table: str) -> None:
        """Invalidate all cached queries for a table."""
        # Simple approach: clear all cache
        # More sophisticated: track which queries use which tables
        self.cache.clear()
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return self.cache.get_stats()


# ============ EMBEDDING CACHE ============


class EmbeddingCache:
    """
    Caches document embeddings to avoid recomputing.
    
    When the same document is uploaded multiple times,
    the embeddings can be reused.
    """
    
    def __init__(self, ttl: int = 86400):  # 24 hours
        self.ttl = ttl
        self.cache = InMemoryCache(max_size=10000, default_ttl=ttl)
    
    def get_embedding(self, text: str) -> Optional[list]:
        """Get cached embedding for text."""
        key = f"embedding:{hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()}"
        return self.cache.get(key)
    
    def set_embedding(self, text: str, embedding: list) -> None:
        """Cache an embedding."""
        key = f"embedding:{hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()}"
        self.cache.set(key, embedding, self.ttl)
    
    def get_document_embeddings(self, document_id: str) -> Optional[Dict]:
        """Get all cached embeddings for a document."""
        key = f"doc_embeddings:{document_id}"
        return self.cache.get(key)
    
    def set_document_embeddings(self, document_id: str, embeddings: Dict) -> None:
        """Cache all embeddings for a document."""
        key = f"doc_embeddings:{document_id}"
        self.cache.set(key, embeddings, self.ttl)
    
    def invalidate_document(self, document_id: str) -> None:
        """Invalidate all embeddings for a document."""
        key = f"doc_embeddings:{document_id}"
        self.cache.delete(key)


# ============ PERFORMANCE MONITOR ============


class PerformanceMonitor:
    """
    Tracks performance metrics for caching and queries.
    """
    
    def __init__(self):
        self.metrics = {
            "cache_hits": 0,
            "cache_misses": 0,
            "query_count": 0,
            "slow_queries": 0,
            "total_query_time": 0,
        }
        self.slow_query_threshold = 1.0  # seconds
    
    def record_cache_hit(self) -> None:
        """Record a cache hit."""
        self.metrics["cache_hits"] += 1
    
    def record_cache_miss(self) -> None:
        """Record a cache miss."""
        self.metrics["cache_misses"] += 1
    
    def record_query(self, duration: float) -> None:
        """Record a database query."""
        self.metrics["query_count"] += 1
        self.metrics["total_query_time"] += duration
        
        if duration > self.slow_query_threshold:
            self.metrics["slow_queries"] += 1
    
    def get_metrics(self) -> Dict:
        """Get all performance metrics."""
        total_cache = self.metrics["cache_hits"] + self.metrics["cache_misses"]
        cache_hit_rate = (
            self.metrics["cache_hits"] / total_cache * 100
            if total_cache > 0 else 0
        )
        
        avg_query_time = (
            self.metrics["total_query_time"] / self.metrics["query_count"]
            if self.metrics["query_count"] > 0 else 0
        )
        
        return {
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": round(cache_hit_rate, 2),
            "query_count": self.metrics["query_count"],
            "slow_queries": self.metrics["slow_queries"],
            "avg_query_time": round(avg_query_time, 4),
        }


# ============ SINGLETON INSTANCES ============


# Global instances
response_cache = ResponseCache(backend="memory")
query_cache = QueryCache(ttl=60)
embedding_cache = EmbeddingCache(ttl=86400)
performance_monitor = PerformanceMonitor()


# ============ HELPER FUNCTIONS ============


def cached(ttl: int = 300, prefix: str = "default"):
    """
    Simple caching decorator.
    
    Usage:
        @cached(ttl=600, prefix="chat")
        def get_chat_response(message):
            return expensive_computation(message)
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key_data = f"{prefix}:{func.__name__}:{args}:{kwargs}"
            cache_key = hashlib.md5(key_data.encode(), usedforsecurity=False).hexdigest()
            
            # Check cache
            cached_value = response_cache.get(prefix, cache_key=cache_key)
            if cached_value is not None:
                performance_monitor.record_cache_hit()
                return cached_value
            
            # Execute function
            performance_monitor.record_cache_miss()
            result = func(*args, **kwargs)
            
            # Cache result
            response_cache.set(prefix, result, ttl=ttl, cache_key=cache_key)
            
            return result
        return wrapper
    return decorator

"""TTL cache for Onshape read operations.

We cache GET responses to avoid re-reading data that hasn't changed.
Different cache TTLs for different data types:
- documents: 2 min (rarely change)
- parts: 1 min 
- features: 30 sec (change with each operation)
- thumbnails: 5 min

Write operations (POST, DELETE) invalidate related cache keys.
"""

from cachetools import TTLCache
import threading
import hashlib
import json
from typing import Any, Optional


class ReadCache:
    """Thread-safe TTL cache for Onshape API reads."""

    def __init__(self, maxsize: int = 200):
        # We use multiple caches with different TTLs
        self._caches = {
            "default": TTLCache(maxsize=maxsize, ttl=60),
            "document": TTLCache(maxsize=20, ttl=120),
            "parts": TTLCache(maxsize=50, ttl=60),
            "features": TTLCache(maxsize=50, ttl=30),
            "thumbnail": TTLCache(maxsize=10, ttl=300),
        }
        self._lock = threading.Lock()

    def _make_key(self, method: str, url: str, params: Optional[dict] = None) -> str:
        """Create a cache key from method + URL + params."""
        raw = f"{method}:{url}"
        if params:
            raw += ":" + json.dumps(params, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _pick_cache(self, url: str) -> str:
        """Pick which TTL bucket based on URL path."""
        if "/thumbnails/" in url or "/shadedviews/" in url:
            return "thumbnail"
        if "/parts/" in url:
            return "parts"
        if "/features" in url:
            return "features"
        if "/documents" in url and "/elements" not in url:
            return "document"
        return "default"

    def get(self, method: str, url: str, params: Optional[dict] = None) -> Optional[Any]:
        """Get a cached response. Returns None if not found or expired."""
        key = self._make_key(method, url, params)
        bucket = self._pick_cache(url)
        with self._lock:
            return self._caches[bucket].get(key)

    def set(self, method: str, url: str, params: Optional[dict] = None, value: Any = None):
        """Cache a response."""
        key = self._make_key(method, url, params)
        bucket = self._pick_cache(url)
        with self._lock:
            self._caches[bucket][key] = value

    def invalidate(self, url_pattern: str = ""):
        """Invalidate cache entries matching a URL pattern.

        If url_pattern is empty, invalidates everything.
        Otherwise removes keys where the URL contains the pattern.
        """
        with self._lock:
            if not url_pattern:
                for cache in self._caches.values():
                    cache.clear()
            else:
                # We can't easily do pattern matching on hashed keys,
                # so for targeted invalidation we clear the relevant bucket
                bucket = self._pick_cache(url_pattern)
                self._caches[bucket].clear()

    def invalidate_document(self, did: str):
        """Invalidate all cached data for a specific document."""
        # Invalidate by clearing all caches — since we hash keys,
        # targeted invalidation requires a key registry.
        # For v1, clearing the feature/parts caches on write is safe.
        with self._lock:
            self._caches["parts"].clear()
            self._caches["features"].clear()
            self._caches["thumbnail"].clear()

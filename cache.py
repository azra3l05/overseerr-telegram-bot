# cache.py
"""
Simple in-memory caching layer for API responses.
Reduces redundant API calls and improves response times.
"""
import logging
import time
from typing import Any, Optional, Dict, Tuple
from functools import wraps
from threading import Lock

logger = logging.getLogger(__name__)


class SimpleCache:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str, default: Any = None) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key
            default: Default value if not found or expired

        Returns:
            Cached value or default
        """
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return default

            value, expires_at = self._cache[key]

            # Check if expired
            if time.time() > expires_at:
                del self._cache[key]
                self._misses += 1
                return default

            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int = 300):
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (default 5 minutes)
        """
        with self._lock:
            expires_at = time.time() + ttl
            self._cache[key] = (value, expires_at)

    def delete(self, key: str):
        """Delete key from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            logger.info("Cache cleared")

    def cleanup_expired(self):
        """Remove expired entries from cache."""
        with self._lock:
            now = time.time()
            expired_keys = [
                key for key, (_, expires_at) in self._cache.items()
                if now > expires_at
            ]
            for key in expired_keys:
                del self._cache[key]

            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

            return {
                "entries": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "total_requests": total_requests,
                "hit_rate": f"{hit_rate:.1f}%",
            }


# Global cache instance
_cache = SimpleCache()


def get_cache() -> SimpleCache:
    """Get the global cache instance."""
    return _cache


def cached(ttl: int = 300, key_prefix: str = ""):
    """
    Decorator to cache function results.

    Args:
        ttl: Time to live in seconds (default 5 minutes)
        key_prefix: Prefix for cache key (default: function name)

    Example:
        @cached(ttl=600, key_prefix="movie")
        def get_movie_details(movie_id: int):
            # ... expensive API call
            return movie_data
    """

    def decorator(func):
        # Use function name as prefix if not provided
        prefix = key_prefix or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key from function arguments
            # Convert args and kwargs to a stable string representation
            args_str = "_".join(str(arg) for arg in args)
            kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = f"{prefix}:{args_str}:{kwargs_str}".replace(" ", "_")

            # Try to get from cache
            cached_value = get_cache().get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache HIT: {cache_key}")
                return cached_value

            # Cache miss - execute function
            logger.debug(f"Cache MISS: {cache_key}")
            result = func(*args, **kwargs)

            # Store in cache if result is not None
            if result is not None:
                get_cache().set(cache_key, result, ttl)

            return result

        return wrapper

    return decorator


def cache_result(key: str, func, ttl: int = 300, *args, **kwargs):
    """
    Cache a function result with a specific key.

    Useful for manual caching without decorator.

    Args:
        key: Cache key
        func: Function to execute if cache miss
        ttl: Time to live in seconds
        *args, **kwargs: Arguments to pass to func

    Returns:
        Cached or fresh function result

    Example:
        details = cache_result(
            f"movie:{movie_id}",
            get_movie_from_api,
            ttl=600,
            movie_id=movie_id
        )
    """
    cached_value = get_cache().get(key)
    if cached_value is not None:
        logger.debug(f"Cache HIT: {key}")
        return cached_value

    logger.debug(f"Cache MISS: {key}")
    result = func(*args, **kwargs)

    if result is not None:
        get_cache().set(key, result, ttl)

    return result


# Cache configuration presets
CACHE_TTL = {
    "search": 300,  # 5 minutes - search results change frequently
    "details": 3600,  # 1 hour - media details are stable
    "trending": 1800,  # 30 minutes - trending lists update regularly
    "recommendations": 3600,  # 1 hour - recommendations are stable
    "availability": 60,  # 1 minute - availability changes quickly
    "poster": 86400,  # 24 hours - posters rarely change
}


def get_ttl(cache_type: str) -> int:
    """
    Get TTL for a cache type.

    Args:
        cache_type: Type of cache (search, details, trending, etc.)

    Returns:
        TTL in seconds
    """
    return CACHE_TTL.get(cache_type, 300)  # Default 5 minutes

import os
import json
from functools import wraps
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse, urlunparse

from cachetools import TTLCache

from common.log import get_logger, info, warning

T = TypeVar('T')

logger = get_logger("redis_cache")

LOCAL_CACHE_MAXSIZE = 1000
LOCAL_CACHE_TTL_SECONDS = 600


def _redact_redis_url(redis_url: str) -> str:
    """Remove credentials from Redis URLs before logging them."""
    parsed = urlparse(redis_url)
    if not parsed.netloc or "@" not in parsed.netloc:
        return redis_url

    user_info, host_info = parsed.netloc.rsplit("@", 1)
    username = user_info.split(":", 1)[0] if ":" in user_info else user_info
    redacted_user_info = f"{username}:***" if username else "***"
    return urlunparse(parsed._replace(netloc=f"{redacted_user_info}@{host_info}"))


def _disable_redis(operation: str, exc: Exception) -> None:
    """Disable Redis after a runtime failure and fall back to local cache."""
    global REDIS_ENABLED, REDIS_CLIENT

    if REDIS_ENABLED:
        warning(
            logger,
            "Redis cache operation failed; falling back to local TTL cache",
            operation=operation,
            error=str(exc),
        )

    REDIS_ENABLED = False
    REDIS_CLIENT = None

# Check if Redis is available and import if it is
REDIS_ENABLED = False
REDIS_CLIENT = None
redis_url = os.environ.get('REDIS_URL')

if redis_url:
    try:
        import redis
        from redis.exceptions import RedisError

        REDIS_CLIENT = redis.from_url(redis_url)
        # Test the connection
        REDIS_CLIENT.ping()
        REDIS_ENABLED = True
        info(
            logger,
            "Redis cache enabled",
            cache_backend="redis",
            redis_url=_redact_redis_url(redis_url),
        )
    except ImportError as exc:
        warning(
            logger,
            "Redis client import failed; using local TTL cache",
            cache_backend="local_ttl",
            redis_url=_redact_redis_url(redis_url),
            error=str(exc),
        )
    except RedisError as exc:
        warning(
            logger,
            "Redis connection failed during startup; using local TTL cache",
            cache_backend="local_ttl",
            redis_url=_redact_redis_url(redis_url),
            error=str(exc),
        )
    except Exception as exc:
        warning(
            logger,
            "Unexpected Redis startup failure; using local TTL cache",
            cache_backend="local_ttl",
            redis_url=_redact_redis_url(redis_url),
            error=str(exc),
        )
else:
    info(
        logger,
        "Redis cache disabled; REDIS_URL not configured, using local TTL cache",
        cache_backend="local_ttl",
        local_ttl_seconds=LOCAL_CACHE_TTL_SECONDS,
    )

# Fallback local cache with 10 minute TTL
local_cache = TTLCache(maxsize=LOCAL_CACHE_MAXSIZE, ttl=LOCAL_CACHE_TTL_SECONDS)

def cache_key(*args, **kwargs) -> str:
    """Generate a consistent cache key from arguments."""
    key_parts = [str(arg) for arg in args]
    # Sort kwargs by key to ensure consistent ordering
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return ":".join(key_parts)

def get_cached(key: str, default: Any = None) -> Any:
    """
    Get a value from the cache.
    
    Args:
        key: The cache key
        default: Default value if key not found
        
    Returns:
        Cached value or default
    """
    if REDIS_ENABLED:
        try:
            value = REDIS_CLIENT.get(key)
            if value:
                return json.loads(value)
            return default
        except Exception as exc:
            _disable_redis("get", exc)
    
    return local_cache.get(key, default)

def set_cached(key: str, value: Any, ttl: int = 600) -> bool:
    """
    Set a value in the cache.
    
    Args:
        key: The cache key
        value: Value to cache
        ttl: Time to live in seconds (default: 10 minutes)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Convert value to JSON string for storage
        json_value = json.dumps(value)
        
        if REDIS_ENABLED:
            try:
                REDIS_CLIENT.setex(key, ttl, json_value)
                return True
            except Exception as exc:
                _disable_redis("set", exc)
        
        # Use local cache
        local_cache[key] = value
        return True
    except Exception:
        return False

def delete_cached(key: str) -> bool:
    """
    Delete a value from the cache.
    
    Args:
        key: The cache key
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if REDIS_ENABLED:
            try:
                REDIS_CLIENT.delete(key)
            except Exception as exc:
                _disable_redis("delete", exc)
        
        # Also remove from local cache
        if key in local_cache:
            del local_cache[key]
        
        return True
    except Exception:
        return False

def clear_pattern(pattern: str) -> bool:
    """
    Clear all keys matching a pattern.
    
    Args:
        pattern: Redis key pattern (e.g., "volunteer:*")
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if REDIS_ENABLED:
            try:
                keys = REDIS_CLIENT.keys(pattern)
                if keys:
                    REDIS_CLIENT.delete(*keys)
            except Exception as exc:
                _disable_redis("clear_pattern", exc)
        
        # For local cache, scan and remove matching keys
        keys_to_delete = [k for k in local_cache if k.startswith(pattern)]
        for k in keys_to_delete:
            if k in local_cache:
                del local_cache[k]
        
        return True
    except Exception:
        return False

def redis_cached(prefix: str, ttl: int = 600):
    """
    Decorator for caching function results.
    
    Args:
        prefix: Prefix for the cache key
        ttl: Time to live in seconds
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key = f"{prefix}:{cache_key(*args, **kwargs)}"
            
            # Try to get from cache
            cached_result = get_cached(key)
            if cached_result is not None:
                return cached_result
            
            # Call the function
            result = func(*args, **kwargs)
            
            # Cache the result
            set_cached(key, result, ttl)
            
            return result
        
        # Add method to clear cache for this function
        wrapper.cache_clear = lambda: clear_pattern(f"{prefix}:*")
        
        return wrapper
    return decorator
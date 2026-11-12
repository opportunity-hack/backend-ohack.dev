import os
import json
import time
from fnmatch import fnmatchcase
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


# A runtime Redis failure used to switch Redis off for the REST OF THE PROCESS:
# after one blip, one gunicorn worker kept using Redis while another served its
# own local copy — two cache versions again (the per-process bug class). Now a
# failure falls back to local and the connection is re-probed at most every
# REDIS_REPROBE_SECONDS, so every worker converges back on the shared cache.
REDIS_REPROBE_SECONDS = 30
_REDIS_RETRY_AT = None  # monotonic time of the next re-probe; None = no retry
REDIS_ENABLED = False
REDIS_CLIENT = None
redis_url = os.environ.get('REDIS_URL')
_REDIS_URL = redis_url
_REDIS_IMPORTABLE = False


def _disable_redis(operation: str, exc: Exception) -> None:
    """Fall back to the local cache after a runtime failure; re-probe later."""
    global REDIS_ENABLED, REDIS_CLIENT, _REDIS_RETRY_AT

    if REDIS_ENABLED:
        warning(
            logger,
            "Redis cache operation failed; falling back to local TTL cache",
            operation=operation,
            error=str(exc),
            reprobe_seconds=REDIS_REPROBE_SECONDS,
        )

    REDIS_ENABLED = False
    REDIS_CLIENT = None
    if _REDIS_URL and _REDIS_IMPORTABLE:
        _REDIS_RETRY_AT = time.monotonic() + REDIS_REPROBE_SECONDS


def _connect(url: str):
    import redis  # noqa: WPS433 - only reached when REDIS_URL is set
    client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    client.ping()
    return client


def _redis_available() -> bool:
    """True when Redis should be used for this call; re-probes when due."""
    global REDIS_ENABLED, REDIS_CLIENT, _REDIS_RETRY_AT

    if REDIS_ENABLED:
        return True
    if _REDIS_RETRY_AT is None or time.monotonic() < _REDIS_RETRY_AT:
        return False
    try:
        REDIS_CLIENT = _connect(_REDIS_URL)
        REDIS_ENABLED = True
        _REDIS_RETRY_AT = None
        info(logger, "Redis cache re-enabled after re-probe", cache_backend="redis")
    except Exception as exc:  # still down; try again later
        REDIS_CLIENT = None
        _REDIS_RETRY_AT = time.monotonic() + REDIS_REPROBE_SECONDS
        warning(logger, "Redis re-probe failed; staying on local TTL cache",
                error=str(exc), reprobe_seconds=REDIS_REPROBE_SECONDS)
    return REDIS_ENABLED


# Check if Redis is available and import if it is
if redis_url:
    try:
        import redis  # noqa: F401
        _REDIS_IMPORTABLE = True
    except ImportError as exc:
        warning(
            logger,
            "Redis client import failed; using local TTL cache",
            cache_backend="local_ttl",
            redis_url=_redact_redis_url(redis_url),
            error=str(exc),
        )

if _REDIS_IMPORTABLE:
    try:
        REDIS_CLIENT = _connect(redis_url)
        REDIS_ENABLED = True
        info(
            logger,
            "Redis cache enabled",
            cache_backend="redis",
            redis_url=_redact_redis_url(redis_url),
        )
    except Exception as exc:
        # Startup failure is no longer permanent either: re-probe later.
        REDIS_CLIENT = None
        _REDIS_RETRY_AT = time.monotonic() + REDIS_REPROBE_SECONDS
        warning(
            logger,
            "Redis connection failed during startup; using local TTL cache",
            cache_backend="local_ttl",
            redis_url=_redact_redis_url(redis_url),
            error=str(exc),
            reprobe_seconds=REDIS_REPROBE_SECONDS,
        )
elif not redis_url:
    info(
        logger,
        "Redis cache disabled; REDIS_URL not configured, using local TTL cache",
        cache_backend="local_ttl",
        local_ttl_seconds=LOCAL_CACHE_TTL_SECONDS,
    )

# Fallback local cache. The TTLCache's own TTL is only the CEILING (10 min);
# every entry also stores its own expiry so a caller's `ttl` is honoured.
# Before Nov 2026 the per-call ttl was ignored here, so a `ttl=2` lookup (the
# volunteer-by-user/email caches) was held for 10 minutes PER gunicorn worker:
# after an admin changed a mentor's status, one worker kept serving the old
# doc and refreshes of /mentor-checkin alternated between two states.
local_cache = TTLCache(maxsize=LOCAL_CACHE_MAXSIZE, ttl=LOCAL_CACHE_TTL_SECONDS)
_MISSING = object()


def _local_get(key: str, default: Any = None) -> Any:
    entry = local_cache.get(key, _MISSING)
    if entry is _MISSING:
        return default
    expires_at, value = entry
    if expires_at is not None and time.monotonic() >= expires_at:
        local_cache.pop(key, None)
        return default
    return value


def _local_set(key: str, value: Any, ttl: int) -> None:
    expires_at = time.monotonic() + ttl if ttl and ttl > 0 else None
    local_cache[key] = (expires_at, value)

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
    if _redis_available():
        try:
            value = REDIS_CLIENT.get(key)
            if value:
                return json.loads(value)
            return default
        except Exception as exc:
            _disable_redis("get", exc)
    
    return _local_get(key, default)

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
        
        if _redis_available():
            try:
                REDIS_CLIENT.setex(key, ttl, json_value)
                return True
            except Exception as exc:
                _disable_redis("set", exc)
        
        # Use local cache
        _local_set(key, value, ttl)
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
        if _redis_available():
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
        if _redis_available():
            try:
                keys = REDIS_CLIENT.keys(pattern)
                if keys:
                    REDIS_CLIENT.delete(*keys)
            except Exception as exc:
                _disable_redis("clear_pattern", exc)
        
        # For local cache, scan and remove matching keys. `pattern` is a Redis
        # glob ("volunteer:by_event:x:mentor*") — a literal startswith() never
        # matched the "*" and cleared nothing.
        keys_to_delete = [k for k in list(local_cache) if fnmatchcase(k, pattern)]
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
        # Read by common/utils/cache_registry.py's guard test.
        wrapper.cache_prefix = prefix
        wrapper.cache_ttl = ttl
        
        return wrapper
    return decorator
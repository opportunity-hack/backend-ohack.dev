import threading
import time
from functools import wraps

from cachetools import cached, TTLCache
from cachetools.keys import hashkey
from firebase_admin import firestore
from firebase_admin.firestore import DocumentReference, DocumentSnapshot

from common.log import get_logger

logger = get_logger("firestore_helpers")

# Registry of caches to clear
_cache_registry = []


def register_cache(cache_obj):
    """Register a cache for bulk clearing via clear_all_caches()."""
    _cache_registry.append(cache_obj)


def clear_all_caches():
    """Clear all registered caches and the doc_to_json cache.

    A registered entry may be either an @cached-decorated function (which
    cachetools gives a `cache_clear()` method) or a raw cache object such
    as a TTLCache (which uses `.clear()`). Try both.
    """
    doc_to_json.cache_clear()
    for cache_obj in _cache_registry:
        try:
            if hasattr(cache_obj, "cache_clear"):
                cache_obj.cache_clear()
            elif hasattr(cache_obj, "clear"):
                cache_obj.clear()
            else:
                logger.warning(
                    f"Registered cache has neither cache_clear nor clear: {type(cache_obj).__name__}"
                )
        except Exception as e:
            logger.warning(f"Failed to clear a registered cache: {e}")


def hash_key(docid, doc=None, depth=0):
    return hashkey(docid)


def log_execution_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logger.debug(f"{func.__name__} execution time: {execution_time:.4f} seconds")
        return result
    return wrapper


def _copy_json_like(value):
    """Recursively copy dicts and lists; return every other leaf as-is.

    Deliberately NOT copy.deepcopy: a DocumentReference leaf holds the
    Firestore client / gRPC channel and must be shared, not cloned.
    """
    if isinstance(value, dict):
        return {k: _copy_json_like(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_json_like(v) for v in value]
    return value


def _snapshot_to_json(docid, snapshot):
    d_json = snapshot.to_dict()
    if d_json is None:
        logger.warning(f"doc.to_dict() is NoneType | docid={docid} doc={snapshot}")
        return None

    # If any values in d_json is a list, add only the document id to the list for DocumentReference or DocumentSnapshot
    for key, value in d_json.items():
        if isinstance(value, list):
            for i, v in enumerate(value):
                if isinstance(v, (firestore.DocumentReference, firestore.DocumentSnapshot)):
                    value[i] = v.id
            d_json[key] = value

    d_json["id"] = docid
    return d_json


@cached(cache=TTLCache(maxsize=2000, ttl=600), lock=threading.Lock(), key=hash_key)
def _doc_to_json_cached(docid, doc):
    """Cached resolve of a DocumentReference (saves one Firestore .get()).

    Keyed by docid only, so it must never be fed a DocumentSnapshot — a
    snapshot's data is already in hand and may be fresher than the cache.
    """
    logger.debug("doc is DocumentReference")
    return _snapshot_to_json(docid, doc.get())


def doc_to_json(docid=None, doc=None, depth=0):
    """Convert a Firestore snapshot/reference to a plain dict.

    - DocumentSnapshot: converted directly, never cached (read already done).
    - DocumentReference: resolved through a 10-min per-process cache.
    - Anything else: returned unchanged.
    Every dict/list returned is a fresh copy, so callers may mutate it.
    """
    if not docid:
        logger.debug("docid is NoneType")
        return
    if not doc:
        logger.debug("doc is NoneType")
        return

    if isinstance(doc, firestore.DocumentSnapshot):
        logger.debug("doc is DocumentSnapshot")
        return _copy_json_like(_snapshot_to_json(docid, doc))
    if isinstance(doc, firestore.DocumentReference):
        return _copy_json_like(_doc_to_json_cached(docid, doc))
    return doc


# Keep the public name's cachetools API (callers/tests use doc_to_json.cache_clear()).
doc_to_json.cache = _doc_to_json_cached.cache
doc_to_json.cache_key = _doc_to_json_cached.cache_key
doc_to_json.cache_lock = _doc_to_json_cached.cache_lock
doc_to_json.cache_clear = _doc_to_json_cached.cache_clear


def doc_to_json_recursive(doc=None):
    logger.debug(f"doc_to_json_recursive start doc={doc}")

    if not doc:
        logger.debug("doc is NoneType")
        return

    docid = ""
    # Check if type is DocumentSnapshot
    if isinstance(doc, DocumentSnapshot):
        logger.debug("doc is DocumentSnapshot")
        d_json = doc_to_json(docid=doc.id, doc=doc)
        docid = doc.id
    # Check if type is DocumentReference
    elif isinstance(doc, DocumentReference):
        logger.debug("doc is DocumentReference")
        d = doc.get()
        docid = d.id
        d_json = doc_to_json(docid=doc.id, doc=d)
    else:
        logger.debug(f"Not DocumentSnapshot or DocumentReference, skipping - returning: {doc}")
        return doc

    d_json["id"] = docid
    return d_json

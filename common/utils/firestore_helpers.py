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
    """Clear all registered caches and the doc_to_json cache."""
    doc_to_json.cache_clear()
    for cache_obj in _cache_registry:
        try:
            cache_obj.cache_clear()
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


@cached(cache=TTLCache(maxsize=2000, ttl=3600), key=hash_key)
def doc_to_json(docid=None, doc=None, depth=0):
    if not docid:
        logger.debug("docid is NoneType")
        return
    if not doc:
        logger.debug("doc is NoneType")
        return

    # Check if type is DocumentSnapshot
    if isinstance(doc, firestore.DocumentSnapshot):
        logger.debug("doc is DocumentSnapshot")
        d_json = doc.to_dict()
    # Check if type is DocumentReference
    elif isinstance(doc, firestore.DocumentReference):
        logger.debug("doc is DocumentReference")
        d = doc.get()
        d_json = d.to_dict()
    else:
        return doc

    if d_json is None:
        logger.warn(f"doc.to_dict() is NoneType | docid={docid} doc={doc}")
        return

    # If any values in d_json is a list, add only the document id to the list for DocumentReference or DocumentSnapshot
    for key, value in d_json.items():
        if isinstance(value, list):
            for i, v in enumerate(value):
                logger.debug(f"doc_to_json - i={i} v={v}")
                if isinstance(v, firestore.DocumentReference):
                    value[i] = v.id
                elif isinstance(v, firestore.DocumentSnapshot):
                    value[i] = v.id
                else:
                    value[i] = v
            d_json[key] = value

    d_json["id"] = docid
    return d_json


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

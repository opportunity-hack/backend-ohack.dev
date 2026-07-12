"""Service layer for praise-bot configuration.

Stores bot behavior (channels, repos, cron schedules, feature toggles) in the
`praise_bot_config` Firestore collection so the Slack bot can be reconfigured
from /admin without touching Fly.io env vars. Secrets never live here — the
per-type key whitelists below are the enforcement point.

Doc types:
  - global            (fixed doc id "global": dry_run, llm_enabled, timezone)
  - github_watcher    (repo-tpm digest/rollup config)
  - calendar_reminder (Google Calendar ICS reminder config)
  - community         (#introductions matchmaker + weekly community digest)
"""
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from db.db import get_db
from common.log import get_logger

logger = get_logger(__name__)

COLLECTION = "praise_bot_config"
GLOBAL_DOC_ID = "global"

# 5 whitespace-separated cron fields; the bot re-validates with cron.validate()
_CRON_RE = re.compile(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$")
_REPO_SHORTHAND_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_REPO_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)

_ALLOWED_KEYS = {
    "global": {"dry_run", "llm_enabled", "timezone"},
    "github_watcher": {"name", "enabled", "source", "digest", "rollup", "dry_run"},
    "calendar_reminder": {
        "name", "enabled", "calendar_id", "channels", "lead_minutes",
        "events_page_url", "poll_cron",
    },
    "community": {
        "name", "enabled", "intro_channel", "matchmaker", "digest",
        "lookback_days", "dry_run",
    },
}
DOC_TYPES = set(_ALLOWED_KEYS.keys())

# Tiny last-good cache so the bot's 60s poll doesn't hammer Firestore
_cache_lock = threading.Lock()
_cache = {"value": None, "at": 0.0}
_CACHE_TTL_SECONDS = 15


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _filter_payload(doc_type, json_in):
    allowed = _ALLOWED_KEYS[doc_type]
    return {k: v for k, v in (json_in or {}).items() if k in allowed}


def _normalize_repo(repo):
    """Accept 'owner/repo' or a full GitHub URL; return 'owner/repo' or None."""
    if not isinstance(repo, str):
        return None
    repo = repo.strip()
    if _REPO_SHORTHAND_RE.match(repo):
        return repo
    m = _REPO_URL_RE.search(repo)
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    return None


def _normalize_channels(channels):
    """Accept a list or comma-separated string of channel names/IDs."""
    if isinstance(channels, str):
        channels = channels.split(",")
    if not isinstance(channels, list):
        return []
    return [c.strip().lstrip("#") for c in channels if isinstance(c, str) and c.strip()]


def _validate_cron(expr, field, errors, required=False):
    if expr in (None, ""):
        if required:
            errors.append(f"{field} is required")
        return
    if not isinstance(expr, str) or not _CRON_RE.match(expr):
        errors.append(f"{field} must be a 5-field cron expression")


def _validate_doc(doc_type, payload):
    """Validate + normalize a filtered payload in place. Returns error list."""
    errors = []

    if doc_type == "global":
        for key in ("dry_run", "llm_enabled"):
            if key in payload and not isinstance(payload[key], bool):
                errors.append(f"{key} must be a boolean")
        if "timezone" in payload and not isinstance(payload["timezone"], str):
            errors.append("timezone must be a string")

    elif doc_type == "github_watcher":
        source = payload.get("source")
        if source is not None:
            if not isinstance(source, dict):
                errors.append("source must be an object")
            else:
                mode = source.get("mode")
                if mode not in ("hackathon", "repos"):
                    errors.append("source.mode must be 'hackathon' or 'repos'")
                elif mode == "hackathon":
                    if not source.get("event_id"):
                        errors.append("source.event_id is required in hackathon mode")
                else:
                    repos = [_normalize_repo(r) for r in source.get("repos") or []]
                    if not repos or None in repos:
                        errors.append(
                            "source.repos must be a non-empty list of owner/repo or GitHub URLs")
                    else:
                        source["repos"] = repos
                    channels = _normalize_channels(source.get("channels"))
                    if not channels:
                        errors.append("source.channels is required in repos mode")
                    else:
                        source["channels"] = channels
                # Accepted and stored now, ignored by the bot until org
                # watching ships — avoids a schema migration later.
                if "orgs" in source and not isinstance(source["orgs"], list):
                    errors.append("source.orgs must be a list")
        for section, cron_required in (("digest", True), ("rollup", False)):
            block = payload.get(section)
            if block is None:
                continue
            if not isinstance(block, dict):
                errors.append(f"{section} must be an object")
                continue
            if block.get("enabled"):
                _validate_cron(block.get("cron"), f"{section}.cron", errors, required=cron_required)
            elif block.get("cron"):
                _validate_cron(block.get("cron"), f"{section}.cron", errors)
        rollup = payload.get("rollup")
        if isinstance(rollup, dict) and rollup.get("enabled") and not rollup.get("channel"):
            errors.append("rollup.channel is required when rollup is enabled")

    elif doc_type == "calendar_reminder":
        if "channels" in payload:
            channels = _normalize_channels(payload["channels"])
            if not channels:
                errors.append("channels must be a non-empty list")
            else:
                payload["channels"] = channels
        if "lead_minutes" in payload:
            lead = payload["lead_minutes"]
            if not isinstance(lead, int) or not 1 <= lead <= 240:
                errors.append("lead_minutes must be an integer between 1 and 240")
        _validate_cron(payload.get("poll_cron"), "poll_cron", errors)

    elif doc_type == "community":
        for section in ("matchmaker", "digest"):
            block = payload.get(section)
            if block is not None and not isinstance(block, dict):
                errors.append(f"{section} must be an object")
        digest = payload.get("digest")
        if isinstance(digest, dict) and digest.get("enabled"):
            _validate_cron(digest.get("cron"), "digest.cron", errors, required=True)
            if not digest.get("channel"):
                errors.append("digest.channel is required when the community digest is enabled")
        matchmaker = payload.get("matchmaker")
        if isinstance(matchmaker, dict) and "max_matches" in matchmaker:
            mm = matchmaker["max_matches"]
            if not isinstance(mm, int) or not 1 <= mm <= 10:
                errors.append("matchmaker.max_matches must be an integer between 1 and 10")
        if "lookback_days" in payload:
            lb = payload["lookback_days"]
            if not isinstance(lb, int) or not 1 <= lb <= 3650:
                errors.append("lookback_days must be an integer between 1 and 3650")

    return errors


def _clear_cache():
    with _cache_lock:
        _cache["value"] = None
        _cache["at"] = 0.0


def _all_docs():
    docs = []
    for doc in get_db().collection(COLLECTION).stream():
        adict = doc.to_dict() or {}
        adict["id"] = doc.id
        docs.append(adict)
    return docs


def get_full_config(include_audit=False):
    """Assemble the config payload for the bot (and the admin UI).

    include_audit=True keeps created_at/updated_at/updated_by on each doc.
    """
    if not include_audit:
        with _cache_lock:
            if _cache["value"] is not None and time.time() - _cache["at"] < _CACHE_TTL_SECONDS:
                return _cache["value"]

    docs = _all_docs()
    audit_keys = () if include_audit else ("created_at", "updated_at", "updated_by")

    global_cfg = {"dry_run": False, "llm_enabled": True}
    github_watchers = []
    calendar_reminders = []
    community = None

    for doc in docs:
        doc_type = doc.get("type")
        cleaned = {k: v for k, v in doc.items() if k not in audit_keys and k != "type"}
        if doc.get("id") == GLOBAL_DOC_ID or doc_type == "global":
            cleaned.pop("id", None)
            global_cfg.update(cleaned)
        elif doc_type == "github_watcher":
            github_watchers.append(cleaned)
        elif doc_type == "calendar_reminder":
            calendar_reminders.append(cleaned)
        elif doc_type == "community":
            community = cleaned
        else:
            logger.warning("Ignoring praise_bot_config doc %s with unknown type %s",
                           doc.get("id"), doc_type)

    github_watchers.sort(key=lambda d: d.get("name") or "")
    calendar_reminders.sort(key=lambda d: d.get("name") or "")

    result = {
        "configured": len(docs) > 0,
        "global": global_cfg,
        "github_watchers": github_watchers,
        "calendar_reminders": calendar_reminders,
        "community": community,
    }

    if not include_audit:
        with _cache_lock:
            _cache["value"] = result
            _cache["at"] = time.time()
    return result


def create_config_doc(json_in, actor):
    doc_type = (json_in or {}).get("type")
    if doc_type not in DOC_TYPES:
        return {"error": f"type must be one of {sorted(DOC_TYPES)}"}, 400
    if doc_type == "global":
        return {"error": "use PATCH /admin/config/global for global settings"}, 400
    if doc_type == "community":
        existing = [d for d in _all_docs() if d.get("type") == "community"]
        if existing:
            return {"error": f"a community config already exists (id {existing[0]['id']}) — PATCH it instead"}, 400

    payload = _filter_payload(doc_type, json_in)
    errors = _validate_doc(doc_type, payload)
    if errors:
        return {"error": "; ".join(errors)}, 400

    payload["type"] = doc_type
    payload.setdefault("enabled", False)
    payload["created_at"] = payload["updated_at"] = _now_iso()
    payload["updated_by"] = actor

    doc_id = str(uuid.uuid4())
    get_db().collection(COLLECTION).document(doc_id).set(payload)
    _clear_cache()
    logger.info("Created praise_bot_config %s doc %s by %s", doc_type, doc_id, actor)
    return {"id": doc_id}, 201


def update_config_doc(doc_id, json_in, actor):
    db = get_db()

    if doc_id == GLOBAL_DOC_ID:
        payload = _filter_payload("global", json_in)
        errors = _validate_doc("global", payload)
        if errors:
            return {"error": "; ".join(errors)}, 400
        payload["type"] = "global"
        payload["updated_at"] = _now_iso()
        payload["updated_by"] = actor
        db.collection(COLLECTION).document(GLOBAL_DOC_ID).set(payload, merge=True)
        _clear_cache()
        return {"id": GLOBAL_DOC_ID}, 200

    ref = db.collection(COLLECTION).document(doc_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return {"error": "not found"}, 404
    doc_type = (snapshot.to_dict() or {}).get("type")
    if doc_type not in DOC_TYPES:
        return {"error": f"document has unknown type {doc_type}"}, 400

    payload = _filter_payload(doc_type, json_in)
    if not payload:
        return {"error": "no updatable fields in payload"}, 400
    errors = _validate_doc(doc_type, payload)
    if errors:
        return {"error": "; ".join(errors)}, 400

    payload["updated_at"] = _now_iso()
    payload["updated_by"] = actor
    ref.update(payload)
    _clear_cache()
    logger.info("Updated praise_bot_config doc %s by %s", doc_id, actor)
    return {"id": doc_id}, 200


def delete_config_doc(doc_id):
    if doc_id == GLOBAL_DOC_ID:
        return {"error": "global settings cannot be deleted"}, 400
    ref = get_db().collection(COLLECTION).document(doc_id)
    if not ref.get().exists:
        return {"error": "not found"}, 404
    ref.delete()
    _clear_cache()
    logger.info("Deleted praise_bot_config doc %s", doc_id)
    return {"id": doc_id}, 200

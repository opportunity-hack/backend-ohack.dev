"""Personal access tokens for the admin MCP endpoint.

An admin mints a token on the site (PropelAuth session required) and puts
it in their MCP client config; ``POST /api/mcp`` accepts it in place of the
30-minute PropelAuth access token.

Collection ``mcp_tokens`` (doc id = sha256 of the raw token): id, name,
user_id (PropelAuth), email, org_id, token_hint, created_at, expires_at,
last_used_at.

Rules:
- The raw token is returned ONCE, at creation; only its hash is stored.
- A token carries NO permissions. Every use re-reads the owner's current
  permissions from PropelAuth (cached ``_IDENTITY_TTL_SECONDS``), so
  removing a role or disabling the account cuts the token off too.
- Tokens expire (``MAX_EXPIRY_DAYS`` at most) and are capped at
  ``MAX_ACTIVE_PER_USER`` per person. Revoking deletes the doc.
- A token cannot mint, list or revoke tokens: those routes take a
  PropelAuth session only.
"""
import hashlib
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from db.db import get_db
from common.log import get_logger

logger = get_logger(__name__)

COLLECTION = "mcp_tokens"
TOKEN_PREFIX = "ohmcp_"

MAX_ACTIVE_PER_USER = 5
MAX_NAME_LENGTH = 60
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 90
DEFAULT_EXPIRY_DAYS = 90

# last_used_at is informational: one write per token per interval, not one
# per MCP request.
_LAST_USED_WRITE_SECONDS = 300
_IDENTITY_TTL_SECONDS = 60

_identity_cache = TTLCache(maxsize=256, ttl=_IDENTITY_TTL_SECONDS)
_identity_lock = threading.Lock()


class IdentityUnavailable(Exception):
    """PropelAuth could not be reached to confirm the token owner."""


class _OrgPermissions(object):
    def __init__(self, permissions):
        self.user_permissions = list(permissions)

    def user_has_permission(self, permission):
        return permission in self.user_permissions


class TokenUser(object):
    """Stand-in for the PropelAuth request user, built from a token.

    Carries the attributes the MCP dispatcher and
    ``common.auth.user_has_any_permission`` read.
    """

    def __init__(self, user_id, email, name, org_id, permissions, token_id):
        self.user_id = user_id
        self.email = email
        self.name = name
        self.org_id = org_id
        self.token_id = token_id
        self.org_id_to_org_member_info = {org_id: _OrgPermissions(permissions)}


def _now():
    # type: () -> datetime
    return datetime.now(timezone.utc)


def _parse(value):
    # type: (Any) -> Optional[datetime]
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _hash(raw):
    # type: (str) -> str
    # The raw token is 256 random bits, so a plain SHA-256 is enough: there
    # is nothing to brute-force and the lookup stays a single doc read.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_expired(record, now=None):
    # type: (Dict[str, Any], Optional[datetime]) -> bool
    expires = _parse(record.get("expires_at"))
    return expires is None or expires <= (now or _now())


def _public(record):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "token_hint": record.get("token_hint"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "last_used_at": record.get("last_used_at"),
        "status": "expired" if _is_expired(record) else "active",
    }


def _error(code, message, status):
    # type: (str, str, int) -> Tuple[Dict[str, Any], int]
    return {"error": code, "message": message}, status


def _audit(action, message, payload):
    # type: (str, str, Dict[str, Any]) -> None
    if os.environ.get("ENVIRONMENT") == "test":
        return
    try:
        from common.utils.slack import send_slack_audit

        send_slack_audit(action=action, message=message, payload=payload)
    except Exception as e:
        logger.warning("MCP token audit failed (%s): %s", action, type(e).__name__)


def _user_records(user_id):
    # type: (str) -> List[Dict[str, Any]]
    db = get_db()
    return [
        doc.to_dict() or {}
        for doc in db.collection(COLLECTION).where("user_id", "==", user_id).stream()
    ]


def list_tokens(admin):
    # type: (Dict[str, Any]) -> Tuple[Dict[str, Any], int]
    user_id = admin.get("propel_id")
    if not user_id:
        return _error("unauthorized", "No user on the request", 401)
    records = _user_records(user_id)
    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {
        "data": [_public(r) for r in records],
        "max_active": MAX_ACTIVE_PER_USER,
        "max_expiry_days": MAX_EXPIRY_DAYS,
    }, 200


def create_token(admin, org_id, body):
    # type: (Dict[str, Any], Any, Any) -> Tuple[Dict[str, Any], int]
    user_id = admin.get("propel_id")
    if not user_id:
        return _error("unauthorized", "No user on the request", 401)
    if not isinstance(org_id, str) or not org_id.strip():
        return _error("invalid_org", "An organization id is required", 400)
    if not isinstance(body, dict):
        return _error("invalid_body", "Request body must be a JSON object", 400)

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error("invalid_name", "A name is required", 400)
    name = " ".join(name.split())
    if len(name) > MAX_NAME_LENGTH:
        return _error(
            "invalid_name", "Name must be %d characters or fewer" % MAX_NAME_LENGTH, 400
        )

    days = body.get("expiresInDays", DEFAULT_EXPIRY_DAYS)
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or not MIN_EXPIRY_DAYS <= days <= MAX_EXPIRY_DAYS
    ):
        return _error(
            "invalid_expiry",
            "expiresInDays must be a whole number from %d to %d"
            % (MIN_EXPIRY_DAYS, MAX_EXPIRY_DAYS),
            400,
        )

    now = _now()
    active = [r for r in _user_records(user_id) if not _is_expired(r, now)]
    if len(active) >= MAX_ACTIVE_PER_USER:
        return _error(
            "token_limit",
            "You already have %d active tokens. Revoke one first." % MAX_ACTIVE_PER_USER,
            409,
        )

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = {
        "id": secrets.token_hex(8),
        "name": name,
        "user_id": user_id,
        "email": admin.get("email"),
        "org_id": org_id.strip(),
        "token_hint": raw[: len(TOKEN_PREFIX) + 4],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "last_used_at": None,
    }
    get_db().collection(COLLECTION).document(_hash(raw)).set(record)
    logger.info("MCP token %s created by %s", record["id"], user_id)
    _audit(
        "mcp_token_created",
        "MCP token '%s' created by %s" % (name, admin.get("email") or user_id),
        {"token_id": record["id"], "caller": user_id, "expires_at": record["expires_at"]},
    )
    # The only response that ever carries the raw token.
    return {"token": raw, "data": _public(record)}, 201


def revoke_token(admin, token_id):
    # type: (Dict[str, Any], Any) -> Tuple[Dict[str, Any], int]
    user_id = admin.get("propel_id")
    if not user_id:
        return _error("unauthorized", "No user on the request", 401)
    db = get_db()
    for doc in db.collection(COLLECTION).where("user_id", "==", user_id).stream():
        record = doc.to_dict() or {}
        if record.get("id") != token_id:
            continue
        db.collection(COLLECTION).document(doc.id).delete()
        logger.info("MCP token %s revoked by %s", token_id, user_id)
        _audit(
            "mcp_token_revoked",
            "MCP token '%s' revoked by %s"
            % (record.get("name"), admin.get("email") or user_id),
            {"token_id": token_id, "caller": user_id},
        )
        return {"success": True}, 200
    # Someone else's token id answers the same as an unknown one.
    return _error("not_found", "Token not found", 404)


def resolve_token(raw):
    # type: (Any) -> Optional[Dict[str, Any]]
    """The stored record for a raw token, or None when it is unknown or
    expired. Stamps ``last_used_at`` at most once per write interval."""
    if not isinstance(raw, str) or not raw.startswith(TOKEN_PREFIX):
        return None
    ref = get_db().collection(COLLECTION).document(_hash(raw))
    snap = ref.get()
    if not snap.exists:
        return None
    record = snap.to_dict() or {}
    now = _now()
    if _is_expired(record, now):
        return None
    last_used = _parse(record.get("last_used_at"))
    if last_used is None or (now - last_used).total_seconds() >= _LAST_USED_WRITE_SECONDS:
        try:
            ref.update({"last_used_at": now.isoformat()})
        except Exception as e:
            logger.warning("MCP token last_used_at write failed: %s", type(e).__name__)
    return record


def _fetch_identity(user_id, org_id):
    # type: (str, str) -> Optional[Dict[str, Any]]
    """The owner's CURRENT name, email and permissions in ``org_id``, read
    from PropelAuth. None when the account is gone, disabled or locked."""
    try:
        from common.auth import auth  # lazy: the real module builds a client at import

        meta = auth.fetch_user_metadata_by_user_id(user_id, include_orgs=True)
    except Exception as e:
        logger.warning("MCP token owner lookup failed: %s", type(e).__name__)
        raise IdentityUnavailable()
    if meta is None:
        return None
    if getattr(meta, "locked", False) or not getattr(meta, "enabled", True):
        return None
    orgs = getattr(meta, "org_id_to_org_info", None) or {}
    org_info = orgs.get(org_id) if isinstance(orgs, dict) else None
    # No longer in the org -> no permissions (the gate answers 403).
    permissions = []  # type: List[str]
    if isinstance(org_info, dict):
        permissions = [p for p in org_info.get("user_permissions") or [] if isinstance(p, str)]
    elif org_info is not None:
        permissions = list(getattr(org_info, "user_permissions", None) or [])
    first = getattr(meta, "first_name", None) or ""
    last = getattr(meta, "last_name", None) or ""
    return {
        "email": getattr(meta, "email", None),
        "name": ("%s %s" % (first, last)).strip() or None,
        "permissions": permissions,
    }


def _identity(user_id, org_id):
    # type: (str, str) -> Optional[Dict[str, Any]]
    key = (user_id, org_id)
    with _identity_lock:
        if key in _identity_cache:
            return _identity_cache[key]
    identity = _fetch_identity(user_id, org_id)
    with _identity_lock:
        _identity_cache[key] = identity
    return identity


def clear_identity_cache():
    # type: () -> None
    with _identity_lock:
        _identity_cache.clear()


def user_for_token(raw):
    # type: (Any) -> Optional[TokenUser]
    """The caller a raw token stands for, with their live permissions.

    None when the token is unknown / expired or its owner is gone.
    Raises ``IdentityUnavailable`` when PropelAuth cannot be reached: a
    token is never honoured on stale or assumed permissions.
    """
    record = resolve_token(raw)
    if record is None:
        return None
    user_id = record.get("user_id")
    org_id = record.get("org_id")
    if not user_id or not org_id:
        return None
    identity = _identity(user_id, org_id)
    if identity is None:
        return None
    return TokenUser(
        user_id=user_id,
        email=identity.get("email") or record.get("email"),
        name=identity.get("name"),
        org_id=org_id,
        permissions=identity.get("permissions") or [],
        token_id=record.get("id"),
    )

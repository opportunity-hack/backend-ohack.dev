"""Dynamic per-team LiteLLM gateway API keys.

Keys are minted when an organizer approves a team (see approve_team in
api/teams/teams_service.py), so a team only gets a key after its nonprofit
problem is approved. No pre-provisioned CSV pool.

Plaintext keys are Fernet-encrypted before they touch Firestore, and are
only ever returned to team members (or admins) through the member-only GET
endpoint. They are never logged.
"""
import logging
import os
from datetime import datetime, timezone

import requests
from cryptography.fernet import Fernet

from db.db import get_db
from api.teams.teams_service import user_is_on_team

logger = logging.getLogger(__name__)

COLLECTION = "team_gateway_keys"
GATEWAY_MODELS = ["muse-spark", "kimi-k2.7-code", "gpt-oss-120b"]
GATEWAY_MAX_BUDGET = 15.0
# Keys die on their own after the event; no post-event cleanup script needed.
GATEWAY_KEY_EXPIRES = "2026-11-16T07:00:00Z"
KEY_ALIAS_PREFIX = "fall26-"


def _base_url():
    return os.environ.get("LITELLM_BASE_URL", "https://ai.ohack.dev").rstrip("/")


def _master_key():
    key = os.environ.get("LITELLM_MASTER_KEY")
    if not key:
        raise RuntimeError(
            "LITELLM_MASTER_KEY env var is not set; cannot call the LiteLLM gateway"
        )
    return key


def _fernet():
    raw = os.environ.get("GATEWAY_KEY_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError(
            "GATEWAY_KEY_ENCRYPTION_KEY env var is not set; generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(raw.encode())
    except Exception:
        raise RuntimeError(
            "GATEWAY_KEY_ENCRYPTION_KEY is not a valid Fernet key; generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )


def _key_alias(team_id):
    return f"{KEY_ALIAS_PREFIX}{team_id}"


def _doc_ref(team_id):
    return get_db().collection(COLLECTION).document(team_id)


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _public_metadata(team_id, data):
    return {
        "team_id": team_id,
        "key_alias": data.get("key_alias"),
        "models": data.get("models", GATEWAY_MODELS),
        "max_budget": data.get("max_budget", GATEWAY_MAX_BUDGET),
        "expires": data.get("expires", GATEWAY_KEY_EXPIRES),
    }


def _generate_key(alias, team_id):
    """Call LiteLLM /key/generate. Returns the plaintext key (once only)."""
    body = {
        "key_alias": alias,
        # No budget_duration: the $15 cap is a lifetime cap, matching the
        # pre-provisioned key design.
        "models": GATEWAY_MODELS,
        "max_budget": GATEWAY_MAX_BUDGET,
        "expires": GATEWAY_KEY_EXPIRES,
        "metadata": {
            "ohack_team_id": team_id,
            "event": "fall-2026",
            "provisioned_by": "ohack-backend",
        },
    }
    r = requests.post(
        f"{_base_url()}/key/generate",
        json=body,
        headers={"Authorization": f"Bearer {_master_key()}"},
        timeout=30,
    )
    r.raise_for_status()
    key = r.json().get("key")
    if not key:
        raise RuntimeError(f"LiteLLM /key/generate returned no key for alias {alias}")
    return key


def provision_team_gateway_key(team_id):
    """Mint a LiteLLM key for a team. Idempotent: an existing active key is
    returned as-is, nothing is minted twice."""
    alias = _key_alias(team_id)
    ref = _doc_ref(team_id)
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        if data.get("status") == "active" and data.get("key_ciphertext"):
            logger.info(
                "Gateway key already active for team %s (alias %s); skipping mint",
                team_id, alias,
            )
            return _public_metadata(team_id, data)

    # Fail fast on misconfiguration before touching Firestore or LiteLLM.
    _master_key()
    fernet = _fernet()

    # Write pending FIRST so a crash mid-mint is recoverable: the plaintext
    # is only returned by LiteLLM at generation time, so on failure we delete
    # the pending doc and the next attempt starts clean.
    ref.set({
        "team_id": team_id,
        "key_alias": alias,
        "status": "pending",
        "created_at": _utcnow_iso(),
    })
    logger.info("Provisioning gateway key for team %s (alias %s)", team_id, alias)
    try:
        plaintext = _generate_key(alias, team_id)
    except Exception:
        ref.delete()
        logger.warning(
            "Gateway key mint failed for team %s (alias %s); pending doc removed",
            team_id, alias,
        )
        raise

    ciphertext = fernet.encrypt(plaintext.encode()).decode()
    data = {
        "team_id": team_id,
        "key_alias": alias,
        "status": "active",
        "key_ciphertext": ciphertext,
        "models": GATEWAY_MODELS,
        "max_budget": GATEWAY_MAX_BUDGET,
        "expires": GATEWAY_KEY_EXPIRES,
        "provisioned_at": _utcnow_iso(),
    }
    ref.set(data)
    logger.info("Gateway key active for team %s (alias %s)", team_id, alias)
    return _public_metadata(team_id, data)


def _key_spend_best_effort(plaintext_key, team_id):
    """Current spend for a key. Never raises; None when LiteLLM is unhappy."""
    try:
        r = requests.post(
            f"{_base_url()}/key/info",
            json={"key": plaintext_key},
            headers={"Authorization": f"Bearer {_master_key()}"},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
        info = payload.get("info", {}) if isinstance(payload, dict) else {}
        return info.get("spend", payload.get("spend"))
    except Exception as e:
        logger.warning("Gateway key spend lookup failed for team %s: %s", team_id, e)
        return None


def get_team_gateway_key(propel_user_id, team_id, is_admin=False):
    """Return (payload, status). The payload carries the plaintext key and is
    only ever returned to team members or admins."""
    db = get_db()
    if not db.collection("teams").document(team_id).get().exists:
        return {"error": "team_not_found"}, 404
    if not is_admin and not user_is_on_team(propel_user_id, team_id):
        logger.info(
            "Gateway key denied for user %s on team %s: not a team member",
            propel_user_id, team_id,
        )
        return {"error": "not_team_member"}, 403

    snap = _doc_ref(team_id).get()
    data = snap.to_dict() if snap.exists else None
    if not data or data.get("status") != "active" or not data.get("key_ciphertext"):
        return {"error": "key_not_provisioned", "retryable": True}, 404

    plaintext = _fernet().decrypt(data["key_ciphertext"].encode()).decode()
    payload = _public_metadata(team_id, data)
    payload["key"] = plaintext
    payload["spend"] = _key_spend_best_effort(plaintext, team_id)
    return payload, 200


def rotate_team_gateway_key(team_id):
    """Delete the team's key in LiteLLM and mint a fresh one under the same
    alias. The alias stays stable so dashboards keep working."""
    ref = _doc_ref(team_id)
    snap = ref.get()
    data = snap.to_dict() if snap.exists else None
    if not data or data.get("status") != "active" or not data.get("key_ciphertext"):
        raise RuntimeError(f"No active gateway key for team {team_id}; cannot rotate")
    alias = data["key_alias"]
    plaintext = _fernet().decrypt(data["key_ciphertext"].encode()).decode()

    logger.info("Rotating gateway key for team %s (alias %s): deleting old key", team_id, alias)
    r = requests.post(
        f"{_base_url()}/key/delete",
        json={"keys": [plaintext]},
        headers={"Authorization": f"Bearer {_master_key()}"},
        timeout=30,
    )
    r.raise_for_status()

    logger.info("Old gateway key deleted for team %s (alias %s); minting replacement", team_id, alias)
    try:
        new_plaintext = _generate_key(alias, team_id)
    except Exception:
        # Old key is gone and the alias is free; drop the doc so the next
        # provision attempt starts clean instead of serving a dead key.
        ref.delete()
        logger.warning(
            "Gateway key rotation failed for team %s (alias %s) after delete; doc removed",
            team_id, alias,
        )
        raise

    ref.set({
        "key_ciphertext": _fernet().encrypt(new_plaintext.encode()).decode(),
        "provisioned_at": _utcnow_iso(),
        "rotated_at": _utcnow_iso(),
    }, merge=True)
    logger.info("Gateway key rotated for team %s (alias %s)", team_id, alias)
    return _public_metadata(team_id, {**data, "key_alias": alias})

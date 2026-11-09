"""Dynamic per-team LiteLLM gateway API keys.

Two keys are minted when an organizer approves a team (see approve_team in
api/teams/teams_service.py), so a team only gets keys after its nonprofit
problem is approved. No pre-provisioned CSV pool.

Key 1 ($15 lifetime): muse-spark, kimi-k2.7-code, gpt-oss-120b.
Key 2 ($20 lifetime): muse-wonder, kimi-k3 (the frontier models).

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
KEY2_MODELS = ["muse-wonder", "kimi-k3"]
KEY2_MAX_BUDGET = 20.0
# Keys die on their own after the event; no post-event cleanup script needed.
# LiteLLM's /key/generate has NO `expires` field (it is silently dropped) —
# expiry is set by sending `duration` (e.g. "600000s"), which LiteLLM turns
# into expires = now + duration. `_key_duration()` derives it from this instant.
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


def _key2_alias(team_id):
    return f"{KEY_ALIAS_PREFIX}{team_id}-frontier"


def _doc_ref(team_id):
    return get_db().collection(COLLECTION).document(team_id)


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _key_duration(now=None, expires=GATEWAY_KEY_EXPIRES):
    """Seconds from `now` until `expires`, as the LiteLLM duration string
    ("<n>s") that /key/generate and /key/update accept. Raises once the
    expiry instant has passed — a key minted then would be dead on arrival,
    and provisioning is best-effort in approve_team, so failing loudly beats
    handing a team an unusable key."""
    now = now or datetime.now(timezone.utc)
    until = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    seconds = int((until - now).total_seconds())
    if seconds <= 0:
        raise RuntimeError(
            f"GATEWAY_KEY_EXPIRES ({expires}) is in the past; refusing to mint "
            "a gateway key that would expire immediately"
        )
    return f"{seconds}s"


# One spec per key. Key 1 fields are unprefixed; Key 2 fields are
# key2_ prefixed, all in the same Firestore doc (doc id = team id).
_KEY_SPECS = (
    {
        "label": "Key 1",
        "key": "key1",
        "status_field": "status",
        "ciphertext_field": "key_ciphertext",
        "alias_field": "key_alias",
        "models_field": "models",
        "budget_field": "max_budget",
        "provisioned_field": "provisioned_at",
        "rotated_field": "rotated_at",
        "models": GATEWAY_MODELS,
        "max_budget": GATEWAY_MAX_BUDGET,
        "alias_fn": _key_alias,
        "key_label": None,
    },
    {
        "label": "Key 2",
        "key": "key2",
        "status_field": "key2_status",
        "ciphertext_field": "key2_ciphertext",
        "alias_field": "key2_alias",
        "models_field": "key2_models",
        "budget_field": "key2_max_budget",
        "provisioned_field": "key2_provisioned_at",
        "rotated_field": "key2_rotated_at",
        "models": KEY2_MODELS,
        "max_budget": KEY2_MAX_BUDGET,
        "alias_fn": _key2_alias,
        "key_label": "key2",
    },
)


def _spec_for_key(key_name):
    for spec in _KEY_SPECS:
        if spec["key"] == key_name:
            return spec
    raise ValueError(f"Unknown gateway key: {key_name!r}; expected 'key1' or 'key2'")


def _is_active(data, spec):
    return (
        data.get(spec["status_field"]) == "active"
        and bool(data.get(spec["ciphertext_field"]))
    )


def _key_entry(team_id, data, spec, plaintext=None, spend=None, include_secret=True):
    """One entry of a "keys" array. Plaintext and spend appear only in the
    member GET response; metadata-only responses omit them entirely."""
    entry = {
        "label": spec["label"],
        "key_alias": data.get(spec["alias_field"]) or spec["alias_fn"](team_id),
        "models": data.get(spec["models_field"]) or list(spec["models"]),
        "max_budget": data.get(spec["budget_field"], spec["max_budget"]),
        "expires": data.get("expires", GATEWAY_KEY_EXPIRES),
        "status": data.get(spec["status_field"]) or "missing",
    }
    if include_secret:
        entry["key"] = plaintext
        entry["spend"] = spend
    return entry


def _metadata(team_id, data):
    """Metadata-only payload for provision/rotate/retry: team id plus one
    entry per key. No plaintext, no spend."""
    return {
        "team_id": team_id,
        "keys": [
            _key_entry(team_id, data, spec, include_secret=False)
            for spec in _KEY_SPECS
        ],
    }


def _generate_key(alias, team_id, models=None, max_budget=None, key_label=None):
    """Call LiteLLM /key/generate. Returns the plaintext key (once only)."""
    if models is None:
        models = GATEWAY_MODELS
    if max_budget is None:
        max_budget = GATEWAY_MAX_BUDGET
    metadata = {
        "ohack_team_id": team_id,
        "event": "fall-2026",
        "provisioned_by": "ohack-backend",
    }
    if key_label:
        metadata["key_label"] = key_label
    body = {
        "key_alias": alias,
        # No budget_duration: the caps are lifetime caps, matching the
        # pre-provisioned key design.
        "models": models,
        "max_budget": max_budget,
        # `duration`, not `expires`: LiteLLM ignores an `expires` field on
        # /key/generate, which left every key non-expiring (found Nov 8 2026).
        "duration": _key_duration(),
        "metadata": metadata,
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


def _mint_one(team_id, ref, fernet, spec):
    """Mint one key in LiteLLM and store it (merge, so the other key's
    fields survive). Raises on LiteLLM failure; nothing is stored then."""
    alias = spec["alias_fn"](team_id)
    plaintext = _generate_key(
        alias, team_id, spec["models"], spec["max_budget"], spec["key_label"]
    )
    fields = {
        spec["status_field"]: "active",
        spec["ciphertext_field"]: fernet.encrypt(plaintext.encode()).decode(),
        spec["alias_field"]: alias,
        spec["models_field"]: list(spec["models"]),
        spec["budget_field"]: spec["max_budget"],
        spec["provisioned_field"]: _utcnow_iso(),
    }
    if spec["key"] == "key1":
        fields["expires"] = GATEWAY_KEY_EXPIRES
    ref.set(fields, merge=True)
    return plaintext


def provision_team_gateway_key(team_id):
    """Mint LiteLLM keys for a team. Idempotent per key: an existing active
    key is returned as-is, nothing is minted twice.

    If one key mints and the other fails, the live key stays active, the
    failed key is recorded as missing, and the metadata is RETURNED (not
    raised) with a ``partial_failure`` entry so the retry endpoint can tell
    the admin UI the true state ("Key 1 ready, Key 2 failed"). Raising
    here would hide the live key behind a 502. Only when no key at all is
    usable is the pending doc removed and the error raised (the pre-Key 2
    behavior).
    """
    alias = _key_alias(team_id)
    ref = _doc_ref(team_id)
    snap = ref.get()
    had_doc = snap.exists
    data = snap.to_dict() if had_doc else {}
    key1_active = _is_active(data, _KEY_SPECS[0])
    key2_active = _is_active(data, _KEY_SPECS[1])
    if key1_active and key2_active:
        logger.info(
            "Gateway keys already active for team %s; skipping mint", team_id
        )
        return _metadata(team_id, data)

    # Fail fast on misconfiguration before touching Firestore or LiteLLM.
    _master_key()
    fernet = _fernet()

    # Write pending FIRST so a crash mid-mint is recoverable: the plaintext
    # is only returned by LiteLLM at generation time.
    if not had_doc:
        ref.set({
            "team_id": team_id,
            "key_alias": alias,
            "status": "pending",
            "created_at": _utcnow_iso(),
        })
    logger.info("Provisioning gateway keys for team %s", team_id)
    partial_failure = None
    try:
        if not key1_active:
            _mint_one(team_id, ref, fernet, _KEY_SPECS[0])
            key1_active = True
        if not key2_active:
            _mint_one(team_id, ref, fernet, _KEY_SPECS[1])
            key2_active = True
    except Exception as e:
        if not key1_active and not key2_active:
            ref.delete()
            logger.warning(
                "Gateway key mint failed for team %s; pending doc removed",
                team_id,
            )
            raise
        failed = _KEY_SPECS[0] if not key1_active else _KEY_SPECS[1]
        ref.set({
            failed["status_field"]: "missing",
            failed["alias_field"]: failed["alias_fn"](team_id),
            failed["models_field"]: list(failed["models"]),
            failed["budget_field"]: failed["max_budget"],
        }, merge=True)
        logger.warning(
            "Gateway %s mint failed for team %s; other key kept active: %s",
            failed["label"], team_id, e,
        )
        partial_failure = {
            "key": failed["key"],
            "label": failed["label"],
            "error": str(e),
        }

    data = ref.get().to_dict() or {}
    meta = _metadata(team_id, data)
    if partial_failure:
        meta["partial_failure"] = partial_failure
    else:
        logger.info("Gateway keys active for team %s", team_id)
    return meta


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


MAX_STATUS_BATCH = 200


def _status_entry(team_id, data, spec):
    """One entry of the batch "keys" array: metadata only, plus the
    provision/rotation timestamps the admin table shows. An "active"
    doc with no ciphertext is unusable, so it reports as missing."""
    status = data.get(spec["status_field"]) or "missing"
    if status == "active" and not data.get(spec["ciphertext_field"]):
        status = "missing"
    return {
        "label": spec["label"],
        "key_alias": data.get(spec["alias_field"]) or spec["alias_fn"](team_id),
        "models": data.get(spec["models_field"]) or list(spec["models"]),
        "max_budget": data.get(spec["budget_field"], spec["max_budget"]),
        "expires": data.get("expires", GATEWAY_KEY_EXPIRES),
        "status": status,
        "provisioned_at": data.get(spec["provisioned_field"]),
        "rotated_at": data.get(spec["rotated_field"]),
    }


def list_gateway_key_statuses(team_ids):
    """Admin-only, metadata-only status for many teams at once (the admin
    Teams table). NEVER includes the plaintext or ciphertext — the per-team
    GET is the only route that decrypts, and only on an explicit reveal.

    Each team maps to {"keys": [one status entry per key]}. Statuses:
    ``active`` (usable key), ``pending`` (mint in flight or crashed
    mid-mint; the next provision attempt starts clean), ``missing`` (no doc
    or approval's best-effort mint failed). Unknown ids report both keys
    as ``missing`` with their deterministic aliases, models, and budgets,
    rather than being omitted, so the frontend can render one chip per row.
    """
    out = {}
    for team_id in list(dict.fromkeys(t for t in team_ids if t))[:MAX_STATUS_BATCH]:
        snap = _doc_ref(team_id).get()
        data = snap.to_dict() if snap.exists else {}
        out[team_id] = {
            "keys": [_status_entry(team_id, data, spec) for spec in _KEY_SPECS]
        }
    return out


def get_team_gateway_key(propel_user_id, team_id, is_admin=False):
    """Return (payload, status). The payload is {"team_id", "keys": [...]},
    one entry per key carrying its plaintext and spend. It is only ever
    returned to team members or admins.

    404 only when no key is active. A decrypt failure on one key reports
    that key as "error" and still returns the other; it never 500s the
    response.
    """
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
    if not data:
        return {"error": "key_not_provisioned", "retryable": True}, 404
    key1_active = _is_active(data, _KEY_SPECS[0])
    key2_active = _is_active(data, _KEY_SPECS[1])
    if not key1_active and not key2_active:
        return {"error": "key_not_provisioned", "retryable": True}, 404

    try:
        fernet = _fernet()
    except Exception as e:
        logger.warning("Gateway key decrypt unavailable for team %s: %s", team_id, e)
        fernet = None

    entries = []
    for spec, active in ((_KEY_SPECS[0], key1_active), (_KEY_SPECS[1], key2_active)):
        if not active:
            entries.append(_key_entry(team_id, data, spec))
            continue
        try:
            plaintext = fernet.decrypt(
                data[spec["ciphertext_field"]].encode()
            ).decode()
        except Exception as e:
            logger.warning(
                "Gateway %s decrypt failed for team %s: %s", spec["label"], team_id, e
            )
            entry = _key_entry(team_id, data, spec)
            entry["status"] = "error"
            entries.append(entry)
            continue
        spend = _key_spend_best_effort(plaintext, team_id)
        entries.append(_key_entry(team_id, data, spec, plaintext, spend))

    return {"team_id": team_id, "keys": entries}, 200


def rotate_team_gateway_key(team_id, key):
    """Delete the team's named key in LiteLLM and mint a fresh one under the
    same alias. The alias stays stable so dashboards keep working. Only the
    named key is touched; the other key is left alone.

    A mint failure after the delete marks only that key missing (its
    ciphertext is cleared so retry can mint a fresh one) instead of
    dropping the doc and losing the other key.
    """
    spec = _spec_for_key(key)
    ref = _doc_ref(team_id)
    snap = ref.get()
    data = snap.to_dict() if snap.exists else None
    if not data or not _is_active(data, spec):
        raise RuntimeError(
            f"No active gateway key ({spec['label']}) for team {team_id}; cannot rotate"
        )
    alias = data.get(spec["alias_field"]) or spec["alias_fn"](team_id)
    plaintext = _fernet().decrypt(data[spec["ciphertext_field"]].encode()).decode()

    logger.info(
        "Rotating gateway %s for team %s (alias %s): deleting old key",
        spec["label"], team_id, alias,
    )
    r = requests.post(
        f"{_base_url()}/key/delete",
        json={"keys": [plaintext]},
        headers={"Authorization": f"Bearer {_master_key()}"},
        timeout=30,
    )
    r.raise_for_status()

    logger.info(
        "Old gateway %s deleted for team %s (alias %s); minting replacement",
        spec["label"], team_id, alias,
    )
    try:
        new_plaintext = _generate_key(
            alias, team_id, spec["models"], spec["max_budget"], spec["key_label"]
        )
    except Exception:
        ref.set({
            spec["status_field"]: "missing",
            spec["ciphertext_field"]: None,
        }, merge=True)
        logger.warning(
            "Gateway %s rotation failed for team %s (alias %s) after delete; "
            "key marked missing, other key kept",
            spec["label"], team_id, alias,
        )
        raise

    ref.set({
        spec["ciphertext_field"]: _fernet().encrypt(new_plaintext.encode()).decode(),
        spec["provisioned_field"]: _utcnow_iso(),
        spec["rotated_field"]: _utcnow_iso(),
    }, merge=True)
    logger.info("Gateway %s rotated for team %s (alias %s)", spec["label"], team_id, alias)
    data = ref.get().to_dict() or {}
    return _metadata(team_id, data)

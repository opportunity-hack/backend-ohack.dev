##########################################
# Health check endpoint
##########################################
# Deliberately zero side effects at import time: no Firestore, no Slack,
# no PropelAuth / common.auth import, and NOT common.utils.redis_cache
# (importing it pings Redis). Fly.io (and anything else probing liveness)
# must be able to hit this even if those dependencies are down.
#
# Besides liveness, the payload makes the environment observable: which
# commit is live, which process/machine answered (each gunicorn worker on
# each Fly machine holds its OWN in-process caches — see "Caching" in
# CLAUDE.md), and whether the optional integrations are configured.
# Everything is read from os.environ and reported as booleans — never values.

import os

from flask import Blueprint, jsonify

bp = Blueprint("api-health", __name__, url_prefix="/api/health")

# env var name -> payload key. Presence (non-empty) only, never the value.
CONFIGURED_FLAGS = {
    "GOOGLE_CAPTCHA_SECRET_KEY": "captcha_configured",
    "RESEND_API_KEY": "resend_configured",
    "STRIPE_SECRET_KEY": "stripe_configured",
    "BACKEND_CRON_TOKEN": "cron_token_configured",
    "STRIPE_HACKER_DEPOSIT_WEBHOOK_SECRET": "hacker_deposit_webhook_configured",
}


def _environment():
    """Best-effort environment name. Fly sets FLY_APP_NAME on every machine;
    ENVIRONMENT / FLASK_ENV are the local and test fallbacks."""
    app_name = os.environ.get("FLY_APP_NAME", "")
    if app_name.endswith("-staging"):
        return "staging"
    if app_name.endswith("-prod"):
        return "production"
    return os.environ.get("ENVIRONMENT") or os.environ.get("FLASK_ENV") or "local"


@bp.route("", strict_slashes=False)
def health():
    payload = {
        "status": "ok",
        "commit_sha": os.environ.get("COMMIT_SHA") or None,
        "environment": _environment(),
        "app": os.environ.get("FLY_APP_NAME") or None,
        "machine_id": os.environ.get("FLY_MACHINE_ID") or None,
        "region": os.environ.get("FLY_REGION") or None,
        "worker_pid": os.getpid(),
        # Mirrors common/utils/redis_cache.py: REDIS_URL present -> Redis is
        # attempted. A failed ping still falls back to local per process.
        "cache_backend": "redis" if os.environ.get("REDIS_URL") else "local",
    }
    for env_name, key in CONFIGURED_FLAGS.items():
        payload[key] = bool(os.environ.get(env_name))
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200

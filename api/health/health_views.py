##########################################
# Health check endpoint
##########################################
# Deliberately zero side effects at import time: no Firestore, no Slack,
# no PropelAuth / common.auth import. Fly.io (and anything else probing
# liveness) must be able to hit this even if those dependencies are down.

from flask import Blueprint, jsonify

bp = Blueprint("api-health", __name__, url_prefix="/api/health")


@bp.route("", strict_slashes=False)
def health():
    return jsonify({"status": "ok"}), 200

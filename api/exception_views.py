from flask import (
    Blueprint, request, jsonify
)
from werkzeug import exceptions
import math

from ratelimit.exception import RateLimitException

bp_name = 'exceptions'
bp = Blueprint(bp_name, __name__)


@bp.app_errorhandler(exceptions.InternalServerError)
def _handle_internal_server_error(ex):
    if request.path.startswith('/api/'):
        return jsonify(message=str(ex)), ex.code
    else:
        return ex


@bp.app_errorhandler(exceptions.NotFound)
def _handle_not_found_error(ex):
    if request.path.startswith('/api/'):
        return {"message": "Not Found"}, ex.code
    else:
        return ex


@bp.app_errorhandler(exceptions.RequestEntityTooLarge)
def _handle_payload_too_large(ex):
    # The app forces a JSON content-type on every response, so werkzeug's HTML
    # 413 page breaks frontend res.json() callers — answer in JSON on /api/.
    if request.path.startswith('/api/'):
        from flask import current_app
        return {"error": "payload_too_large", "max_bytes": current_app.config.get("MAX_CONTENT_LENGTH")}, 413
    return ex


@bp.app_errorhandler(RateLimitException)
def _handle_rate_limited(ex):
    # `ratelimit` @limits counters are per-process and all-clients-combined;
    # without this handler a tripped limit surfaced as an HTTP 500 for every
    # caller. Views that wrap their service call in a blanket `except
    # Exception` (volunteers / planning / store) still convert it to their own
    # 500 before it reaches here — follow-up.
    try:
        retry_after = math.ceil(float(getattr(ex, "period_remaining", 0) or 0))
    except (TypeError, ValueError):
        retry_after = 0
    retry_after = retry_after or 60
    if request.path.startswith('/api/'):
        return {"error": "rate_limited"}, 429, {"Retry-After": str(retry_after)}
    return "Too Many Requests", 429, {"Retry-After": str(retry_after)}

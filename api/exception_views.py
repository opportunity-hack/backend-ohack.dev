from flask import (
    Blueprint, request, jsonify
)
from werkzeug import exceptions

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

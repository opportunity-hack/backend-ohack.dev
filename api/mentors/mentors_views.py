"""
Per-team mentor support panel — Flask routes.

All mutating routes require an authenticated user; the underlying service
functions also verify the caller has an approved (isSelected=True) mentor
volunteer record for THIS event. The public read goes through the existing
GET /api/messages/team/<id> path (no new GET needed — mentor_* fields are
already part of the team doc).
"""
import logging
from flask import Blueprint, request

from common.auth import auth, auth_user
from api.mentors.mentors_service import (
    add_mentor_note,
    delete_mentor_note,
    raise_mentor_flag,
    take_over_mentor_flag,
    resolve_mentor_flag,
    toggle_mentor_coverage,
    set_mentor_rating,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bp_name = "api-mentors"
bp_url_prefix = "/api/team"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)


def _unauthorized():
    return {"error": "Unauthorized"}, 401


@bp.route("/<teamid>/mentor/coverage", methods=["POST"])
@auth.require_user
def toggle_mentor_coverage_api(teamid):
    """Body: { item: <slug>, done: bool, note?: str }"""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    item = body.get("item")
    done = body.get("done")
    note = body.get("note")
    if item is None or done is None:
        return {"error": "Both 'item' and 'done' are required"}, 400
    return toggle_mentor_coverage(auth_user.user_id, teamid, item, bool(done), note=note)


@bp.route("/<teamid>/mentor/notes", methods=["POST"])
@auth.require_user
def add_mentor_note_api(teamid):
    """Body: { body: str }"""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    text = body.get("body")
    return add_mentor_note(auth_user.user_id, teamid, text)


@bp.route("/<teamid>/mentor/notes/<note_id>", methods=["DELETE"])
@auth.require_user
def delete_mentor_note_api(teamid, note_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    return delete_mentor_note(auth_user.user_id, teamid, note_id)


@bp.route("/<teamid>/mentor/flags", methods=["POST"])
@auth.require_user
def raise_mentor_flag_api(teamid):
    """Body: { severity: 'needs_attention'|'blocked', body: str }"""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    severity = body.get("severity")
    text = body.get("body")
    if not severity or not text:
        return {"error": "Both 'severity' and 'body' are required"}, 400
    return raise_mentor_flag(auth_user.user_id, teamid, severity, text)


@bp.route("/<teamid>/mentor/flags/<flag_id>/take-over", methods=["POST"])
@auth.require_user
def take_over_mentor_flag_api(teamid, flag_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    return take_over_mentor_flag(auth_user.user_id, teamid, flag_id)


@bp.route("/<teamid>/mentor/flags/<flag_id>/resolve", methods=["POST"])
@auth.require_user
def resolve_mentor_flag_api(teamid, flag_id):
    """Body: { resolution_note: str }"""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    note = body.get("resolution_note")
    return resolve_mentor_flag(auth_user.user_id, teamid, flag_id, note)


@bp.route("/<teamid>/mentor/ratings", methods=["POST"])
@auth.require_user
def set_mentor_rating_api(teamid):
    """Body: { criterion: 'scope'|'documentation'|'polish'|'security',
              score: 'green'|'yellow'|'red', note?: str }"""
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    criterion = body.get("criterion")
    score = body.get("score")
    note = body.get("note")
    if not criterion or not score:
        return {"error": "Both 'criterion' and 'score' are required"}, 400
    return set_mentor_rating(auth_user.user_id, teamid, criterion, score, note=note)

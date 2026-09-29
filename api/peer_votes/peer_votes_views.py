"""
Flask routes for Hackers' Choice (the peer-vote award). Voter-facing routes
require login only (eligibility — isSelected hacker — is enforced in the
service); admin routes additionally check is_admin(auth_user). The public
summary route needs no auth at all.
"""
import logging
from flask import Blueprint, request

from common.auth import auth, auth_user
from services.hackathon_planning_service import is_admin
from api.peer_votes.peer_votes_service import (
    get_slate,
    submit_ballot,
    get_results,
    void_ballot,
    publish_results,
    get_public_summary,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bp_name = "api-peer-votes"
bp = Blueprint(bp_name, __name__, url_prefix="/api/hackathons")


def _unauthorized():
    return {"error": "Unauthorized"}, 401


def _forbidden():
    return {"error": "Forbidden"}, 403


@bp.route("/<event_id>/peer-vote/slate", methods=["GET"])
@auth.require_user
def get_slate_api(event_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    return get_slate(auth_user.user_id, event_id)


@bp.route("/<event_id>/peer-vote/ballot", methods=["POST"])
@auth.require_user
def submit_ballot_api(event_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    body = request.get_json() or {}
    return submit_ballot(auth_user.user_id, event_id, body.get("picks"))


@bp.route("/<event_id>/peer-vote/results", methods=["GET"])
@auth.require_user
def get_results_api(event_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    if not is_admin(auth_user):
        return _forbidden()
    return get_results(event_id)


@bp.route("/<event_id>/peer-vote/ballots/<propel_id>/void", methods=["POST"])
@auth.require_user
def void_ballot_api(event_id, propel_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    if not is_admin(auth_user):
        return _forbidden()
    return void_ballot(event_id, propel_id, auth_user.user_id)


@bp.route("/<event_id>/peer-vote/publish", methods=["POST"])
@auth.require_user
def publish_results_api(event_id):
    if not (auth_user and auth_user.user_id):
        return _unauthorized()
    if not is_admin(auth_user):
        return _forbidden()
    body = request.get_json() or {}
    return publish_results(event_id, auth_user.user_id, team_id=body.get("team_id"))


@bp.route("/<event_id>/peer-vote/summary", methods=["GET"])
def get_summary_api(event_id):
    return get_public_summary(event_id)

"""
Server-side participant rules for joining, leaving and creating hackathon teams.

Before Nov 2026 these rules lived ONLY in the frontend (the Join button was
hidden), so any logged-in user could join or leave any team — or create one —
by calling the API directly. This module is the server half.

TWIN: the frontend's `src/lib/teamMembershipApi.js`
(`teamJoinBlockedReason` / `teamLeaveBlockedReason`) and
`src/constants/teamStatus.js` (`JOINING_DISABLED_STATUSES`). The checks and
their ORDER are ported verbatim so the API never refuses something the page
offers (or allows something the page hides). Change both sides together.

Refusal codes (HTTP status):
  not_approved_hacker  403  caller has no approved (isSelected) hacker application
  event_ended          409  after 23:59:59 on end_date, event timezone
  team_join_disabled   409  constraints.team_join_enabled is False
  team_inactive        409  team.active is not True/"True"
  team_not_joinable    409  team.status in JOINING_DISABLED_STATUSES
  team_not_found       404

Admins (volunteer.admin) bypass every participant rule.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

JOIN = "join"
LEAVE = "leave"

NOT_APPROVED_HACKER = "not_approved_hacker"
EVENT_ENDED = "event_ended"
TEAM_JOIN_DISABLED = "team_join_disabled"
TEAM_INACTIVE = "team_inactive"
TEAM_NOT_JOINABLE = "team_not_joinable"
TEAM_NOT_FOUND = "team_not_found"

# Twin of src/constants/teamStatus.js JOINING_DISABLED_STATUSES.
JOINING_DISABLED_STATUSES = frozenset({
    "ONBOARDED",
    "SWAG_RECEIVED",
    "PROJECT_SUBMITTED",
    "COMPLETED_DEMO",
    "COMPLETED_HACKATHON",
    "FOUNDING_ENGINEERS",
    "COMPLETION_SUPPORT",
    "CATEGORY_WINNER",
    "DEPLOYED",
    "NONPROFIT_SIGNOFF",
    "INACTIVE",
})

# Human copy mirrors the frontend's blocked-reason sentences.
REFUSAL_MESSAGES = {
    NOT_APPROVED_HACKER: "Only approved hackers for this event can join or create a team. Your application may still be under review.",
    EVENT_ENDED: "This hackathon has ended, so its teams are closed.",
    TEAM_JOIN_DISABLED: "Team changes are paused right now. Ask an organizer if you need to move.",
    TEAM_INACTIVE: "This team isn't active right now.",
    TEAM_NOT_JOINABLE: "This team isn't taking new members anymore.",
    TEAM_NOT_FOUND: "Team not found",
}

REFUSAL_STATUS = {
    NOT_APPROVED_HACKER: 403,
    TEAM_NOT_FOUND: 404,
}


def _team_is_active(team: Dict[str, Any]) -> bool:
    # Frontend: team.active === "True" || team.active === true
    active = team.get("active")
    return active is True or active == "True"


def _event_ended(event: Optional[Dict[str, Any]], now=None) -> bool:
    if not event:
        return False
    from services.volunteers_service import mentor_checkin_window
    return mentor_checkin_window(event, now=now)["state"] == "closed"


def team_membership_rules(
    action: str,
    team: Dict[str, Any],
    event: Optional[Dict[str, Any]],
    is_approved_hacker: bool,
    now=None,
) -> Optional[str]:
    """Pure rule check. Returns None when allowed, else a refusal code.

    Join order = teamJoinBlockedReason (eventEnded → team_join_enabled →
    active → status), then the approved-hacker gate. Leave =
    teamLeaveBlockedReason (eventEnded → team_join_enabled); leaving never
    needs an approved application (members can be added by an organizer or
    listed at team creation).
    """
    constraints = (event or {}).get("constraints") or {}
    if _event_ended(event, now=now):
        return EVENT_ENDED
    if constraints.get("team_join_enabled") is False:
        return TEAM_JOIN_DISABLED
    if action == LEAVE:
        return None
    if not _team_is_active(team):
        return TEAM_INACTIVE
    if team.get("status") in JOINING_DISABLED_STATUSES:
        return TEAM_NOT_JOINABLE
    if not is_approved_hacker:
        return NOT_APPROVED_HACKER
    return None


def caller_is_approved_hacker(propel_user_id: str, event_id: Optional[str]) -> bool:
    """Same answer the frontend's fetchHackerStatus gets from
    GET /api/hacker/application/<event_id>: the caller's hacker doc (three-way
    identity resolver) has isSelected === True."""
    if not propel_user_id or not event_id:
        return False
    from services.volunteers_service import find_volunteer_by_caller_identity
    try:
        volunteer = find_volunteer_by_caller_identity(propel_user_id, event_id, "hacker")
    except Exception as e:  # never 500 a join on a lookup hiccup — refuse instead
        logger.warning("approved-hacker lookup failed for %s on %s: %s", propel_user_id, event_id, e)
        return False
    return bool(volunteer) and volunteer.get("isSelected") is True


def refusal(code: str):
    """Flask response tuple for a refusal code."""
    return {"error": code, "message": REFUSAL_MESSAGES.get(code, code)}, REFUSAL_STATUS.get(code, 409)


def _load_team(team_id: str) -> Optional[Dict[str, Any]]:
    from db.db import get_db
    snap = get_db().collection("teams").document(team_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def _load_event(event_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not event_id:
        return None
    from common.utils.firebase import get_hackathon_by_event_id
    return get_hackathon_by_event_id(event_id)


def check_team_membership_change(propel_user_id: str, team_id: Optional[str], action: str, admin: bool = False, now=None):
    """Gate for PATCH/DELETE /api/messages/team. Returns None (go ahead) or a
    Flask response tuple. A missing team id is left to the legacy service
    (it already answers that case)."""
    if admin or not team_id:
        return None
    team = _load_team(team_id)
    if team is None:
        return refusal(TEAM_NOT_FOUND)
    event_id = team.get("hackathon_event_id")
    event = _load_event(event_id)
    approved = caller_is_approved_hacker(propel_user_id, event_id) if action == JOIN else True
    code = team_membership_rules(action, team, event, approved, now=now)
    if code:
        logger.info("team %s refused for %s on team %s: %s", action, propel_user_id, team_id, code)
        return refusal(code)
    return None


def check_team_membership_request(user, body, action: str):
    """Entry point for the legacy PATCH/DELETE /api/messages/team views
    (messages_views is frozen — it only calls this)."""
    from services.hackathon_planning_service import is_admin
    team_id = (body or {}).get("teamId") if isinstance(body, dict) else None
    return check_team_membership_change(user.user_id, team_id, action, admin=is_admin(user))


def check_team_queue(propel_user_id: str, event_id: Optional[str], admin: bool = False):
    """Gate for POST /api/team/queue — creating a team needs an approved
    hacker application for that event. Checked BEFORE queue_team creates the
    Slack channel, so a refusal has no side effects."""
    if admin:
        return None
    if not caller_is_approved_hacker(propel_user_id, event_id):
        logger.info("team queue refused for %s on %s: %s", propel_user_id, event_id, NOT_APPROVED_HACKER)
        return refusal(NOT_APPROVED_HACKER)
    return None

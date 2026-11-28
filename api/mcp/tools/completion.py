"""Project completion monitoring tools for the admin MCP server.

Thin wrappers over the existing service layer — no business logic lives
here. Status reads use the full team doc from
``services.teams_service.get_team_admin`` (not ``get_team``, whose
``public_team_view`` strips fields); the event rollup reads
``api.teams.teams_service.get_teams_by_hackathon_id``. The only status
write is ``completion_mark_submitted``, which calls
``api.submissions.submissions_service.submit_project`` with ``admin=True``:
an admin-forced submission, recorded as ``late`` past the close, and
idempotent (an already submitted/late project answers
``already_submitted`` 200). There is no service function that sets an
arbitrary ``project_submission_status``, so no such tool exists.
``SUBMITTED_STATUSES`` / ``REQUIRED_SUBMIT_FIELDS`` are imported from
``api.submissions.submissions_service`` (lines 70/74), not duplicated.

GitHub activity parses org/repo out of the team's stored
``github_links`` URL (set on approve in
``api.teams.teams_service``: repo name ``{team_name}-{nonprofit_title}``
truncated to 100 chars, org = the hackathon's ``github_org``) and calls
``api.github.github_service.get_github_activity``.

No Slack stand-up tool: the backend has no channel-history reader.
``common/utils/slack.py`` only sends, audits, and looks up users/channels,
and no ``conversations.history`` read exists anywhere in ``api/``,
``services/``, or ``common/``; the only "standups" hits are a
hearts-service gamification award for standups completed. A stand-up
tool would need new service code (a ``conversations.history`` read plus a
stand-up definition), not a wrapper, so it is out of scope for this slice.
"""
from urllib.parse import urlsplit

from api.github.github_service import get_github_activity
from api.mcp.registry import VOLUNTEER_ADMIN, register_tool
from api.submissions.submissions_service import (
    REQUIRED_SUBMIT_FIELDS,
    SUBMITTED_STATUSES,
    submit_project,
)
from api.teams.teams_service import get_teams_by_hackathon_id
from services.teams_service import get_team_admin

_NOT_FOUND = {"error": "team_not_found", "message": "Team not found"}


def _submission_summary(team):
    # type: (dict) -> dict
    """The completion fields an admin needs, from one full team doc."""
    status = team.get("project_submission_status") or "draft"
    submitted = status in SUBMITTED_STATUSES
    missing = []
    if not submitted:
        missing = [
            field
            for field in REQUIRED_SUBMIT_FIELDS
            if not (team.get(field) or "").strip()
        ]
    return {
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "status": team.get("status"),
        "project_submission_status": status,
        "submitted": submitted,
        "project_submitted_at": team.get("project_submitted_at"),
        "missing_submit_fields": missing,
        "slack_channel": team.get("slack_channel"),
        "github_links": team.get("github_links") or [],
    }


def _parse_github_url(link):
    # type: (str) -> tuple
    """(org, repo) from a stored GitHub URL, or (None, None).

    Accepts the full ``https://github.com/<org>/<repo>`` URL approval
    stores, and a bare ``<org>/<repo>`` string. Anything else is not a
    repo the activity service can query.
    """
    if not isinstance(link, str) or not link:
        return None, None
    link = link.strip().rstrip("/")
    if "://" in link:
        parts = urlsplit(link)
        if parts.netloc not in ("github.com", "www.github.com"):
            return None, None
        segments = [seg for seg in parts.path.split("/") if seg]
    else:
        segments = [seg for seg in link.split("/") if seg]
    if len(segments) != 2:
        return None, None
    repo = segments[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not repo:
        return None, None
    return segments[0], repo


@register_tool(
    "completion_team_status",
    "Project submission status for one team: draft/submitted/late, "
    "submitted-at timestamp, which required write-up fields are still "
    "missing, Slack channel, and linked GitHub repo.",
    {
        "type": "object",
        "properties": {"team_id": {"type": "string"}},
        "required": ["team_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def completion_team_status(arguments, admin):
    # type: (dict, dict) -> tuple
    team = get_team_admin(arguments["team_id"])
    if team is None:
        return _NOT_FOUND, 404
    return {"success": True, **_submission_summary(team)}, 200


@register_tool(
    "completion_event_status",
    "Project submission rollup for one hackathon: total teams, how many "
    "submitted (submitted or late) vs still draft, and the per-team "
    "status rows. hackathon_id is the hackathon's database document id "
    "(the id GET /api/team/<hackathon_id> takes), NOT the event_id slug "
    "such as 2026_fall; an unknown id reads as zero teams.",
    {
        "type": "object",
        "properties": {"hackathon_id": {"type": "string"}},
        "required": ["hackathon_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def completion_event_status(arguments, admin):
    # type: (dict, dict) -> tuple
    result = get_teams_by_hackathon_id(arguments["hackathon_id"])
    teams = (result or {}).get("teams") or []
    rows = [_submission_summary(team) for team in teams]
    submitted = sum(1 for row in rows if row["submitted"])
    return {
        "success": True,
        "hackathon_id": arguments["hackathon_id"],
        "total_teams": len(rows),
        "submitted": submitted,
        "draft": len(rows) - submitted,
        "teams": rows,
    }, 200


@register_tool(
    "completion_mark_submitted",
    "Admin-forced project submission for one team: marks the project "
    "submitted, or late when past the submission close. Idempotent — "
    "an already submitted project answers already_submitted. Fails "
    "400 incomplete when the required write-up fields are missing.",
    {
        "type": "object",
        "properties": {
            "team_id": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["team_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
    requires_confirm=True,
)
def completion_mark_submitted(arguments, admin):
    # type: (dict, dict) -> tuple
    # Caller identity comes from the verified token only; admin=True is
    # what makes submit_project skip the membership and deadline gates.
    return submit_project(admin.get("propel_id"), arguments["team_id"], admin=True)


@register_tool(
    "completion_github_activity",
    "GitHub activity for a team's linked repo: commits, contributors, "
    "and open PRs. The org/repo come from the GitHub URL stored on the "
    "team at approval time.",
    {
        "type": "object",
        "properties": {"team_id": {"type": "string"}},
        "required": ["team_id"],
        "additionalProperties": False,
    },
    (VOLUNTEER_ADMIN,),
)
def completion_github_activity(arguments, admin):
    # type: (dict, dict) -> tuple
    team = get_team_admin(arguments["team_id"])
    if team is None:
        return _NOT_FOUND, 404
    links = team.get("github_links") or []
    # Approval stores [{"link", "name"}]; an admin edit can leave a bare
    # URL string, which must read as a link rather than crash.
    first = links[0] if isinstance(links, list) and links else None
    link = first.get("link") if isinstance(first, dict) else first
    org, repo = _parse_github_url(link)
    if org is None or repo is None:
        return {
            "error": "no_github_repo",
            "message": "Team has no linked GitHub repo",
        }, 404
    return get_github_activity(org, repo)

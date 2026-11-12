import copy
import uuid
import logging
from datetime import datetime, timedelta

from cachetools import TTLCache
from db.db import get_db, get_user_doc_reference
from api.messages.messages_service import get_problem_statement_from_id_old
from services.teams_service import get_teams_list, get_team, public_team_view
from services.nonprofits_service import get_single_npo
from common.utils.firestore_helpers import clear_all_caches as clear_cache, register_cache
from services.users_service import (
    get_propel_user_details_by_id,
    save_user,
    get_user_from_slack_id
)
from common.utils.github import create_github_repo, validate_github_username
from common.utils.slack import async_send_slack, create_slack_channel, invite_user_to_channel, send_slack, send_slack_audit
from common.utils.firebase import get_hackathon_by_event_id
from common.utils.oauth_providers import extract_slack_user_id, is_oauth_user_id, normalize_slack_user_id
from common.utils.validators import sanitize_markdown, normalize_github_org

from common.utils.slack import add_bot_to_channel

logger = logging.getLogger("myapp")

# Admin override of a team's project_* write-up fields via PATCH
# /api/team/edit (MEDIUM finding #3 — documented in api/submissions/README.md
# "Ownership" as always having been the intended admin path, but edit_team's
# field_mappings never actually carried these fields). Limits/catalog are
# duplicated from api.submissions.submissions_service's PROJECT_LIMITS /
# SUBMITTED_STATUSES (not imported — teams_service must never import
# api.submissions, a one-directional dependency documented in that module's
# docstring).
PROJECT_TAGLINE_MAX_LEN = 140
PROJECT_STORY_MAX_LEN = 20000
PROJECT_SUBMISSION_STATUSES = {"draft", "submitted", "late"}

# Slack user IDs for OHack admins who are auto-invited to every team channel
# and CCed on completion broadcasts. Single source of truth for both call sites.
TEAM_COMPLETION_SLACK_ADMINS = [
    "UCQKX6LPR",
    "U035023T81Z",
    "UC31XTRT5",
    "UC2JW3T3K",
    "UPD90QV17",
    "UEP2U69AA",
]

# Canonical 8-item Definition of Done. Must stay in lockstep with the frontend
# COMPLETION_ITEMS array in src/components/Teams/TeamCompletionChecklist.js.
COMPLETION_ITEMS = [
    {"slug": "deployed", "label": "Deployed", "blurb": "Code is live in production (AWS, fly.io, GCP)."},
    {"slug": "nonprofit_signoff", "label": "Nonprofit Signoff", "blurb": "Your nonprofit partner agrees the software meets their needs."},
    {"slug": "login_details", "label": "Login Details for Testing", "blurb": "Test credentials shared securely (changeable later)."},
    {"slug": "code_updated", "label": "Code Updated", "blurb": "All code, README, and docs in the designated GitHub repo."},
    {"slug": "tasks_closed", "label": "Tasks Closed", "blurb": "GitHub issues/tasks closed or addressed."},
    {"slug": "sensitive_info_security", "label": "Sensitive Info Secured", "blurb": "No secrets in the repo; shared securely elsewhere."},
    {"slug": "documentation", "label": "Documentation", "blurb": "How to use, deploy, update, and configure."},
    {"slug": "open_source", "label": "Open-Sourced (MIT)", "blurb": "Repo is public under MIT."},
]
COMPLETION_ITEM_SLUGS = {item["slug"]: item for item in COMPLETION_ITEMS}

def add_team_member(team_id, user_id):
    """
    Admin function to add a member to a team
    - Add user to the team in the database
    - Notify the user via Slack about the addition
    """
    send_slack_audit(action="add_team_member", message="Adding", payload={"team_id": team_id, "user_id": user_id})
    
    db = get_db()
    logger.debug("Adding Team Member")
    
    # Get the team document
    team_doc = db.collection('teams').document(team_id)
    team_data = team_doc.get().to_dict()
    slack_channel = team_data.get("slack_channel", None)
    
    if not team_data:
        logger.error("Team not found")
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Get the user document
    user_doc = db.collection('users').document(user_id)
    user_data = user_doc.get().to_dict()
    slack_user_id = user_data.get("user_id")
    
    if slack_channel is not None:
        invite_user_to_channel(slack_user_id, slack_channel)
    
    if not user_data:
        logger.error("User not found")
        return {
            "message": "Error: User not found",
            "success": False
        }
    
    # Check if the user is already in the team
    if user_doc in team_data.get("users", []):
        logger.error("User is already a member of the team")
        return {
            "message": "Error: User is already a member of the team",
            "success": False
        }

    # Add the user to the team
    users = team_data.get("users", [])
    users.append(user_doc)
    
    # Update the team document
    team_doc.set({
        "users": users
    }, merge=True)
    
    # Notify the user via Slack
    slack_message = f''':rocket: You have been added to the team *{team_data["name"]}*! :tada:'''
    # Get the user's slack ID if user_data["user_id"] is not None and split with - getting the last
    if user_data["user_id"] is not None:
        slack_user_id = user_data["user_id"].split("-")[-1]
        send_slack(slack_message, slack_user_id)
    else:
        # If user_id is None, send to the team channel
        slack_channel = team_data["slack_channel"]
        send_slack(slack_message, slack_channel)    

    logger.info("User %s added to team %s", user_data["name"], team_data["name"])

    return {
        "message": f"User {user_data['name']} added to team {team_data['name']}",
        "success": True,
        "team_id": team_id
    }

def remove_team_member(team_id, user_id):
    """
    Admin function to remove a member from a team
    - Remove user from the team in the database
    - Notify the user via Slack about the removal
    """
    send_slack_audit(action="remove_team_member", message="Removing", payload={"team_id": team_id, "user_id": user_id})
    
    db = get_db()
    logger.debug(f"Removing Team Member {user_id} from Team {team_id}")
    
    # Get the team document
    team_doc = db.collection('teams').document(team_id)
    team_data = team_doc.get().to_dict()
    
    if not team_data:
        logger.error("Team not found")
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Get the user document
    user_doc = db.collection('users').document(user_id)
    user_data = user_doc.get().to_dict()
    
    if not user_data:
        logger.error("User not found")
        return {
            "message": "Error: User not found",
            "success": False
        }
    
    # Check if the user is in the team
    if user_doc not in team_data.get("users", []):
        logger.error("User is not a member of the team")
        return {
            "message": "Error: User is not a member of the team",
            "success": False
        }

    # Remove the user from the team
    users = team_data.get("users", [])
    users.remove(user_doc)
    
    # Update the team document
    team_doc.set({
        "users": users
    }, merge=True)
    
    # Notify the user via Slack
    slack_message = f''':rocket: You have been removed from the team *{team_data["name"]}*! :tada:'''
    # Get the user's slack ID if user_data["user_id"] is not None and split with - getting the last
    if user_data["user_id"] is not None:
        slack_user_id = user_data["user_id"].split("-")[-1]
        send_slack(slack_message, slack_user_id)
    else:
        # If user_id is None, send to the team channel
        slack_channel = team_data["slack_channel"]
        send_slack(slack_message, slack_channel)    

    logger.info("User %s removed from team %s", user_data["name"], team_data["name"])

    return {
        "message": f"User {user_data['name']} removed from team {team_data['name']}",
        "success": True,
        "team_id": team_id
    }

def remove_team(team_id):
    """
    Admin function to remove a team
    - Delete the team from the database
    - Notify team members via Slack about the deletion
    """
    send_slack_audit(action="remove_team", message="Removing", payload={"team_id": team_id})
    
    db = get_db()
    logger.debug("Removing Team")
    
    # Get the team document
    team_doc = db.collection('teams').document(team_id)
    team_data = team_doc.get().to_dict()
    
    if not team_data:
        logger.error("Team not found")
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Remove team from all users
    for user_ref in team_data.get("users", []):
        user_data = user_ref.get().to_dict()
        if user_data:
            # Remove the team from the user's teams list
            user_teams = user_data.get("teams", [])
            if team_doc in user_teams:
                user_teams.remove(team_doc)
                user_ref.set({
                    "teams": user_teams
                }, merge=True)
    logger.info("Removed team from all users")

    # Remove team from all hackathon events
    hackathon_event_id = team_data.get("hackathon_event_id")
    if hackathon_event_id:
        hackathon_db_id = get_hackathon_by_event_id(hackathon_event_id)["id"]
        event_collection = db.collection("hackathons").document(hackathon_db_id)
        event_collection_dict = event_collection.get().to_dict() or {}

        # Remove the team from the hackathon event (tolerate docs with no
        # `teams` key — same KeyError class as the queue_team linking bug)
        new_teams = []
        for t in event_collection_dict.get("teams") or []:
            if t != team_doc:
                new_teams.append(t)
        
        event_collection.set({
            "teams": new_teams
        }, merge=True)

    # Delete the team document
    team_doc.delete()
    logger.info("Deleted team document")

    logger.info("Team %s removed", team_data["name"])

    return {
        "message": f"Team {team_data['name']} removed",
        "success": True,
        "team_id": team_id
    }

def edit_team(json):
    """
    Admin function to edit a team
    - Update team details in the database
    - Notify team members via Slack about the changes
    """
    send_slack_audit(action="edit_team", message="Editing", payload=json)
    
    db = get_db()
    logger.debug(f"Team Edit with JSON: {json}")
    
    team_id = json["id"]
    
    # Get the team document
    team_doc = db.collection('teams').document(team_id)
    team_data = team_doc.get().to_dict()
    
    if not team_data:
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Update the team document with new data
    update_data = {}
    
    # Map all possible fields from the JSON to the database fields
    field_mappings = {
        "name": "name",
        "slack_channel": "slackChannel",
        "github_username": "github_username",
        "comments": "comments",
        "nonprofit_rankings": "nonprofitRankings",
        "selected_nonprofit_id": "selected_nonprofit_id",
        "status": "status", 
        "team_number": "team_number",
        "active": "active",
        "hackathon_event_id": "hackathon_event_id",
        "location": "location",
        "admin_notes": "admin_notes",
        "devpost_link": "devpost_link",
        "demo_video_url": "demo_video_url",
        "project_tagline": "project_tagline",
        "project_story": "project_story",
        "project_built_with": "project_built_with",
        "project_links": "project_links",
        "project_thumbnail_url": "project_thumbnail_url",
        "project_images": "project_images",
        "project_submission_status": "project_submission_status",
    }

    # Normalize demo_video_url: trim, cap at 500 chars, empty string => clear
    if "demo_video_url" in json:
        raw = json.get("demo_video_url")
        if isinstance(raw, str):
            trimmed = raw.strip()[:500]
            json["demo_video_url"] = trimmed if trimmed else None

    # project_submission_status must be one of the catalog values — a bad
    # value 400s rather than writing a status the dashboard/gallery/funnel
    # don't know how to render.
    if "project_submission_status" in json:
        status_val = json.get("project_submission_status")
        if status_val not in PROJECT_SUBMISSION_STATUSES:
            return {
                "message": f"Error: project_submission_status must be one of {sorted(PROJECT_SUBMISSION_STATUSES)}",
                "success": False,
            }, 400
        if status_val != team_data.get("project_submission_status"):
            update_data["project_updated_at"] = datetime.now().isoformat()

    # project_tagline / project_story get the same defence-in-depth
    # sanitize_markdown treatment as the self-serve save_project path.
    if "project_tagline" in json:
        val = json.get("project_tagline")
        json["project_tagline"] = sanitize_markdown(val, PROJECT_TAGLINE_MAX_LEN) if isinstance(val, str) and val else None

    if "project_story" in json:
        val = json.get("project_story")
        json["project_story"] = sanitize_markdown(val, PROJECT_STORY_MAX_LEN) if isinstance(val, str) and val else None

    # Move a team to a different project: problem_statement_id must be one of
    # the selected nonprofit's projects (the nonprofit in this same request
    # wins over the stored one). Writes problem_statements: [ref] — the field
    # the repo-link / README-seed helpers and /me's `project` read. A
    # falsy value clears it.
    if "problem_statement_id" in json:
        ps_id = _ref_id(json.get("problem_statement_id"))
        if ps_id is None:
            update_data["problem_statements"] = []
        else:
            npo_id = json.get("selected_nonprofit_id") or team_data.get("selected_nonprofit_id")
            project, refusal = _resolve_nonprofit_project(db, npo_id, ps_id)
            if refusal:
                return refusal
            update_data["problem_statements"] = [db.collection("problem_statements").document(project["id"])]

    # If this is the first time setting devpost_link, set devpost_link_submitted date
    if "devpost_link" in json and "devpost_link" not in team_data:
        update_data["devpost_link_submitted"] = datetime.now().isoformat()

    # If this is the first time setting demo_video_url, stamp demo_video_url_submitted
    if json.get("demo_video_url") and not team_data.get("demo_video_url"):
        update_data["demo_video_url_submitted"] = datetime.now().isoformat()

    for db_field, json_field in field_mappings.items():
        if json_field in json:
            update_data[db_field] = json[json_field]
    
    # Add a last_updated field to track changes
    update_data["last_updated"] = datetime.now().isoformat()

    # Handle any other fields not in the mapping
    if "created" in json:
        update_data["created"] = json["created"]
    
    if len(update_data) > 0:
        team_doc.set(update_data, merge=True)
        clear_cache()  # Clear the cache to ensure updated data is reflected
        
        return {
            "message": "Team updated successfully",
            "success": True,
            "team_id": team_id
        }
    else:
        return {
            "message": "No changes to update",
            "success": True,
            "team_id": team_id
        }


def _append_team_to_hackathon(db, hackathon_db_id, team_ref):
    """Link a team DocumentReference into hackathons/{id}.teams[].

    Hackathon docs created through the admin UI may have no ``teams`` key at
    all (not even an empty list); indexing it directly raised KeyError after
    the team doc was already inserted, leaving the team orphaned from its
    event (Sep 2026, test event fall-2026). Idempotent: an already-linked
    team is not appended twice.
    """
    event_ref = db.collection("hackathons").document(hackathon_db_id)
    event_dict = event_ref.get().to_dict() or {}
    teams = list(event_dict.get("teams") or [])
    existing_ids = {getattr(t, "id", None) for t in teams}
    if getattr(team_ref, "id", None) not in existing_ids:
        teams.append(team_ref)
        event_ref.set({"teams": teams}, merge=True)
    return teams


# ---------------------------------------------------------------------------
# Project options + ranking validation (team creation, Nov 2026)
#
# A nonprofit can have several projects (problem statements), so teams rank
# individual projects, not nonprofits. get_team_project_options is the ONE
# rule for "which projects can a team pick in this event": the
# GET /api/team/<event_id>/project-options route returns it and queue_team
# validates rankings against it, so the page and the API can't disagree.
# ---------------------------------------------------------------------------

PROJECT_DESCRIPTION_PREVIEW_CHARS = 400
# Live projects don't need a hackathon team. Mirrors the frontend's
# "production" status in src/lib/projectStatus.js.
UNSELECTABLE_PROJECT_STATUSES = {"production"}
MIN_RANKED_PROJECTS = 2
MAX_RANKED_PROJECTS = 5

_PROJECT_OPTIONS_CACHE = TTLCache(maxsize=64, ttl=60)
register_cache(_PROJECT_OPTIONS_CACHE)


def _ref_id(value):
    """Id of a DocumentReference or a bare id string; None otherwise."""
    if isinstance(value, str):
        return value.strip() or None
    return getattr(value, "id", None)


def _as_ref(db, collection, value):
    """DocumentReference for a ref or a bare id string (None for junk)."""
    if isinstance(value, str):
        return db.collection(collection).document(value) if value.strip() else None
    return value if getattr(value, "id", None) else None


def _snapshot_dict(snap):
    if snap is None or not getattr(snap, "exists", False):
        return None
    return snap.to_dict() or {}


def _preview(text, limit=PROJECT_DESCRIPTION_PREVIEW_CHARS):
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _skills_list(value):
    if isinstance(value, list):
        return [s for s in value if isinstance(s, str) and s.strip()]
    if isinstance(value, str) and value.strip():
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def _build_project_options(db, hackathon):
    visible = hackathon.get("visible_problem_statements")
    # None/missing = every project is visible (same reading as the frontend's
    # Hackathon/NonprofitList.js); an explicit list (even empty) is the allowlist.
    visible_ids = None if visible is None else {_ref_id(v) for v in visible if _ref_id(v)}

    npo_refs = []
    seen_npos = set()
    for value in hackathon.get("nonprofits") or []:
        ref = _as_ref(db, "nonprofits", value)
        if ref is not None and ref.id not in seen_npos:
            seen_npos.add(ref.id)
            npo_refs.append(ref)
    if not npo_refs:
        return []

    npo_by_id = {}
    for snap in db.get_all(npo_refs):
        data = _snapshot_dict(snap)
        if data is not None:
            npo_by_id[snap.id] = data

    # One get_all over every nonprofit's problem statements.
    ps_refs = []
    seen_ps = set()
    for ref in npo_refs:
        for value in (npo_by_id.get(ref.id) or {}).get("problem_statements") or []:
            ps_ref = _as_ref(db, "problem_statements", value)
            if ps_ref is None or ps_ref.id in seen_ps:
                continue
            if visible_ids is not None and ps_ref.id not in visible_ids:
                continue
            seen_ps.add(ps_ref.id)
            ps_refs.append(ps_ref)

    ps_by_id = {}
    if ps_refs:
        for snap in db.get_all(ps_refs):
            data = _snapshot_dict(snap)
            if data is not None:
                ps_by_id[snap.id] = data

    nonprofits = []
    for ref in npo_refs:  # keep the event's nonprofit order
        npo = npo_by_id.get(ref.id)
        if npo is None:
            continue
        projects = []
        for value in npo.get("problem_statements") or []:
            ps_id = _ref_id(value)
            ps = ps_by_id.get(ps_id)
            if ps is None:
                continue  # hidden, deleted, or listed twice
            if (ps.get("status") or "").strip().lower() in UNSELECTABLE_PROJECT_STATUSES:
                continue
            if any(p["id"] == ps_id for p in projects):
                continue
            projects.append({
                "id": ps_id,
                "title": ps.get("title") or "Untitled project",
                "description": _preview(ps.get("description")),
                "skills": _skills_list(ps.get("skills")),
                "status": ps.get("status"),
            })
        if not projects:
            continue  # nothing a team can pick
        nonprofits.append({
            "id": ref.id,
            "name": npo.get("name") or "",
            "image": npo.get("image"),
            "website": npo.get("website"),
            "projects": projects,
        })
    return nonprofits


def get_team_project_options(event_id):
    """Projects a team can rank at creation for `event_id`, grouped by
    nonprofit: {"event_id", "nonprofits": [{id, name, image, website,
    projects: [{id, title, description, skills, status}]}]}.

    Projects come from the event's nonprofits, filtered by the event's
    `visible_problem_statements` allowlist (missing/None = all) with
    production projects dropped; nonprofits left with no project are
    dropped. Returns None when the event doesn't exist. Cached 60s per event
    (static class — cleared by every admin write through clear_cache)."""
    if not event_id:
        return None
    cached = _PROJECT_OPTIONS_CACHE.get(event_id)
    if cached is not None:
        return copy.deepcopy(cached)

    hackathon = get_hackathon_by_event_id(event_id)
    if not hackathon:
        return None
    result = {
        "event_id": event_id,
        "nonprofits": _build_project_options(get_db(), hackathon),
    }
    _PROJECT_OPTIONS_CACHE[event_id] = copy.deepcopy(result)
    return result


def _project_index(options):
    """{problem_statement_id: {"title": str, "nonprofits": {npo_id: npo_name}}}"""
    index = {}
    for npo in (options or {}).get("nonprofits") or []:
        for project in npo.get("projects") or []:
            entry = index.setdefault(project["id"], {"title": project.get("title") or "", "nonprofits": {}})
            entry["nonprofits"][npo["id"]] = npo.get("name") or ""
    return index


def ranking_limits(total_available):
    """(min, max) projects a team must rank given how many are selectable."""
    total = max(0, int(total_available or 0))
    return min(MIN_RANKED_PROJECTS, total), min(MAX_RANKED_PROJECTS, total)


class RankingError(ValueError):
    """A refusal from validate_project_rankings; str(e) is the user copy."""


def _rank_sort_key(item):
    index, entry = item
    rank = entry.get("rank")
    try:
        return (0, float(rank), index)
    except (TypeError, ValueError):
        return (1, 0.0, index)


def validate_project_rankings(rankings, options):
    """Normalize a team's ranked projects against `options`
    (get_team_project_options). Returns [{nonprofit_id,
    problem_statement_id, rank}] with ranks reassigned 1..n, in the team's
    order, duplicates dropped. Raises RankingError with user-facing copy.

    Each entry must name a project that is selectable in this event AND
    belongs to that nonprofit. A team ranks MIN..MAX projects, or every
    available project when the event has fewer than MIN.

    Legacy shape (no entry carries `problem_statement_id`): the frontend
    before the project-ranking release sent [{nonprofit_id, rank}]. Kept
    valid so team creation works between the backend and frontend deploys —
    each nonprofit must still be one of this event's nonprofits; no count
    rule. Returns [{nonprofit_id, rank}]."""
    if rankings is None:
        rankings = []
    if not isinstance(rankings, list) or not all(isinstance(r, dict) for r in rankings):
        raise RankingError("Your project ranking didn't come through. Refresh the page and choose your projects again.")

    ordered = [entry for _, entry in sorted(enumerate(rankings), key=_rank_sort_key)]
    has_project = [bool(_ref_id(e.get("problem_statement_id"))) for e in ordered]

    if not any(has_project):
        event_npos = {npo["id"] for npo in (options or {}).get("nonprofits") or []}
        normalized, seen = [], set()
        for entry in ordered:
            npo_id = _ref_id(entry.get("nonprofit_id"))
            if not npo_id:
                raise RankingError("Each ranking needs a nonprofit.")
            if npo_id in seen:
                continue
            if npo_id not in event_npos:
                raise RankingError("One of the nonprofits you ranked isn't part of this event. Refresh the page and choose again.")
            seen.add(npo_id)
            normalized.append({"nonprofit_id": npo_id, "rank": len(normalized) + 1})
        return normalized

    if not all(has_project):
        raise RankingError("Each ranking needs both a nonprofit and one of its projects.")

    index = _project_index(options)
    normalized, seen = [], set()
    for entry in ordered:
        ps_id = _ref_id(entry.get("problem_statement_id"))
        npo_id = _ref_id(entry.get("nonprofit_id"))
        if ps_id in seen:
            continue  # same project listed twice: keep the higher rank
        project = index.get(ps_id)
        if project is None:
            raise RankingError(
                "One of the projects you ranked isn't open for teams in this event anymore. "
                "Refresh the list and choose again."
            )
        if npo_id not in project["nonprofits"]:
            raise RankingError(f"“{project['title']}” doesn't belong to the nonprofit it was ranked under. Choose it again from the list.")
        seen.add(ps_id)
        normalized.append({
            "nonprofit_id": npo_id,
            "problem_statement_id": ps_id,
            "rank": len(normalized) + 1,
        })

    low, high = ranking_limits(len(index))
    if len(normalized) < low:
        raise RankingError(f"Rank at least {low} projects (you ranked {len(normalized)}).")
    if len(normalized) > high:
        raise RankingError(f"Rank at most {high} projects (you ranked {len(normalized)}).")
    return normalized


def _ranking_lines(rankings, options):
    """Slack lines "#1 {project} — {nonprofit}" (legacy: "#1 {nonprofit}")."""
    names = {npo["id"]: npo.get("name") or npo["id"] for npo in (options or {}).get("nonprofits") or []}
    titles = {}
    for npo in (options or {}).get("nonprofits") or []:
        for project in npo.get("projects") or []:
            titles[project["id"]] = project.get("title") or project["id"]
    lines = []
    for entry in rankings or []:
        npo_name = names.get(entry.get("nonprofit_id"), entry.get("nonprofit_id") or "")
        ps_id = entry.get("problem_statement_id")
        if ps_id:
            lines.append(f"#{entry['rank']} {titles.get(ps_id, ps_id)} — {npo_name}")
        else:
            lines.append(f"#{entry['rank']} {npo_name}")
    return lines


def _refusal(code, message, status=400):
    """Real 4xx refusal body (never the legacy 200 {message})."""
    return {"error": code, "message": message, "success": False}, status


def queue_team(propel_user_id, json):
    """
    Create a team and queue it for nonprofit pairing
    - Save to teams collection with status IN_REVIEW
    - Set active status to False
    - Send Slack notification that team is in queue
    - Do not create GitHub repo or other resources yet

    Everything that can refuse runs BEFORE the Slack channel is created, so a
    refusal leaves no orphaned channel: required fields → event exists →
    project rankings → GitHub username → creator's user doc. Refusals are
    400 {error, message}: missing_fields | event_not_found |
    invalid_rankings | invalid_github_username | user_not_found.
    """
    send_slack_audit(action="queue_team", message="Queueing", payload=json)

    json = json if isinstance(json, dict) else {}
    required = {
        "name": "a team name",
        "slackChannel": "a Slack channel name",
        "eventId": "the event",
        "githubUsername": "your GitHub username",
    }
    missing = [label for key, label in required.items()
               if not (isinstance(json.get(key), str) and json.get(key).strip())]
    if missing:
        return _refusal("missing_fields", "Please add " + ", ".join(missing) + ".")

    team_name = json["name"].strip()
    slack_channel = json["slackChannel"].strip()
    hackathon_event_id = json["eventId"].strip()
    github_username = json["githubUsername"].strip()

    hackathon = get_hackathon_by_event_id(hackathon_event_id)
    if not hackathon:
        return _refusal("event_not_found", "We couldn't find this event. Refresh the page and try again.")

    # Ranked projects: [{nonprofit_id, problem_statement_id, rank}], validated
    # against the same options the create-team page shows.
    project_options = get_team_project_options(hackathon_event_id) or {"nonprofits": []}
    try:
        nonprofit_rankings = validate_project_rankings(json.get("nonprofitRankings"), project_options)
    except RankingError as e:
        return _refusal("invalid_rankings", str(e))

    if not validate_github_username(github_username):
        return _refusal(
            "invalid_github_username",
            "We couldn't find that GitHub username. Use your username without the @ symbol, not your email.",
        )

    _, user_id, _, _, name, _ = get_propel_user_details_by_id(propel_user_id)
    slack_user_id = user_id

    # Extract the raw Slack user ID (handles both OAuth formats and Google users gracefully)
    root_slack_user_id = extract_slack_user_id(slack_user_id)
    user = get_user_doc_reference(root_slack_user_id)
    if user is None:
        return _refusal(
            "user_not_found",
            "We couldn't find your profile. Open your profile page once (it creates it), then try again.",
        )
    users_list = [user]

    db = get_db()
    logger.debug("Team Queue")
    logger.debug(json)

    doc_id = uuid.uuid1().hex  # Generate a new team id
    comments = json.get("comments", "")
    teamMembers = json.get("teamMembers", []) # In addition to the user
    url_hackathon = f"https://ohack.dev/hack/{hackathon_event_id}"
    url_manage_team = f"{url_hackathon}/manageteam"
    ranking_lines = _ranking_lines(nonprofit_rankings, project_options)
    ranking_block = "\n".join(ranking_lines) if ranking_lines else "(none yet)"

    # Create the slack channel
    logger.info("Creating slack channel %s", slack_channel)
    channel_id = create_slack_channel(slack_channel)

    logger.info("Adding bot to channel: %s with channel ID: %s", slack_channel, channel_id)
    add_bot_to_channel(channel_id)    

    # Sleep for a few seconds to ensure the channel is created before inviting users
    import time
    time.sleep(2)

    # Send a message to the team about their request being queued
    slack_message = f'''
:rocket: Team *{team_name}* has been created! :tada:

*Channel:* #{slack_channel}
*Created by:* <@{root_slack_user_id}> (add your other team members here)

*Your ranked projects:*
{ranking_block}

- Thank you for your project preferences!
- Our team will review your request and pair you with a project soon.
- We'll send a message to this Slack channel when the pairing process is complete.


:question: *Need help?* 
Join <#C01E5CGDQ74> for questions and updates.

- Read more about the hackathon <{url_hackathon}|here>
- Manage your team <{url_manage_team}|here>

Let's make a difference! :muscle: :heart:
'''
    send_slack(slack_message, slack_channel)
    send_slack(slack_message, "log-team-creation")


    logger.info("Inviting user %s to slack channel %s", slack_user_id, slack_channel)
    invite_user_to_channel(slack_user_id, slack_channel)

    # Look at teamMember.id to get the slack user id
    
    send_slack_audit(action="queue_team", message=f"Queueing {len(teamMembers)} team members", payload=json)
    for member in teamMembers:
        # teamMembers is a mixed list: Slack-user objects ({id, name, real_name})
        # picked from the autocomplete, plus any free-text names the user typed
        # (the picker is freeSolo). Only objects with a Slack id can be invited to
        # the channel and linked to the team. Guard on dict explicitly — `"id" in
        # member` against a raw string is a substring test (e.g. "Sidney" matches),
        # and member["id"] would then raise TypeError on that string.
        member_slack_id = member.get("id") if isinstance(member, dict) else None
        if member_slack_id and member_slack_id != root_slack_user_id:
            logger.info("Inviting user %s to slack channel %s", member_slack_id, slack_channel)
            invite_user_to_channel(member_slack_id, slack_channel)
            # Lookup the user in the database by user_id which is their slack user id
            full_user_id_with_slack_prefix = normalize_slack_user_id(member_slack_id)
            user_db_check = get_user_from_slack_id(full_user_id_with_slack_prefix)
            if not user_db_check:
                new_user = save_user(
                    user_id=full_user_id_with_slack_prefix,
                    email="",
                    last_login="",
                    profile_image="",
                    name=member.get("real_name", ""),
                    nickname=member.get("name", "")
                )
                users_list.append(get_user_doc_reference(new_user.user_id))
            else:
                in_db_user = get_user_doc_reference(user_db_check.user_id)
                users_list.append(in_db_user)
                logger.info("User %s already exists in the database", user_db_check)


        else:
            logger.info("Skipping non-Slack team member (free-text or self): %s", member)
        
    
    # Add Slack admins
    for admin in TEAM_COMPLETION_SLACK_ADMINS:
        logger.info("Inviting admin %s to slack channel %s", admin, slack_channel)
        invite_user_to_channel(admin, slack_channel)


    my_date = datetime.now()
    collection = db.collection('teams')
    
    # Save the team with status IN_REVIEW and active=False
    insert_res = collection.document(doc_id).set({
        "team_number": -1,
        "users": users_list,
        "name": team_name,
        "slack_channel": slack_channel,
        "created": my_date.isoformat(),
        "active": False,
        "status": "IN_REVIEW",
        "hackathon_event_id": hackathon_event_id,
        "github_username": github_username,
        "nonprofit_rankings": nonprofit_rankings,
        "comments": comments,
    })

    send_slack_audit(action="queue_team", message=f"Queueing {len(teamMembers)} team members - insert into database successful {insert_res}", payload=json)
    logger.debug("Insert Result: %s", insert_res)

    # Link the team to the user
    new_team_doc = db.collection('teams').document(doc_id)
    for user in users_list:
        # Add the team to the user's teams list
        user_doc = user.get()
        user_dict = user_doc.to_dict()
        user_teams = user_dict.get("teams", [])
        user_teams.append(new_team_doc)
        user.set({
            "teams": user_teams
        }, merge=True)


    # Link the team to the hackathon event
    hackathon_db_id = hackathon["id"]
    _append_team_to_hackathon(db, hackathon_db_id, new_team_doc)

    # Clear the cache
    logger.info("Clearing cache for event_id=%s doc_id=%s",
                hackathon_db_id, doc_id)
    clear_cache()

    # Get the team
    team = get_teams_list(doc_id)

    return {
        "message": f"Thanks for creating a team, we'll get back to you shortly in your Slack channel: #{slack_channel} - watch for a message from us!",
        "success": True,
        "team": team        
    }

def get_my_teams_by_event_id(propel_id, event_id):
    logger.debug(f"Getting my teams by event ID {event_id} for user {propel_id}")

    # Call get_propel_user_details_by_id(propel_id) to get user details
    _, user_id, _, _, name, _ = get_propel_user_details_by_id(propel_id)

    hackathon_dict = get_hackathon_by_event_id(event_id)

    teams = []
    if "teams" not in hackathon_dict:
        logger.debug("No teams found for event ID %s", event_id)
        return {
            "teams": teams,
            "user_id": user_id
        }

    db = get_db()
    team_refs = hackathon_dict["teams"]

    if not team_refs:
        return {
            "teams": teams,
            "user_id": user_id
        }

    # Batch fetch all teams at once (1 query instead of N queries)
    team_docs = db.get_all(team_refs)

    # Collect all unique user references from all teams
    all_user_refs = []
    teams_with_users = []

    for team_doc in team_docs:
        if not team_doc.exists:
            continue

        team_data = team_doc.to_dict()
        team_data["id"] = team_doc.id
        user_refs = team_data.get("users", [])

        # Store team data with its user references for later filtering
        teams_with_users.append({
            "team_data": team_data,
            "user_refs": user_refs
        })

        # Collect all user references for batch fetching
        all_user_refs.extend(user_refs)

    # Batch fetch all users at once (1 query instead of N*M queries)
    if all_user_refs:
        user_docs = db.get_all(all_user_refs)

        # Create a map of user_ref.id -> user_data for fast lookup
        user_map = {}
        for user_doc in user_docs:
            if user_doc.exists:
                user_data = user_doc.to_dict()
                user_map[user_doc.id] = user_data.get("user_id")

        # Filter teams that contain the target user
        my_teams = []
        for item in teams_with_users:
            team_data = item["team_data"]
            user_refs = item["user_refs"]

            # Check if any user in this team matches our target user
            for user_ref in user_refs:
                if user_ref.id in user_map and user_map[user_ref.id] == user_id:
                    my_teams.append(team_data)
                    break  # No need to check other users in this team

        # `problem_statements` holds DocumentReferences (not JSON) — replace
        # it with `project: {id, title}` (the project the team was approved
        # for; null before approval). One get_all across all my teams.
        project_ids = {}
        for team_data in my_teams:
            first = next(iter(team_data.get("problem_statements") or []), None)
            ps_ref = _as_ref(db, "problem_statements", first) if first is not None else None
            if ps_ref is not None:
                project_ids[team_data["id"]] = ps_ref
        titles = {}
        if project_ids:
            try:
                unique = {ref.id: ref for ref in project_ids.values()}
                for snap in db.get_all(list(unique.values())):
                    data = _snapshot_dict(snap)
                    if data is not None:
                        titles[snap.id] = data.get("title") or ""
            except Exception:
                logger.warning("Could not resolve team projects for /me", exc_info=True)

        for team_data in my_teams:
            team_data.pop("users", None)
            team_data.pop("problem_statements", None)
            ps_ref = project_ids.get(team_data["id"])
            team_data["project"] = (
                {"id": ps_ref.id, "title": titles[ps_ref.id]}
                if ps_ref is not None and ps_ref.id in titles else None
            )
            teams.append(public_team_view(team_data))

    logger.debug("Teams data: %s", teams)

    return {
        "teams": teams,
        "user_id": user_id
    }

    

def get_teams_by_hackathon_id(hackathon_id):
    """
    Get all teams for a specific hackathon ID.
    Optimized version using batch operations for better performance.
    """
    db = get_db()
    logger.info(f"Getting teams by hackathon ID: {hackathon_id}")
    
    try:
        # Get the event collection
        event_doc = db.collection("hackathons").document(hackathon_id).get()
        
        if not event_doc.exists:
            logger.warning(f"Hackathon {hackathon_id} not found")
            return {"teams": []}
            
        event_data = event_doc.to_dict()
        team_refs = event_data.get("teams", [])
        
        if not team_refs:
            logger.debug(f"No teams found for hackathon {hackathon_id}")
            return {"teams": []}
        
        logger.info(f"Found {len(team_refs)} team references for hackathon {hackathon_id}")
        
        # Try batch get all team documents, with fallback to individual gets
        team_docs = []
        try:
            if len(team_refs) <= 10:  # Firestore batch limit is 10
                team_docs = db.get_all(team_refs)
                logger.info(f"Successfully batch retrieved {len(team_docs)} team documents")
            else:
                # For larger batches, process in chunks of 10
                for i in range(0, len(team_refs), 10):
                    chunk = team_refs[i:i+10]
                    chunk_docs = db.get_all(chunk)
                    team_docs.extend(chunk_docs)
                logger.info(f"Successfully batch retrieved {len(team_docs)} team documents in chunks")
        except Exception as batch_error:
            logger.warning(f"Batch get failed, falling back to individual gets: {str(batch_error)}")
            # Fallback to individual gets
            team_docs = []
            for team_ref in team_refs:
                try:
                    team_doc = team_ref.get()
                    if team_doc.exists:
                        team_docs.append(team_doc)
                except Exception as individual_error:
                    logger.error(f"Failed to get individual team {team_ref.id}: {str(individual_error)}")
        
        # Collect all user references for batch processing
        all_user_refs = []
        team_user_mapping = {}  # Maps team_id to list of user_refs
        
        for team_doc in team_docs:
            if team_doc.exists:
                team_data = team_doc.to_dict()
                user_refs = team_data.get("users", [])
                team_user_mapping[team_doc.id] = user_refs
                all_user_refs.extend(user_refs)
        
        logger.info(f"Found {len(all_user_refs)} total user references across all teams")
        
        # Batch get all user documents if there are any users
        user_data_map = {}  # Maps user_ref.id to user_data
        if all_user_refs:
            # Remove duplicates while preserving order
            unique_user_refs = []
            seen_refs = set()
            for ref in all_user_refs:
                if ref.id not in seen_refs:
                    unique_user_refs.append(ref)
                    seen_refs.add(ref.id)
            
            logger.info(f"Processing {len(unique_user_refs)} unique user references")
            
            # Try batch get users, with fallback to individual gets
            try:
                if len(unique_user_refs) <= 10:
                    user_docs = db.get_all(unique_user_refs)
                else:
                    # For larger batches, process in chunks of 10
                    user_docs = []
                    for i in range(0, len(unique_user_refs), 10):
                        chunk = unique_user_refs[i:i+10]
                        chunk_docs = db.get_all(chunk)
                        user_docs.extend(chunk_docs)
                
                for user_doc in user_docs:
                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        user_data["id"] = user_doc.id
                        
                        # Remove unnecessary fields to reduce memory usage
                        for field in ["badges", "hackathons", "teams"]:
                            user_data.pop(field, None)
                        
                        user_data_map[user_doc.id] = user_data
            except Exception as user_batch_error:
                logger.warning(f"User batch get failed, falling back to individual gets: {str(user_batch_error)}")
                # Fallback to individual gets for users
                for user_ref in unique_user_refs:
                    try:
                        user_doc = user_ref.get()
                        if user_doc.exists:
                            user_data = user_doc.to_dict()
                            user_data["id"] = user_doc.id
                            
                            # Remove unnecessary fields
                            for field in ["badges", "hackathons", "teams"]:
                                user_data.pop(field, None)
                            
                            user_data_map[user_doc.id] = user_data
                    except Exception as individual_user_error:
                        logger.error(f"Failed to get individual user {user_ref.id}: {str(individual_user_error)}")
        
        # Build the final teams list
        teams = []
        for team_doc in team_docs:
            if not team_doc.exists:
                continue
                
            team_data = team_doc.to_dict()
            team_data["id"] = team_doc.id
            
            # Get team members using the pre-fetched user data
            users = []
            for user_ref in team_user_mapping.get(team_doc.id, []):
                if user_ref.id in user_data_map:
                    users.append(user_data_map[user_ref.id])
            
            # Remove unnecessary fields from team data
            for field in ["users", "problem_statements"]:
                team_data.pop(field, None)
            
            team_data["team_members"] = users
            teams.append(team_data)
        
        logger.info(f"Retrieved {len(teams)} teams for hackathon {hackathon_id}")
        return {"teams": teams}
        
    except Exception as e:
        logger.error(f"Error getting teams for hackathon {hackathon_id}: {str(e)}")
        return {"teams": []}


# Same slim member shape services.teams_service._enrich_team_users produces.
_SLIM_MEMBER_FIELDS = ("id", "user_id", "name", "nickname", "profile_image")


def public_hackathon_teams_view(payload):
    """Non-admin projection of get_teams_by_hackathon_id's payload: team
    internals stripped and each member trimmed to the slim public profile
    (the full user docs carry email_address etc.)."""
    teams = []
    for team in (payload or {}).get("teams") or []:
        view = public_team_view(team)
        if isinstance(view, dict) and isinstance(view.get("team_members"), list):
            view["team_members"] = [
                {k: m.get(k) for k in _SLIM_MEMBER_FIELDS} if isinstance(m, dict) else m
                for m in view["team_members"]
            ]
        teams.append(view)
    return {**(payload or {}), "teams": teams}


def _normalize_repo_link(link):
    return (link or "").strip().rstrip("/").lower()


def _link_repo_to_problem_statements(db, team_data, nonprofit, repo_name, repo_url):
    """Append the team's newly created GitHub repo to the `github` array of the
    problem statement(s) the team is working on, so the public /project/<id>
    page shows the code. Historically the repo was only written to the team
    doc, leaving most problem statements with an empty `github` field.

    Prefers the team's own `problem_statements` refs; falls back to the
    nonprofit's problem statements only when there is exactly one (anything
    else is ambiguous — skip and log). Never raises: repo linkage is
    best-effort and must not fail the approval flow.
    """
    try:
        ps_refs = []
        for ps in team_data.get("problem_statements") or []:
            if isinstance(ps, str):
                ps_refs.append(db.collection("problem_statements").document(ps))
            elif ps is not None and hasattr(ps, "get"):  # DocumentReference
                ps_refs.append(ps)

        if not ps_refs:
            # nonprofit comes from get_single_npo → doc_to_json, so entries are id strings
            npo_ps = (nonprofit or {}).get("problem_statements") or []
            if len(npo_ps) == 1 and isinstance(npo_ps[0], str):
                ps_refs.append(db.collection("problem_statements").document(npo_ps[0]))
            else:
                logger.info(
                    "Not linking repo %s to a problem statement: team has no "
                    "problem_statements and nonprofit has %d (ambiguous)",
                    repo_url, len(npo_ps),
                )
                return

        for ps_ref in ps_refs:
            ps_doc = ps_ref.get()
            if not ps_doc.exists:
                logger.warning("Problem statement %s not found; skipping repo link", ps_ref.id)
                continue
            ps_data = ps_doc.to_dict() or {}
            existing = ps_data.get("github")
            if isinstance(existing, list):
                github_list = list(existing)
            elif isinstance(existing, str) and existing.strip():
                # Legacy shape: a bare URL string — convert to the {name, link} form
                legacy_link = existing.strip()
                github_list = [{"name": legacy_link.rstrip("/").split("/")[-1], "link": legacy_link}]
            else:
                github_list = []

            already_linked = {
                _normalize_repo_link(entry.get("link") if isinstance(entry, dict) else entry)
                for entry in github_list
            }
            if _normalize_repo_link(repo_url) in already_linked:
                continue

            github_list.append({"name": repo_name, "link": repo_url})
            ps_ref.set({"github": github_list}, merge=True)
            logger.info("Linked repo %s to problem statement %s", repo_url, ps_ref.id)

        # The public single-problem-statement read is cached (10 min) and NOT
        # in the shared cache registry — clear it explicitly
        try:
            from api.messages.messages_service import get_single_problem_statement_old
            get_single_problem_statement_old.cache_clear()
        except Exception:
            logger.warning("Could not clear problem statement cache", exc_info=True)
    except Exception:
        logger.error("Failed linking repo %s to problem statement(s)", repo_url, exc_info=True)


def _select_problem_statement(db, team_data, nonprofit):
    """The problem statement the repo README should name: the team's own
    first `problem_statements` ref, else the nonprofit's first one. Returns a
    (ps_id, ps_dict) pair or ("", {}) — never raises."""
    try:
        candidates = []
        for ps in team_data.get("problem_statements") or []:
            if isinstance(ps, str):
                candidates.append(ps)
            elif ps is not None and hasattr(ps, "id"):  # DocumentReference
                candidates.append(ps.id)
        if not candidates:
            npo_ps = (nonprofit or {}).get("problem_statements") or []
            if len(npo_ps) >= 1 and isinstance(npo_ps[0], str):
                candidates.append(npo_ps[0])
        for ps_id in candidates:
            snap = db.collection("problem_statements").document(ps_id).get()
            if snap.exists:
                return ps_id, (snap.to_dict() or {})
    except Exception:
        logger.warning("Could not resolve a problem statement for the repo seed", exc_info=True)
    return "", {}


def _build_repo_seed_files(db, team_data, team_id, hackathon_event, nonprofit, nonprofit_id,
                           creator_name, devpost_url, github_org, repository_name):
    """Render the files committed into a new team repo (see
    common.utils.team_repo_seed). Returns None on ANY failure so
    create_github_repo falls back to its legacy README — seeding must never
    block an approval."""
    try:
        from common.utils.team_repo_seed import SeedContext, build_seed_files
        ps_id, ps = _select_problem_statement(db, team_data, nonprofit)
        ctx = SeedContext(
            event_id=team_data.get("hackathon_event_id") or "",
            event_title=hackathon_event.get("title") or "",
            hackathon=hackathon_event,
            team_id=team_id,
            team_name=team_data.get("name") or "",
            team_slack_channel=team_data.get("slack_channel") or "",
            creator_name=creator_name or "",
            nonprofit_id=nonprofit_id or "",
            nonprofit_name=(nonprofit or {}).get("name") or "",
            nonprofit_slack_channel=(nonprofit or {}).get("slack_channel") or "",
            problem_statement_id=ps_id,
            problem_statement_title=ps.get("title") or "",
            problem_statement_slack_channel=ps.get("slack_channel") or "",
            devpost_url=devpost_url or "",
            github_org=github_org or "",
            repo_name=repository_name or "",
        )
        return build_seed_files(ctx)
    except Exception:
        logger.error("Building repo seed files failed; using legacy README", exc_info=True)
        return None


def _resolve_nonprofit_project(db, nonprofit_id, problem_statement_id):
    """The project a team is approved for / moved to. Returns
    ((project_dict | None), refusal | None) where project_dict is
    {"id", "title", "nonprofit": <get_single_npo dict>}.

    - unknown nonprofit → 400 nonprofit_not_found
    - nonprofit with no projects → 400 no_problem_statements
    - id given but not one of the nonprofit's projects → 400 invalid_problem_statement
    - no id: a single-project nonprofit auto-fills it; with several →
      400 problem_statement_required
    """
    if not nonprofit_id:
        return None, _refusal("missing_fields", "Pick a nonprofit first.")
    nonprofit = None
    try:
        nonprofit = (get_single_npo(nonprofit_id) or {}).get("nonprofits")
    except Exception:
        logger.warning("Nonprofit lookup failed for %s", nonprofit_id, exc_info=True)
    if not nonprofit or not nonprofit.get("name"):
        return None, _refusal("nonprofit_not_found", "We couldn't find that nonprofit. Refresh and pick it again.")

    npo_ps_ids = []
    for value in nonprofit.get("problem_statements") or []:
        ps = _ref_id(value) or (value.get("id") if isinstance(value, dict) else None)
        if ps and ps not in npo_ps_ids:
            npo_ps_ids.append(ps)
    if not npo_ps_ids:
        return None, _refusal("no_problem_statements", f"{nonprofit['name']} has no projects yet. Add one before approving a team for it.")

    ps_id = _ref_id(problem_statement_id) if problem_statement_id else None
    if ps_id is None:
        if len(npo_ps_ids) == 1:
            ps_id = npo_ps_ids[0]
        else:
            return None, _refusal(
                "problem_statement_required",
                f"{nonprofit['name']} has {len(npo_ps_ids)} projects — pick the one this team will build.",
            )
    elif ps_id not in npo_ps_ids:
        return None, _refusal("invalid_problem_statement", f"That project isn't one of {nonprofit['name']}'s projects.")

    snap = db.collection("problem_statements").document(ps_id).get()
    ps_data = _snapshot_dict(snap)
    if ps_data is None:
        return None, _refusal("invalid_problem_statement", "That project no longer exists. Refresh and pick again.")
    return {"id": ps_id, "title": ps_data.get("title") or "", "nonprofit": nonprofit}, None


# A claim older than this is treated as abandoned (the approving worker
# crashed or was redeployed mid-approval) and may be taken over.
APPROVAL_CLAIM_STALE_AFTER = timedelta(minutes=10)
APPROVING_STATUS = "APPROVING"


def _in_transaction(db, body):
    """Runs body(transaction) in a real Firestore transaction. Tests replace
    this with `lambda db, body: body(FakeTransaction())` — a real
    @firestore.transactional callable needs a live Firestore client."""
    from firebase_admin import firestore

    @firestore.transactional
    def _run(transaction):
        return body(transaction)

    return _run(db.transaction())


def _tx_get_one(transaction, ref):
    """First snapshot from transaction.get(ref) — the real Transaction.get()
    returns a generator for a DocumentReference."""
    result = transaction.get(ref)
    if hasattr(result, "exists"):
        return result
    return next(iter(result), None)


def _approval_claim_is_stale(team_data, now=None):
    """True when an APPROVING claim is older than APPROVAL_CLAIM_STALE_AFTER
    (or carries no readable start time)."""
    now = now or datetime.utcnow()
    try:
        started = datetime.fromisoformat(team_data.get("approval_started_at"))
    except (TypeError, ValueError):
        return True
    return now - started > APPROVAL_CLAIM_STALE_AFTER


def _claim_team_for_approval(db, team_ref, admin_user_id):
    """Atomically flip IN_REVIEW → APPROVING so two admins approving at once
    can't both post to Slack and mint keys. Returns None when this caller
    owns the approval, else the status that blocked it. An APPROVING claim
    older than APPROVAL_CLAIM_STALE_AFTER is taken over."""
    now = datetime.utcnow()

    def body(transaction):
        data = _snapshot_dict(_tx_get_one(transaction, team_ref))
        if data is None:
            return "NOT_FOUND"
        status = data.get("status")
        if status == APPROVING_STATUS:
            if not _approval_claim_is_stale(data, now):
                return status
        elif status != "IN_REVIEW":
            return status or "UNKNOWN"
        transaction.update(team_ref, {
            "status": APPROVING_STATUS,
            "approval_started_at": now.isoformat(),
            "approval_started_by": admin_user_id,
        })
        return None

    return _in_transaction(db, body)


def _release_approval_claim(team_ref):
    """Put a claimed team back in the review queue after a failed approval."""
    try:
        team_ref.set({"status": "IN_REVIEW"}, merge=True)
    except Exception:
        logger.error("Could not restore IN_REVIEW on team %s after a failed approval",
                     getattr(team_ref, "id", "?"), exc_info=True)


def approve_team(admin_user_id, json):
    """
    Admin function to approve a team and pair it with a nonprofit project.
    Body: {teamId, nonprofitId, problemStatementId?}. problemStatementId is
    required when the nonprofit has more than one project (a single-project
    nonprofit auto-fills it).
    - Claims the team (IN_REVIEW → APPROVING) in a transaction; a second
      approver gets 409 already_approved
    - Creates the GitHub repo, posts to Slack, sets status NONPROFIT_SELECTED,
      active, selected_nonprofit_id, problem_statements: [ref], github_links
    - Mints AI gateway keys, links the repo to the chosen project
    - Any failure before the final write restores IN_REVIEW
    Refusals are 4xx {error, message, success: false}.
    """
    send_slack_audit(action="approve_team", message="Approving", payload=json)

    db = get_db()
    logger.debug("Team Approval")
    logger.debug(json)

    json = json if isinstance(json, dict) else {}
    team_id = _ref_id(json.get("teamId"))
    nonprofit_id = _ref_id(json.get("nonprofitId"))
    if not team_id or not nonprofit_id:
        return _refusal("missing_fields", "Pick a nonprofit (and its project) before approving.")

    team_doc = db.collection('teams').document(team_id)
    team_data = _snapshot_dict(team_doc.get())
    if not team_data:
        return _refusal("team_not_found", "Team not found.", 404)

    status = team_data.get("status")
    abandoned_claim = status == APPROVING_STATUS and _approval_claim_is_stale(team_data)
    if status != "IN_REVIEW" and not abandoned_claim:
        if status == APPROVING_STATUS or team_data.get("approved_at"):
            return _refusal("already_approved", "This team was already approved (or another organizer is approving it right now). Refresh to see it.", 409)
        return _refusal("not_in_review", f"This team isn't waiting for review (status: {status}).", 409)

    project, refusal = _resolve_nonprofit_project(db, nonprofit_id, json.get("problemStatementId"))
    if refusal:
        return refusal
    nonprofit = project["nonprofit"]
    nonprofit_name = nonprofit["name"]
    problem_statement_id = project["id"]
    problem_statement_title = project["title"]

    if not team_data.get("users"):
        return _refusal("team_has_no_members", "This team has no members, so there's no one to create the repo for. Add a member first.")

    # Prepare data for GitHub repo creation
    team_name = team_data.get("name") or ""
    slack_channel = team_data.get("slack_channel") or ""
    github_username = team_data.get("github_username") or ""
    hackathon_event_id = team_data.get("hackathon_event_id")

    # Get hackathon event details by event ID
    hackathon_event = get_hackathon_by_event_id(hackathon_event_id) if hackathon_event_id else None
    if not hackathon_event:
        return _refusal("event_not_found", "This team's hackathon event wasn't found.")

    # Make sure that github_org is set within the event. Older events may
    # still carry a pasted URL / @handle from before the validator normalized
    # it, so reduce it to the bare slug here too.
    github_org = normalize_github_org(hackathon_event.get("github_org"))
    if not github_org:
        return _refusal(
            "github_org_missing",
            "This event has no GitHub organization set. "
            "Add it under Admin → Hackathon → Overview → GitHub organization "
            "(just the org name, e.g. Opportunity-Hack-2026), then approve again.",
        )
    logger.info("GitHub organization: %s", github_org)

    # Claim the team before any side effect (repo, Slack, keys).
    blocked = _claim_team_for_approval(db, team_doc, admin_user_id)
    if blocked == "NOT_FOUND":
        return _refusal("team_not_found", "Team not found.", 404)
    if blocked:
        return _refusal("already_approved", "This team was already approved (or another organizer is approving it right now). Refresh to see it.", 409)

    try:
        result = _approve_claimed_team(
            db, admin_user_id, team_doc, team_id, team_data, nonprofit, nonprofit_id,
            nonprofit_name, problem_statement_id, problem_statement_title,
            team_name, slack_channel, github_username, hackathon_event_id,
            hackathon_event, github_org,
        )
    except Exception:
        _release_approval_claim(team_doc)
        raise
    if isinstance(result, tuple):  # a refusal after the claim — back to review
        _release_approval_claim(team_doc)
    return result


def _approve_claimed_team(db, admin_user_id, team_doc, team_id, team_data, nonprofit, nonprofit_id,
                          nonprofit_name, problem_statement_id, problem_statement_title,
                          team_name, slack_channel, github_username, hackathon_event_id,
                          hackathon_event, github_org):
    """The side-effecting half of approve_team, run only by the caller that
    claimed the team. Returns the success dict, or a refusal tuple."""
    # The chosen project wins over anything the team doc carried, so the
    # repo README seed and the repo → project link both use it.
    team_data["problem_statements"] = [problem_statement_id]

    # Look through the "links" within the hackathon event to find any url that contains "devpost"
    devpost_url = ""
    for link in hackathon_event.get("links", []):
        if "devpost" in link.get("link", ""):
            devpost_url = link["link"]
            break
    logger.info("DevPost URL: %s", devpost_url)
    
    # Get admin user details for audit
    _, _, _, _, admin_name, _ = get_propel_user_details_by_id(admin_user_id)
    
    # Get first team member for GitHub repo creation (team_data["users"] is
    # non-empty — approve_team checked)
    first_user_ref = team_data["users"][0]
    first_user_data = _snapshot_dict(first_user_ref.get()) if hasattr(first_user_ref, "get") else None
    first_user_name = (first_user_data or {}).get("name") or "Team Member"
    
    # Generate GitHub repo name        
    nonprofit_title = nonprofit_name.replace(" ", "").replace("-", "")[:20]
    repository_name = f"{team_name}-{nonprofit_title}"[:100]
    
    # Files committed into the new repo: README with real nonprofit/Slack/
    # dashboard links + the final-day timeline, AGENTS.md/CLAUDE.md working
    # agreements for AI tools, Claude Code hooks, PR template + lint. Pure
    # and best-effort: if anything about building it fails we fall back to
    # the legacy README rather than block the approval.
    seed_files = _build_repo_seed_files(
        db, team_data, team_id, hackathon_event, nonprofit, nonprofit_id,
        first_user_name, devpost_url, github_org, repository_name,
    )

    # Create GitHub repo
    try:
        logger.info("Creating github repo %s", repository_name)
        repo = create_github_repo(
            repository_name, 
            hackathon_event_id, 
            first_user_name, 
            team_name, 
            slack_channel,             
            github_username, 
            nonprofit_name, 
            nonprofit_id,
            github_org,
            devpost_url,
            seed_files=seed_files,
        )
    except ValueError as e:
        logger.error("Error creating GitHub repo: %s", e)
        if "already exists" in str(e):
            # create_repo lost a race with another create of the same name
            # (create_github_repo already reuses a repo it can see). The repo
            # is there — point at it instead of crashing on an unset `repo`.
            logger.warning("GitHub repo %s already exists; using it", repository_name)
            repo = {
                "repo_name": repository_name,
                "full_url": f"https://github.com/{github_org}/{repository_name}",
            }
        else:
            return _refusal("github_repo_failed", f"Error creating GitHub repo: {e}", 502)

    nonprofit_url = f"https://ohack.dev/nonprofit/{nonprofit_id}"
    project_url = f"https://www.ohack.dev/project/{problem_statement_id}"

    logger.info("GitHub repo created: %s", repo["full_url"])
    
    # Send notification to the team. DevPost is optional now (the dashboard is
    # the submission of record) — only mention it when the event has a link.
    devpost_step = (f"; optionally also <{devpost_url}|submit on DevPost>" if devpost_url else "")
    slack_message = f'''
:rocket: Great news! Your team *{team_name}* has been approved and paired with a nonprofit! :tada:

*Channel:* #{slack_channel}
*Nonprofit:* <{nonprofit_url}|{nonprofit_name}>
*Project:* <{project_url}|{problem_statement_title or problem_statement_id}>
*Approved by:* {admin_name}

:github_parrot: *GitHub Repository:* {repo['full_url']}
:rotating_light: *This is your team's ONE repo — ALL code goes here.* Please don't create your own repo (personal or org): judges, your Team Dashboard, the public project page and the nonprofit only see this one, so work pushed anywhere else won't count. Add every teammate as a collaborator. We're building for the public good (MIT license).

:question: *Need help?* 
Join <#C01E5CGDQ74> for questions and updates.

:clipboard: *Next Steps:*
1. Add team to GitHub repo: <https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600|How-to guide> — then read the repo's README and AGENTS.md (final-day timeline, PR habits, submission checklist)
2. Your <https://www.ohack.dev/hack/{hackathon_event_id}/manageteam|Team Dashboard> is where you submit: project story + demo video link, then Submit (keep updating until the close){devpost_step}
3. Study your nonprofit slides and software requirements doc and chat with mentors
4. Code, collaborate, and create — small PRs, documented as GitHub Issues
5. Share your progress on the socials: `#ohack` and `@opportunityhack`
6. <https://www.ohack.dev/volunteer/track|Log volunteer hours>
7. Post-hack: Update LinkedIn with your amazing experience! You are getting real-world industry experience!
8. Update <https://www.ohack.dev/profile|your profile> for a chance to win prizes!
9. Follow the schedule at <https://www.ohack.dev/hack/{hackathon_event_id}|ohack.dev/hack/{hackathon_event_id}>

Let's make a difference! :muscle: :heart:
'''
    send_slack(slack_message, slack_channel)
    send_slack(slack_message, "log-team-creation")
    
    repo_name = repo["repo_name"]
    full_github_repo_url = repo["full_url"]
    
    # Update the team document
    team_doc.set({
        "status": "NONPROFIT_SELECTED",
        "active": "True",        
        "selected_nonprofit_id": nonprofit_id,
        "problem_statements": [db.collection("problem_statements").document(problem_statement_id)],
        "github_links": [
            {
                "link": full_github_repo_url,
                "name": repo_name
            }
        ],
        "approved_by": admin_user_id,
        "approved_at": datetime.now().isoformat()
    }, merge=True)

    # Mint the team's AI gateway key now that approval is durable.
    # Best-effort: a LiteLLM outage must never fail an approval.
    # Lazy import: teams_service must never import gateway_keys at module top.
    try:
        from api.teams.gateway_keys import provision_team_gateway_key
        meta = provision_team_gateway_key(team_id)
        partial = (meta or {}).get("partial_failure")
        if partial:
            logger.warning(
                "Gateway %s mint failed for team %s (non-blocking; admin retry mints it): %s",
                partial.get("label"), team_id, partial.get("error"),
            )
        else:
            logger.info("Gateway API keys provisioned for team %s", team_id)
    except Exception as e:
        logger.warning("Gateway key provisioning failed for team %s (non-blocking): %s", team_id, e)

    # Fix-forward: also link the repo to the problem statement(s) so the
    # public project page shows the code (best-effort, never blocks approval)
    _link_repo_to_problem_statements(
        db, team_data, nonprofit, repo_name, full_github_repo_url
    )

    # The approval is durable from here on: a failure below must not send
    # the team back to review.
    team = None
    try:
        clear_cache()
        team = get_teams_list(team_id)
    except Exception:
        logger.warning("Post-approval cache clear / team reload failed for %s", team_id, exc_info=True)

    return {
        "message": (
            f"Team approved for “{problem_statement_title}” ({nonprofit_name}). "
            f"Slack notification sent to #{slack_channel}."
        ),
        "success": True,
        "team": team,
        "problem_statement_id": problem_statement_id,
    }

def get_queued_teams():
    """Get all teams with status IN_REVIEW"""
    db = get_db()
    teams_ref = db.collection('teams').where("status", "==", "IN_REVIEW").stream()
    
    teams = []
    for team in teams_ref:
        team_data = team.to_dict()
        team_data['id'] = team.id
        teams.append(team_data)
    
    return {"teams": teams}


def send_team_message(admin_user, teamid, json):
    """
    Send a message to a team
    - Get the team document
    - Send the message to the team Slack channel
    """
    send_slack_audit(action="send_team_message", message="Sending", payload=json)
    
    
    admin_name = f"{admin_user.first_name} ({admin_user.last_name})"
    db = get_db()
    logger.debug("Sending Team Message")
    
    # Get the team document
    team_doc = db.collection('teams').document(teamid)
    team_data = team_doc.get().to_dict()
    
    if not team_data:
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Get the message from the request
    message = json.get("message", "")
    
    # Send the message to the team Slack channel
    slack_channel = team_data["slack_channel"]
    send_slack(message, slack_channel)

    # I'd also like to send each of them an email using send_volunteer_message from volunteers_service.py where we need to get their Slack User ID and pass that in as recipient_id
    from api.volunteers.volunteers_service import send_volunteer_message, get_user_from_slack_id
    for user_ref in team_data.get("users", []):
        user_data = user_ref.get().to_dict()
        slack_user_id = user_data.get("user_id", "")
        if slack_user_id:
            send_volunteer_message(
                admin_user=admin_user,
                recipient_id=slack_user_id,
                subject=f"Message from Opportunity Hack Team {team_data['name']}",
                message=message
            )
    

    # If communication_history doesn't exist, create it and append the timestamp, sender, text
    if "communication_history" not in team_data:
        team_data["communication_history"] = []
    team_data["communication_history"].append({
        "timestamp": datetime.now().isoformat(),
        "sender": admin_name,
        "text": message
    })
    # Update the team document
    team_doc.set({
        "communication_history": team_data["communication_history"]
    }, merge=True)
    # Clear cache
    clear_cache()
    
    return {
        "message": f"Message sent to team {teamid}",
        "success": True,
        "team_id": teamid
    }


def user_is_on_team(propel_user_id, team_id):
    """
    Returns True if the caller (identified by their PropelAuth UUID) is one of
    the team's users[]. Translates propel_id -> OAuth user_id via
    get_propel_user_details_by_id (same pattern as get_my_teams_by_event_id),
    then walks team.users[] DocumentReferences and compares each user doc's
    `user_id` field (or its `propel_id` field as a fallback).
    """
    if not propel_user_id or not team_id:
        return False
    db = get_db()
    team_doc = db.collection("teams").document(team_id).get()
    if not team_doc.exists:
        return False

    try:
        details = get_propel_user_details_by_id(propel_user_id) or ()
        caller_oauth_user_id = details[1] if len(details) > 1 else None
    except Exception as e:
        logger.warning("user_is_on_team: get_propel_user_details_by_id failed: %s", e)
        caller_oauth_user_id = None

    team_data = team_doc.to_dict() or {}
    for ref in team_data.get("users", []):
        try:
            snap = ref.get() if hasattr(ref, "get") else db.collection("users").document(ref).get()
            if not snap.exists:
                continue
            user_data = snap.to_dict() or {}
            stored_user_id = user_data.get("user_id")
            stored_propel_id = user_data.get("propel_id")
            if caller_oauth_user_id and stored_user_id and stored_user_id == caller_oauth_user_id:
                return True
            if stored_propel_id and stored_propel_id == propel_user_id:
                return True
        except Exception as e:
            logger.warning("user_is_on_team: failed to resolve a user ref on team %s: %s", team_id, e)
            continue
    return False


def _completion_done_count(checklist):
    if not isinstance(checklist, dict):
        return 0
    return sum(1 for slug in COMPLETION_ITEM_SLUGS if checklist.get(slug, {}).get("done"))


def _project_link(team_data, team_id):
    event_id = team_data.get("hackathon_event_id") or ""
    if event_id:
        return f"https://ohack.dev/hack/{event_id}/team/{team_id}"
    return f"https://ohack.dev/hack/team/{team_id}"


def _completer_name(propel_user_id):
    try:
        details = get_propel_user_details_by_id(propel_user_id) or {}
        return details.get("firstName") or details.get("email") or "A teammate"
    except Exception:
        return "A teammate"


def toggle_completion_item(propel_user_id, team_id, item_slug):
    """
    Mark a single Definition-of-Done item complete for a team. Sends a Slack
    message to the team's channel. Idempotent in the strict sense: once an item
    is `done=True`, this returns 409 (no double-Slack, no unchecking).
    """
    if item_slug not in COMPLETION_ITEM_SLUGS:
        return {"error": f"Unknown checklist item: {item_slug}"}, 400

    if not user_is_on_team(propel_user_id, team_id):
        return {"error": "You must be on this team to update its completion checklist."}, 403

    db = get_db()
    team_doc = db.collection("teams").document(team_id)
    snap = team_doc.get()
    if not snap.exists:
        return {"error": "Team not found"}, 404
    team_data = snap.to_dict() or {}
    checklist = dict(team_data.get("completion_checklist") or {})
    if checklist.get(item_slug, {}).get("done"):
        return {"error": "Already complete"}, 409

    name = _completer_name(propel_user_id)
    now_iso = datetime.now().isoformat()
    checklist[item_slug] = {
        "done": True,
        "completed_at": now_iso,
        "completed_by_propel_id": propel_user_id,
        "completed_by_name": name,
    }
    done_n = _completion_done_count(checklist)
    total_n = len(COMPLETION_ITEMS)
    new_status = "complete" if done_n == total_n else "in_progress"

    update = {
        "completion_checklist": checklist,
        "completion_status": new_status,
    }
    team_doc.set(update, merge=True)

    item = COMPLETION_ITEM_SLUGS[item_slug]
    slack_channel = team_data.get("slack_channel")
    if slack_channel:
        msg = (
            f":white_check_mark: *{name}* checked off *{item['label']}* "
            f"({done_n}/{total_n} complete!)\n"
            f"{item['blurb']}\n"
            f"See the full Definition of Done → https://ohack.dev/about/completion"
        )
        try:
            async_send_slack(message=msg, channel=slack_channel)
        except Exception as e:
            logger.error("toggle_completion_item: send_slack failed for team %s: %s", team_id, e)

    send_slack_audit(
        action="completion_toggle",
        message=f"Team {team_id} item {item_slug} marked done by {name} ({done_n}/{total_n})",
        payload={"team_id": team_id, "item": item_slug, "by": propel_user_id},
    )
    clear_cache()

    # Route through get_team so DocumentReferences on users[] are flattened
    # via doc_to_json + enriched into profile dicts (Flask can't JSON-serialize
    # raw DocumentReference objects).
    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh, "done": done_n, "total": total_n}, 200


def mark_team_complete(propel_user_id, team_id):
    """
    Finalize a team's project. Requires all 8 checklist items to be done. Posts
    a celebration message to the team's Slack channel CCing the OHack admins.
    Idempotent: returns 409 if already complete.
    """
    if not user_is_on_team(propel_user_id, team_id):
        return {"error": "You must be on this team to mark it complete."}, 403

    db = get_db()
    team_doc = db.collection("teams").document(team_id)
    snap = team_doc.get()
    if not snap.exists:
        return {"error": "Team not found"}, 404
    team_data = snap.to_dict() or {}

    if team_data.get("completion_status") == "complete":
        return {"error": "Project already marked complete"}, 409

    checklist = team_data.get("completion_checklist") or {}
    missing = [s for s in COMPLETION_ITEM_SLUGS if not checklist.get(s, {}).get("done")]
    if missing:
        return {"error": "Cannot mark complete: items remaining", "missing": missing}, 409

    name = _completer_name(propel_user_id)
    now_iso = datetime.now().isoformat()
    team_doc.set({
        "completion_status": "complete",
        "completion_completed_at": now_iso,
        "completion_completed_by_propel_id": propel_user_id,
        "completion_completed_by_name": name,
    }, merge=True)

    slack_channel = team_data.get("slack_channel")
    team_name = team_data.get("name", "Your team")
    project_link = _project_link(team_data, team_id)
    admin_mentions = " ".join(f"<@{uid}>" for uid in TEAM_COMPLETION_SLACK_ADMINS)
    msg = (
        f":tada: :rocket: :tada:  *{team_name} marked their project COMPLETE!*\n"
        f"Congratulations team — you shipped it. 🏆\n\n"
        f"cc {admin_mentions}\n\n"
        f"Project link: {project_link}\n"
        f"See examples of completed projects: https://ohack.dev/about/success-stories"
    )
    if slack_channel:
        try:
            async_send_slack(message=msg, channel=slack_channel)
        except Exception as e:
            logger.error("mark_team_complete: send_slack failed for team %s: %s", team_id, e)

    send_slack_audit(
        action="completion_complete",
        message=f"Team {team_id} ({team_name}) marked PROJECT COMPLETE by {name}",
        payload={"team_id": team_id, "by": propel_user_id},
    )
    clear_cache()

    fresh = (get_team(team_id) or {}).get("team") or {}
    return {"success": True, "team": fresh}, 200
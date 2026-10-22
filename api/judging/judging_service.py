from typing import Dict
from datetime import datetime

from common.log import get_logger, debug, warning, error
from db.db import (
    fetch_judge_assignments_by_judge_id,
    fetch_judge_assignment_by_id,
    fetch_judge_assignments_by_event_and_judge,
    fetch_judge_scores_by_judge_and_event,
    fetch_judge_score,
    insert_judge_assignment,
    update_judge_assignment,
    delete_judge_assignment,
    fetch_judge_panels_by_event,
    fetch_judge_panel,
    insert_judge_panel,
    update_judge_panel,
    delete_judge_panel,
    upsert_judge_score,
    fetch_judge_assignments_by_panel_id,
    fetch_judge_assignment,
    get_volunteer_from_db_by_user_id_volunteer_type_and_event_id,
    fetch_judge_scores_by_event_and_round,
    fetch_judge_assignments_by_event_id,
    fetch_judge_scores_by_event_id,
    fetch_judge_assignments_by_event_id
)
from model.judge_assignment import JudgeAssignment
from model.judge_score import JudgeScore
from model.judge_panel import JudgePanel
from services.hackathons_service import get_single_hackathon_event
from services.teams_service import (
    get_team,
    get_teams_batch
)
from common.utils.firebase import get_nonprofit_by_id, get_volunteer_from_db_by_event

logger = get_logger("judging_service")


def get_judge_assignments(judge_id: str) -> Dict:
    """Get all hackathon assignments for a specific judge."""
    try:
        debug(logger, "Fetching judge assignments", judge_id=judge_id)

        assignments = fetch_judge_assignments_by_judge_id(judge_id)

        # Group assignments by event_id
        hackathon_assignments = {}
        for assignment in assignments:
            event_id = assignment.event_id
            if event_id not in hackathon_assignments:
                hackathon_assignments[event_id] = {
                    'event_id': event_id,
                    'round1_teams': [],
                    'round2_teams': []
                }

            if assignment.round == 'round1':
                hackathon_assignments[event_id]['round1_teams'].append(
                    assignment.team_id)
            elif assignment.round == 'round2':
                hackathon_assignments[event_id]['round2_teams'].append(
                    assignment.team_id)

        # Fetch hackathon details and judge scores for each event
        hackathons = []
        for event_id, data in hackathon_assignments.items():
            try:
                hackathon_info = get_single_hackathon_event(event_id)
                if hackathon_info:
                    # Get judging progress
                    scores = fetch_judge_scores_by_judge_and_event(
                        judge_id, event_id)

                    round1_completed = len([s for s in scores
                                           if s.round == 'round1'])
                    round2_completed = len([s for s in scores
                                           if s.round == 'round2'])

                    hackathon_result = {
                        "event_id": event_id,
                        "title": hackathon_info.get('title', ''),
                        "start_date": hackathon_info.get('start_date'),
                        "end_date": hackathon_info.get('end_date'),
                        "round1_teams": len(data['round1_teams']),
                        "round2_teams": len(data['round2_teams']),
                        "judging_status": {
                            "round1": {
                                "completed": round1_completed,
                                "total": len(data['round1_teams'])
                            },
                            "round2": {
                                "completed": round2_completed,
                                "total": len(data['round2_teams'])
                            }
                        }
                    }
                    hackathons.append(hackathon_result)
            except Exception as e:
                warning(logger, "Error fetching hackathon details",
                        event_id=event_id, error=str(e))
                continue

        return {"hackathons": hackathons}

    except Exception as e:
        error(logger, "Error fetching judge assignments",
              judge_id=judge_id, error=str(e))
        return {"hackathons": [], "error": "Failed to fetch assignments"}


def get_judge_teams(judge_id: str, event_id: str) -> Dict:
    """Get teams assigned to a judge for a specific hackathon."""
    try:
        debug(logger, f"Fetching judge teams {judge_id} for event {event_id}")           

        assignments = fetch_judge_assignments_by_event_and_judge(
            event_id, judge_id)

        # Separate assignments by round
        round1_team_ids = [a.team_id for a in assignments
                           if a.round == 'round1']
        round2_team_ids = [a.team_id for a in assignments
                           if a.round == 'round2']

        # Get team details
        all_team_ids = list(set(round1_team_ids + round2_team_ids))
        teams_data = (get_teams_batch({"team_ids": all_team_ids})
                      if all_team_ids else [])
        
        # Log the teams_data
        debug(logger, f"Fetched teams data {len(teams_data)} teams for judge {judge_id} in event {event_id}")

        # Get existing scores for this judge and event
        scores = fetch_judge_scores_by_judge_and_event(judge_id, event_id)
        score_lookup = {}
        for score in scores:
            key = f"{score.team_id}_{score.round}"
            score_lookup[key] = score

        # Format teams for each round
        round1_teams = []
        round2_teams = []

        # Get all of the nonprofit IDs for this event as a set, remove any that are empty strings
        nonprofit_ids = {team.get('selected_nonprofit_id', '') for team in teams_data}
        nonprofit_ids = {id for id in nonprofit_ids if id}

        logger.info(f"Nonprofit IDs for event {event_id}: {nonprofit_ids}")

        # For each id, call get_nonprofit_by_id, get the id/name mapping
        nonprofit_mapping = {id: get_nonprofit_by_id(id) for id in nonprofit_ids}
        logger.info(f"Fetched {len(nonprofit_mapping)} nonprofits for event {event_id}")

        # One batched profile read for every member of every assigned team
        # (was one serial get_user_by_id per member per team per judge —
        # 15 judges × 10 teams × 4 members at kickoff).
        users_by_id = _batch_users_for_teams(teams_data)

        for team in teams_data:
            team_id = team.get('id')
            nonprofit_name = nonprofit_mapping.get(team.get('selected_nonprofit_id', ''), {}).get('name', '')
            nonprofit_id = team.get('selected_nonprofit_id', '')

            # Round 2 assignments carry the demo slot; prefer that one when
            # the same team is also assigned in round 1.
            assignment = next(
                (a for a in assignments if a.team_id == team_id and a.round == 'round2'),
                None) or next((a for a in assignments if a.team_id == team_id), None)

            team_data = format_team_for_judge(team, score_lookup, nonprofit_id, nonprofit_name,
                                              users_by_id=users_by_id)

            if team_id in round1_team_ids:
                round1_teams.append(team_data)

            if team_id in round2_team_ids:
                # Add round2 specific fields
                team2_data = team_data.copy()
                team2_data.update({
                    "demo_time": assignment.demo_time if assignment else None,
                    "room": assignment.room if assignment else None,
                    "judged": f"{team_id}_round2" in score_lookup,
                    "score": (score_lookup.get(f"{team_id}_round2").total_score
                              if f"{team_id}_round2" in score_lookup else None)
                })
                round2_teams.append(team2_data)

        return {
            "round1_teams": round1_teams,
            "round2_teams": round2_teams,
            "event": _event_summary(event_id),
            "locks": get_round_locks(event_id, public=True),
        }

    except Exception as e:
        error(logger, "Error fetching judge teams",
              judge_id=judge_id, event_id=event_id, error=str(e))
        # print stack trace
        import traceback
        traceback.print_exc()
        return {"round1_teams": [], "round2_teams": [],
                "error": "Failed to fetch teams"}


def _event_summary(event_id: str) -> Dict:
    """Title/dates/timezone for the judge dashboard masthead (cached getter;
    best-effort — never fails the teams payload)."""
    try:
        h = get_single_hackathon_event(event_id) or {}
        return {
            "event_id": event_id,
            "title": h.get("title", ""),
            "start_date": h.get("start_date"),
            "end_date": h.get("end_date"),
            "timezone": h.get("timezone"),
        }
    except Exception as e:
        warning(logger, "Event summary lookup failed", event_id=event_id, error=str(e))
        return {"event_id": event_id, "title": "", "start_date": None, "end_date": None, "timezone": None}


def get_team_details(team_id: str) -> Dict:
    """Get detailed information about a specific team for judging."""
    try:
        debug(logger, "Fetching team details", team_id=team_id)

        team_data = get_team(team_id)
        if not team_data:
            return {"error": "Team not found"}
        if "team" not in team_data:
            error(logger, "Invalid team data format", team_id=team_id,
                  data=team_data)
            return {"error": "Invalid team data format"}
        
        team_data = team_data["team"]

        logger.debug(f"Fetched team data: {team_data}")

        # Resolve team members from user IDs
        members = []
        user_ids = team_data.get('users', [])
        if user_ids:
            # Import here to avoid circular imports
            from common.utils.firebase import get_user_by_id
            
            for u in user_ids:
                # get_team enriches users[] into dicts; legacy docs carry id
                # strings. Re-fetch either way (the member shape needs email).
                user_id = u.get("id") if isinstance(u, dict) else u
                if not user_id:
                    continue
                try:
                    user_data = get_user_by_id(user_id)
                    if user_data:
                        member = {
                            "id": user_data.get('id'),
                            "name": user_data.get('name', ''),
                            "email": user_data.get('email_address', ''),
                            "profile_image": user_data.get('profile_image', '')
                        }
                        members.append(member)
                except Exception as e:
                    warning(logger, "Error fetching user details", 
                           user_id=user_id, error=str(e))
                    continue

        # Extract GitHub URL from github_links array
        github_url = ""
        github_links = team_data.get('github_links', [])
        if github_links and len(github_links) > 0:
            github_url = github_links[0].get('link', '')

        # Format team data according to API specification
        formatted_team = {
            "id": team_data.get('id'),
            "name": team_data.get('name', ''),
            "description": team_data.get('description', ''),
            "problem_statement": {
                "title": team_data.get('problem_statement', {}).get(
                    'title', ''),
                "description": team_data.get('problem_statement', {}).get(
                    'description', ''),
                "nonprofit": team_data.get('problem_statement', {}).get(
                    'nonprofit', ''),
                "nonprofit_contact": team_data.get(
                    'problem_statement', {}).get('nonprofit_contact', '')
            },
            "members": members,  # Now populated with actual member data
            "github_url": github_url,
            "devpost_url": team_data.get('devpost_link', ''),
            "slack_channel": team_data.get('slack_channel', ''),
            # demo_video_url is the real field written by the team dashboard's
            # DemoVideoEditor / TeamStatusPanel; video_url is filled from it
            # too so judge-side consumers reading either name keep working
            # (Part 9 bug #2 — judges never received the team's demo video
            # because this used to read a phantom `video_url` key that
            # save/edit_team never writes).
            "demo_video_url": team_data.get('demo_video_url', ''),
            "video_url": team_data.get('demo_video_url') or team_data.get('video_url', ''),
            "demo_url": team_data.get('demo_url', ''),
            "technologies": team_data.get('technologies', []),
            "features": team_data.get('features', [])
        }

        logger.debug(f"Formatted team data: {formatted_team}")

        return {"team": formatted_team}

    except Exception as e:
        error(logger, "Error fetching team details",
              team_id=team_id, error=str(e))
        return {"error": "Failed to fetch team details"}


def submit_judge_score(judge_id: str, team_id: str, event_id: str,
                       round_name: str, scores_data: Dict,
                       submitted_at: str = None, feedback: str = None) -> Dict:
    """Submit or update a team score."""
    try:
        debug(logger, "Submitting judge score", judge_id=judge_id,
              team_id=team_id, event_id=event_id, round_name=round_name)

        # Validate score values (1-5 points each)
        required_fields = ['scopeImpact', 'scopeComplexity',
                           'documentationCode', 'documentationEase',
                           'polishWorkRemaining', 'polishCanUseToday',
                           'securityData', 'securityRole'] # We don't include accessibility here as it's optional and a special category prize

        for field in required_fields:
            if field not in scores_data:
                return {"success": False,
                        "error": f"Missing required field: {field}"}

            score_value = scores_data[field]
            if not isinstance(score_value, int) or \
               score_value < 1 or score_value > 5:
                return {"success": False,
                        "error": f"Invalid score for {field}: must be 1-5"}

        # Create score object
        score = JudgeScore.from_api_format(scores_data)
        score.judge_id = judge_id
        score.team_id = team_id
        score.event_id = event_id
        score.round = round_name
        score.is_draft = False
        score.feedback = feedback
        score.submitted_at = (datetime.fromisoformat(
            submitted_at.replace('Z', '+00:00'))
            if submitted_at else datetime.now())

        # Calculate total score
        score.calculate_total_score()

        # Save to database
        saved_score = upsert_judge_score(score)

        return {
            "success": True,
            "message": "Score submitted successfully",
            "score_id": saved_score.id
        }

    except Exception as e:
        error(logger, "Error submitting score",
              judge_id=judge_id, team_id=team_id, error=str(e))
        return {"success": False, "error": "Failed to submit score"}


def get_judge_scores(judge_id: str, event_id: str) -> Dict:
    """Get all scores submitted by a judge for a hackathon."""
    try:
        debug(logger, "Fetching judge scores",
              judge_id=judge_id, event_id=event_id)

        scores = fetch_judge_scores_by_judge_and_event(judge_id, event_id)

        formatted_scores = []
        for score in scores:
            formatted_score = {
                "team_id": score.team_id,
                "round": score.round,
                "feedback": score.feedback,
                "scores": score.to_api_format(),
                "submitted_at": (score.submitted_at.isoformat()
                                 if score.submitted_at else None)
            }
            formatted_scores.append(formatted_score)

        return {"scores": formatted_scores}

    except Exception as e:
        error(logger, "Error fetching judge scores",
              judge_id=judge_id, event_id=event_id, error=str(e))
        return {"scores": [], "error": "Failed to fetch scores"}


def is_judge_assigned_to_team(judge_id: str, team_id: str) -> bool:
    """Check if a judge is assigned to judge a specific team."""
    try:
        debug(logger, "Checking judge assignment",
              judge_id=judge_id, team_id=team_id)

        assignments = fetch_judge_assignments_by_judge_id(judge_id)

        # Check if any assignment matches this team
        for assignment in assignments:
            if assignment.team_id == team_id:
                return True

        return False

    except Exception as e:
        error(logger, "Error checking judge assignment",
              judge_id=judge_id, team_id=team_id, error=str(e))
        return False


def is_judge_assigned_for_round(judge_id: str, event_id: str, team_id: str,
                                round_name: str) -> bool:
    """Assignment check for score/draft WRITES: the judge must hold an
    assignment for this team in THIS event and THIS round. The older
    `is_judge_assigned_to_team` only asks "any event, any round", which let a
    Round-1-only judge post a Round 2 score for a finalist they'd seen in
    Round 1 — and that score counted in the final results."""
    try:
        assignments = fetch_judge_assignments_by_event_and_judge(event_id, judge_id)
        return any(a.team_id == team_id and a.round == round_name for a in assignments)
    except Exception as e:
        error(logger, "Error checking round assignment", judge_id=judge_id,
              team_id=team_id, event_id=event_id, round_name=round_name, error=str(e))
        return False


def save_draft_score(judge_id: str, team_id: str, event_id: str,
                     round_name: str, scores_data: Dict, updated_at: str, feedback: str) -> Dict:
    """Save draft scores (auto-save functionality)."""
    try:
        debug(logger, "Saving draft score", judge_id=judge_id,
              team_id=team_id, event_id=event_id, round_name=round_name)

        # Create draft score object
        score = JudgeScore.from_api_format(scores_data)
        score.judge_id = judge_id
        score.team_id = team_id
        score.event_id = event_id
        score.round = round_name
        score.is_draft = True
        score.feedback = feedback
        score.submitted_at = None
        # Handle updated_at, example: 2025-07-25T15:49:24.680Z
        if updated_at:
            score.updated_at = datetime.fromisoformat(
                updated_at.replace('Z', '+00:00'))
        else:
            score.updated_at = datetime.now()

        # Only calculate total if all scores are present
        required_fields = ['scope_impact', 'scope_complexity',
                           'documentation_code', 'documentation_ease',
                           'polish_work_remaining', 'polish_can_use_today',
                           'security_data', 'security_role']
        if all(getattr(score, field) is not None for field in required_fields):
            score.calculate_total_score()

        # Save to database
        upsert_judge_score(score)

        return {"success": True}

    except Exception as e:
        error(logger, "Error saving draft score",
              judge_id=judge_id, team_id=team_id, error=str(e))
        return {"success": False, "error": "Failed to save draft"}


def get_draft_score(judge_id: str, team_id: str, event_id: str, round_name: str) -> Dict:
    """Get draft scores for a specific judge, team, event, and round."""
    try:
        logger.debug(f"Fetching draft score for judge {judge_id}, team {team_id}, event {event_id}, round {round_name}")

        score = fetch_judge_score(judge_id, team_id, event_id, round_name, is_draft=True)

        if not score:
            return {"draft": None}

        return {
            "draft": {
                "id": score.id,
                "judge_id": score.judge_id,
                "team_id": score.team_id,
                "event_id": score.event_id,
                "round": score.round,
                "scores": score.to_api_format(),
                "total_score": score.total_score,
                "feedback": score.feedback,
                "updated_at": score.updated_at.isoformat() if score.updated_at else None,
                "created_at": score.created_at.isoformat() if score.created_at else None
            }
        }

    except Exception as e:
        error(logger, "Error fetching draft score",
              judge_id=judge_id, team_id=team_id, event_id=event_id,
              error=str(e))
        return {"draft": None, "error": "Failed to fetch draft"}


def _member_ids(team: Dict) -> list:
    """users[] may be enriched dicts or legacy id strings."""
    out = []
    for u in team.get('users', []) or []:
        uid = u.get("id") if isinstance(u, dict) else u
        if uid and isinstance(uid, str):
            out.append(uid)
    return out


def _batch_users_for_teams(teams: list) -> Dict:
    """id -> slim member dict for every member across `teams`, via ONE
    Firestore get_all. Returns None on any failure so callers fall back to
    the per-user lookup (never fail a judge's dashboard over an optimisation)."""
    ids = []
    for team in teams or []:
        for uid in _member_ids(team):
            if uid not in ids:
                ids.append(uid)
    if not ids:
        return {}
    try:
        db = _db()
        refs = [db.collection("users").document(uid) for uid in ids]
        out = {}
        for snap in db.get_all(refs):
            if getattr(snap, "exists", False):
                d = snap.to_dict() or {}
                out[snap.id] = {
                    "id": snap.id,
                    "name": d.get('name', ''),
                    "email": d.get('email_address', ''),
                    "profile_image": d.get('profile_image', ''),
                }
        return out
    except Exception as e:
        warning(logger, "Batch member lookup failed; falling back to per-user reads", error=str(e))
        return None


def format_team_for_judge(team: Dict, score_lookup: Dict = None, nonprofit_id: str = None,
                          nonprofit_name: str = None, users_by_id: Dict = None) -> Dict:
    """Format team data for judge API response. Pass `users_by_id` (from
    `_batch_users_for_teams`) to avoid a Firestore read per member."""
    if score_lookup is None:
        score_lookup = {}

    team_id = team.get('id')

    # Resolve team members from user IDs if present
    members = []
    user_ids = team.get('users', [])
    if user_ids and isinstance(users_by_id, dict):
        for uid in _member_ids(team):
            if uid in users_by_id:
                members.append(dict(users_by_id[uid]))
    elif user_ids:
        # Import here to avoid circular imports
        from common.utils.firebase import get_user_by_id
        
        for u in user_ids:
            # users[] may be enriched dicts or legacy id strings.
            user_id = u.get("id") if isinstance(u, dict) else u
            if not user_id:
                continue
            try:
                user_data = get_user_by_id(user_id)
                if user_data:
                    member = {
                        "id": user_data.get('id'),
                        "name": user_data.get('name', ''),
                        "email": user_data.get('email_address', ''),
                        "profile_image": user_data.get('profile_image', '')
                    }
                    members.append(member)
            except Exception as e:
                warning(logger, "Error fetching user details for team formatting", 
                       user_id=user_id, error=str(e))
                continue

    # Extract GitHub URL from github_links array
    github_url = ""
    github_links = team.get('github_links', [])
    if github_links and len(github_links) > 0:
        github_url = github_links[0].get('link', '')

    # Get score info for this team
    score_key = f"{team_id}_round1"
    score_obj = score_lookup.get(score_key) if score_key in score_lookup else None

    return {
        "id": team_id,
        "name": team.get('name', ''),
        "problem_statement": {
            "nonprofit": nonprofit_name,
            "nonprofit_id": nonprofit_id
        },
        "members": members,  # Now populated with actual member data
        "github_url": github_url,
        "devpost_url": team.get('devpost_link', ''),
        "slack_channel": team.get('slack_channel', ''),
        # See get_team_details' matching comment (Part 9 bug #2): demo_video_url
        # is the real field; video_url is filled from it for legacy readers.
        "demo_video_url": team.get('demo_video_url', ''),
        "video_url": team.get('demo_video_url') or team.get('video_url', ''),
        "demo_time": None,  # Will be overridden for round2
        "judged": score_obj is not None,
        "score": score_obj.total_score if score_obj else None,
        "specialCategoryScores":
        {
            "accessibility": score_obj.accessibility if score_obj else None
        }        
    }


# Judge Assignment Management Functions

def create_judge_assignment(judge_id: str, event_id: str, team_id: str,
                           round_name: str, demo_time: str = None,
                           room: str = None, panel_id: str = None) -> Dict:
    """Create a new judge assignment."""
    try:
        debug(logger, "Creating judge assignment",
              judge_id=judge_id, event_id=event_id, team_id=team_id,
              round_name=round_name)

        assignment = JudgeAssignment()
        assignment.judge_id = judge_id
        assignment.event_id = event_id
        assignment.panel_id = panel_id
        assignment.team_id = team_id
        assignment.round = round_name
        assignment.demo_time = demo_time
        assignment.room = room

        # Don't insert if already exists for a given panel_id, event_id, judge_id, round, and team_id
        current_assignment = fetch_judge_assignment(panel_id, event_id, judge_id, round_name, team_id)
        
        saved_assignment = None
        if current_assignment:
            warning(logger, "Judge assignment already exists",
                    judge_id=judge_id, event_id=event_id, team_id=team_id,
                    round_name=round_name)
            saved_assignment = update_judge_assignment(current_assignment)            
        else:
            saved_assignment = insert_judge_assignment(assignment)

        return {
            "success": True,
            "assignment": {
                "id": saved_assignment.id,
                "judge_id": saved_assignment.judge_id,
                "event_id": saved_assignment.event_id,
                "team_id": saved_assignment.team_id,
                "round": saved_assignment.round,
                "demo_time": saved_assignment.demo_time,
                "room": saved_assignment.room,
                "panel_id": saved_assignment.panel_id,
                "created_at": (saved_assignment.created_at.isoformat()
                               if saved_assignment.created_at else None)
            }
        }

    except Exception as e:
        error(logger, "Error creating judge assignment",
              judge_id=judge_id, event_id=event_id, team_id=team_id,
              error=str(e))
        return {"success": False, "error": "Failed to create assignment"}


def update_judge_assignment_details(assignment_id: str, demo_time: str = None,
                                   room: str = None) -> Dict:
    """Update judge assignment details like demo time and room."""
    try:
        debug(logger, "Updating judge assignment", assignment_id=assignment_id)

        # First fetch the existing assignment. (Part 9 bug #4: this used to
        # call fetch_judge_assignments_by_judge_id("") — an empty judge_id
        # matches no real assignment — so `assignment` was always None and
        # every call 400'd "Assignment not found". fetch_judge_assignment_by_id
        # is a direct doc-get.)
        assignment = fetch_judge_assignment_by_id(assignment_id)

        if not assignment:
            return {"success": False, "error": "Assignment not found"}

        # Update fields
        if demo_time is not None:
            assignment.demo_time = demo_time
        if room is not None:
            assignment.room = room

        updated_assignment = update_judge_assignment(assignment)

        return {
            "success": True,
            "assignment": {
                "id": updated_assignment.id,
                "judge_id": updated_assignment.judge_id,
                "event_id": updated_assignment.event_id,
                "team_id": updated_assignment.team_id,
                "round": updated_assignment.round,
                "demo_time": updated_assignment.demo_time,
                "room": updated_assignment.room,
                "updated_at": (updated_assignment.updated_at.isoformat()
                               if updated_assignment.updated_at else None)
            }
        }

    except Exception as e:
        error(logger, "Error updating judge assignment",
              assignment_id=assignment_id, error=str(e))
        return {"success": False, "error": "Failed to update assignment"}


def remove_judge_assignment(assignment_id: str) -> Dict:
    """Remove a judge assignment."""
    try:
        debug(logger, "Removing judge assignment", assignment_id=assignment_id)

        result = delete_judge_assignment(assignment_id)

        if result:
            return {"success": True, "message": "Assignment removed successfully"}
        return {"success": False, "error": "Assignment not found"}

    except Exception as e:
        error(logger, "Error removing judge assignment",
              assignment_id=assignment_id, error=str(e))
        return {"success": False, "error": "Failed to remove assignment"}


def get_individual_judge_score(judge_id: str, team_id: str, event_id: str,
                              round_name: str, is_draft: bool = False) -> Dict:
    """Get a specific judge score."""
    try:
        debug(logger, "Fetching individual judge score",
              judge_id=judge_id, team_id=team_id, event_id=event_id,
              round_name=round_name, is_draft=is_draft)

        score = fetch_judge_score(judge_id, team_id, event_id, round_name, is_draft)

        if not score:
            return {"score": None}

        return {
            "score": {
                "id": score.id,
                "judge_id": score.judge_id,
                "team_id": score.team_id,
                "event_id": score.event_id,
                "round": score.round,
                "scores": score.to_api_format(),
                "total_score": score.total_score,
                "is_draft": score.is_draft,
                "feedback": score.feedback,
                "submitted_at": score.submitted_at.isoformat() if score.submitted_at else None,
                "created_at": score.created_at.isoformat() if score.created_at else None,
                "updated_at": score.updated_at.isoformat() if score.updated_at else None
            }
        }

    except Exception as e:
        error(logger, "Error fetching individual judge score",
              judge_id=judge_id, team_id=team_id, event_id=event_id,
              error=str(e))
        return {"score": None, "error": "Failed to fetch score"}


# Judge Panel Management Functions

def get_event_judge_panels(event_id: str) -> Dict:
    """Get all judge panels for an event."""
    try:
        debug(logger, "Fetching judge panels for event", event_id=event_id)

        panels = fetch_judge_panels_by_event(event_id)

        logger.debug(f"Fetched {len(panels)} panels for event {event_id}")        

        formatted_panels = []
        for panel in panels:
            formatted_panel = {
                "id": panel.id,
                "event_id": panel.event_id,
                "panel_name": panel.panel_name,
                "panel_id": panel.panel_id,
                "room": panel.room,                
                "created_at": panel.created_at.isoformat() if panel.created_at else None
            }
            formatted_panels.append(formatted_panel)

        logger.debug(f"Formatted {len(formatted_panels)} panels for event {event_id}")
        logger.debug(f"Formatted panels: {formatted_panels}")
        return {"panels": formatted_panels}

    except Exception as e:
        error(logger, "Error fetching judge panels",
              event_id=event_id, error=str(e))
        # print stack trace
        import traceback
        traceback.print_exc()
        return {"panels": [], "error": "Failed to fetch panels"}


def get_judge_event_details(judge_id: str, event_id: str) -> Dict:
    # Use get_volunteer_from_db_by_user_id_volunteer_type_and_event_id
    """Get all information about a specific judge's event."""
    volunteer = get_volunteer_from_db_by_user_id_volunteer_type_and_event_id(
        user_id=judge_id, volunteer_type="judge", event_id=event_id
    )
    if not volunteer:
        logger.info(f"Judge not found for user_id {judge_id} and event_id {event_id}")
        return {"error": "Judge not found"}, 404

    logger.debug(f"Fetched judge details: {volunteer}")
    return {
        "judge": {
            "id": volunteer["id"],
            "name": volunteer["name"],
            "event_id": volunteer["event_id"],            
            "slack_user_id": volunteer["slack_user_id"] if "slack_user_id" in volunteer else None,           
        }
    }


def get_judge_assignments_for_panel(panel_id: str) -> Dict:
    """Get all judge assignments for a specific panel."""
    try:
        logger.debug(f"Fetching judge assignments for panel {panel_id}")
        # Use fetch_judge_assignments_by_panel_id
        assignments = fetch_judge_assignments_by_panel_id(panel_id)
        if not assignments:
            return {"assignments": [], "error": "No assignments found for this panel"}
        formatted_assignments = []
        for assignment in assignments:
            formatted_assignment = {
                "id": assignment.id,
                "judge_id": assignment.judge_id,
                "event_id": assignment.event_id,
                "team_id": assignment.team_id,
                "round": assignment.round,
                "demo_time": assignment.demo_time,
                "room": assignment.room,
                "created_at": assignment.created_at.isoformat() if assignment.created_at else None
            }
            formatted_assignments.append(formatted_assignment)
        return {"assignments": formatted_assignments}
    except Exception as e:
        error(logger, "Error fetching judge assignments for panel",
              panel_id=panel_id, error=str(e))
        # print stack trace
        import traceback
        traceback.print_exc()

        return {"assignments": [], "error": "Failed to fetch assignments for panel"}       


def create_judge_panel(event_id: str, panel_name: str, room: str,
                      panel_id: str) -> Dict:
    """Create a new judge panel."""
    try:
        debug(logger, "Creating judge panel",
              event_id=event_id, panel_name=panel_name, room=room)

        panel = JudgePanel()
        panel.event_id = event_id
        panel.panel_name = panel_name
        panel.room = room
        panel.panel_id = panel_id

        saved_panel = insert_judge_panel(panel)

        return {
            "success": True,
            "panel": {
                "id": saved_panel.id,
                "event_id": saved_panel.event_id,
                "panel_name": saved_panel.panel_name,
                "panel_id": saved_panel.panel_id,
                "room": saved_panel.room,                
                "created_at": saved_panel.created_at.isoformat() if saved_panel.created_at else None
            }
        }

    except Exception as e:
        error(logger, "Error creating judge panel",
              event_id=event_id, panel_name=panel_name, error=str(e))
        return {"success": False, "error": "Failed to create panel"}


def update_judge_panel_details(panel_id: str, panel_name: str = None,
                              room: str = None) -> Dict:
    """Update judge panel details."""
    try:
        debug(logger, "Updating judge panel", panel_id=panel_id)

        # Get existing panel (this is inefficient but works with current interface)
        panel = fetch_judge_panel(panel_id)  # Would need event_id in real implementation
        
        if not panel:
            return {"success": False, "error": "Panel not found"}

        # Update fields
        if panel_name is not None:
            panel.panel_name = panel_name
        if room is not None:
            panel.room = room        

        updated_panel = update_judge_panel(panel)

        return {
            "success": True,
            "panel": {
                "id": updated_panel.id,
                "event_id": updated_panel.event_id,
                "panel_name": updated_panel.panel_name,
                "room": updated_panel.room                
            }
        }

    except Exception as e:
        error(logger, "Error updating judge panel",
              panel_id=panel_id, error=str(e))
        return {"success": False, "error": "Failed to update panel"}


def remove_judge_panel(panel_id: str) -> Dict:
    """Remove a judge panel."""
    try:
        debug(logger, "Removing judge panel", panel_id=panel_id)

        result = delete_judge_panel(panel_id)

        if result:
            return {"success": True, "message": "Panel removed successfully"}
        return {"success": False, "error": "Panel not found"}

    except Exception as e:
        error(logger, "Error removing judge panel",
              panel_id=panel_id, error=str(e))
        return {"success": False, "error": "Failed to remove panel"}


def get_bulk_judge_scores(event_id: str, round_name: str) -> Dict:
    """Get all judge scores for a specific event and round."""
    try:
        debug(logger, "Fetching bulk judge scores",
              event_id=event_id, round_name=round_name)

        scores = fetch_judge_scores_by_event_and_round(event_id, round_name)

        # Get unique team IDs and judge IDs for batch processing
        team_ids = list(set([score.team_id for score in scores]))
        judge_ids = list(set([score.judge_id for score in scores]))

        debug(logger, "Found scores for bulk retrieval",
              total_scores=len(scores), 
              unique_teams=len(team_ids), 
              unique_judges=len(judge_ids))

        # Get team details in batch if needed
        teams_data = {}
        if team_ids:
            try:
                teams_batch = get_teams_batch({"team_ids": team_ids})
                for team in teams_batch:
                    teams_data[team.get('id')] = {
                        "name": team.get('name', ''),
                        "team_number": team.get('team_number', '')
                    }
            except Exception as e:
                warning(logger, "Error fetching teams batch", error=str(e))

        # Get judge details in batch
        judges_data = {}
        for judge_id in judge_ids:
            try:
                # Get basic judge info
                judge_info = get_volunteer_from_db_by_user_id_volunteer_type_and_event_id(
                    judge_id, "judge", event_id
                )
                if judge_info:
                    judges_data[judge_id] = {
                        "name": judge_info.get('name', ''),
                        "id": judge_id
                    }
            except Exception as e:
                warning(logger, "Error fetching judge details", 
                       judge_id=judge_id, error=str(e))

        # Format the scores
        formatted_scores = []
        for score in scores:
            logger.info(f"Processing score: {score.id} for team {score.team_id} by judge {score.judge_id}")
            formatted_score = {
                "id": score.id,
                "judge_id": score.judge_id,
                "judge_name": judges_data.get(score.judge_id, {}).get('name', 'Unknown'),
                "team_id": score.team_id,
                "team_name": teams_data.get(score.team_id, {}).get('name', 'Unknown'),
                "team_number": teams_data.get(score.team_id, {}).get('team_number', ''),
                "event_id": score.event_id,
                "round": score.round,
                "total_score": score.total_score,
                "scores": score.to_api_format(),
                "feedback": score.feedback,
                "submitted_at": score.submitted_at.isoformat() if score.submitted_at else None,
                "created_at": score.created_at.isoformat() if score.created_at else None
            }
            formatted_scores.append(formatted_score)

        # Sort by team name, then judge name for consistent ordering
        formatted_scores.sort(key=lambda x: (x['team_name'], x['judge_name']))

        # Create summary statistics
        summary = {
            "total_scores": len(formatted_scores),
            "unique_teams": len(team_ids),
            "unique_judges": len(judge_ids),
            "event_id": event_id,
            "round": round_name,
            "average_score": round(sum(s.total_score for s in scores) / len(scores), 2) if scores else 0
        }

        return {
            "scores": formatted_scores,
            "summary": summary
        }

    except Exception as e:
        error(logger, "Error fetching bulk judge scores",
              event_id=event_id, round_name=round_name, error=str(e))
        import traceback
        traceback.print_exc()
        return {"scores": [], "summary": {}, "error": "Failed to fetch bulk scores"}


def get_bulk_judge_details(event_id: str) -> Dict:
    """Get all judge details for a specific event in one request."""
    try:
        logger.debug("Fetching bulk judge details for event %s", event_id)

        # Get all judges for the event
        judges_result = get_volunteer_from_db_by_event(event_id, "judge")

        # Get all assignments for the event
        assignments = fetch_judge_assignments_by_event_id(event_id)

        # Get all of the scores for the event. (Part 9 bug #3: this called an
        # undefined name, fetch_judge_scores_by_event — the correctly-named
        # fetch_judge_scores_by_event_id was imported but never used here —
        # so every call silently NameError'd into the outer except and this
        # function always returned an empty judges list.)
        scores = fetch_judge_scores_by_event_id(event_id)
        

        if "error" in judges_result:
            error(logger, "Error fetching judges for event", event_id=event_id)
            return {"judges": [], "error": "Failed to fetch judges for event"}

        judges_data = judges_result.get("data", [])

        if not judges_data:
            logger.debug("No judges found for event %s", event_id)
            return {"judges": [], "total_count": 0}

        formatted_judges = []
        for judge in judges_data:
            formatted_judge = {
                "id": judge.get("id", ""),
                "user_id": judge.get("user_id", ""),
                "name": judge.get("name", ""),
                "email": judge.get("email", ""),
                "event_id": judge.get("event_id", event_id),
                "slack_user_id": judge.get("slack_user_id", ""),
                "volunteer_type": judge.get("volunteer_type", "judge"),
                "created_at": judge.get("created_at"),
                "updated_at": judge.get("updated_at")
            }
            formatted_judges.append(formatted_judge)

        logger.debug("Successfully fetched %d judges for event %s", 
                    len(formatted_judges), event_id)

        return {
            "judges": formatted_judges,
            "total_count": len(formatted_judges),
            "event_id": event_id
        }

    except Exception as e:
        error(logger, "Error fetching bulk judge details",
              event_id=event_id, error=str(e))
        import traceback
        traceback.print_exc()
        return {"judges": [], "total_count": 0, "error": "Failed to fetch judge details"}


# ---------------------------------------------------------------------------
# Round locks, judge progress, bulk assignments (Oct 2026)
#
# Locks live in their own collection (`judging_round_locks`, doc id
# `<event_id>__<round>`) and are read with ONE uncached doc-get on every
# score/draft write, so flipping the lock takes effect immediately — the
# hackathon doc is behind a 10-minute cache and would have lagged a live
# awards ceremony. Writes go through `common.utils.firebase.get_db()` (the
# same client the submissions service uses; MockFirestore under
# ENVIRONMENT=test).
# ---------------------------------------------------------------------------

JUDGING_ROUNDS = ("round1", "round2")
JUDGING_LOCKS_COLLECTION = "judging_round_locks"
BULK_ASSIGN_MAX_JUDGES = 100
BULK_ASSIGN_MAX_TEAMS = 200


def _db():
    # Lazy import: common.utils.firebase builds Firebase credentials at import
    # time; keeping it inside the function keeps judging_service importable in
    # unit tests that patch it.
    from common.utils.firebase import get_db as _get_db
    return _get_db()


def _lock_doc_id(event_id: str, round_name: str) -> str:
    return f"{event_id}__{round_name}"


def _empty_lock(event_id: str, round_name: str) -> Dict:
    return {
        "event_id": event_id,
        "round": round_name,
        "locked": False,
        "locked_at": None,
        "locked_by": None,
        "locked_by_name": None,
        "updated_at": None,
    }


def _normalize_lock(raw: Dict, event_id: str, round_name: str) -> Dict:
    lock = _empty_lock(event_id, round_name)
    if isinstance(raw, dict):
        lock["locked"] = bool(raw.get("locked", False))
        for key in ("locked_at", "locked_by", "locked_by_name", "updated_at"):
            lock[key] = raw.get(key)
    return lock


def get_round_lock(event_id: str, round_name: str) -> Dict:
    """Current lock state for one round. Missing doc ⇒ unlocked."""
    try:
        snap = _db().collection(JUDGING_LOCKS_COLLECTION).document(
            _lock_doc_id(event_id, round_name)).get()
        raw = snap.to_dict() if getattr(snap, "exists", False) else None
        return _normalize_lock(raw, event_id, round_name)
    except Exception as e:
        # Fail OPEN on a read error: a flaky lock read must never block a judge
        # from scoring. Admins see the lock state on their panel and can retry.
        warning(logger, "Error reading judging round lock",
                event_id=event_id, round_name=round_name, error=str(e))
        return _empty_lock(event_id, round_name)


PUBLIC_LOCK_FIELDS = ("event_id", "round", "locked", "locked_at", "updated_at")


def public_lock_view(lock: Dict) -> Dict:
    """What a judge (any signed-in user) may see: never the locking admin's
    PropelAuth id or name — only whether/when the round closed."""
    return {k: lock.get(k) for k in PUBLIC_LOCK_FIELDS}


def get_round_locks(event_id: str, public: bool = False) -> Dict:
    """`{"round1": lock, "round2": lock}`. `public=True` strips the actor
    fields for judge-facing responses; admins get the full lock."""
    locks = {r: get_round_lock(event_id, r) for r in JUDGING_ROUNDS}
    if public:
        return {r: public_lock_view(lock) for r, lock in locks.items()}
    return locks


def is_round_locked(event_id: str, round_name: str) -> bool:
    return bool(get_round_lock(event_id, round_name).get("locked"))


def set_round_lock(event_id: str, round_name: str, locked: bool,
                   actor_id: str = None, actor_name: str = None) -> Dict:
    """Lock or unlock scoring for a round. Idempotent; returns the new lock."""
    if round_name not in JUDGING_ROUNDS:
        return {"success": False, "error": "invalid_round"}
    if not isinstance(locked, bool):
        return {"success": False, "error": "locked_must_be_boolean"}
    try:
        now_iso = datetime.now().astimezone().isoformat()
        doc = {
            "event_id": event_id,
            "round": round_name,
            "locked": locked,
            "updated_at": now_iso,
        }
        if locked:
            doc.update({
                "locked_at": now_iso,
                "locked_by": actor_id,
                "locked_by_name": actor_name,
            })
        else:
            doc.update({"locked_at": None, "locked_by": None, "locked_by_name": None})

        _db().collection(JUDGING_LOCKS_COLLECTION).document(
            _lock_doc_id(event_id, round_name)).set(doc, merge=True)

        try:
            from common.utils.slack import send_slack_audit
            send_slack_audit(
                action="judging_round_lock",
                message=f"{'Locked' if locked else 'Unlocked'} {round_name} scoring for {event_id}",
                payload={"event_id": event_id, "round": round_name, "locked": locked,
                         "by": actor_name or actor_id},
            )
        except Exception as e:  # audit is best-effort
            warning(logger, "judging lock audit failed", error=str(e))

        return {"success": True, "lock": _normalize_lock(doc, event_id, round_name)}
    except Exception as e:
        error(logger, "Error setting judging round lock",
              event_id=event_id, round_name=round_name, error=str(e))
        return {"success": False, "error": "Failed to update lock"}


class JudgingReadError(RuntimeError):
    """A db fetcher reported a failure instead of rows."""


def _rows(result) -> list:
    """The event-scoped db fetchers return {"data": [...]} — or
    {"data": [], "error": ...} when the read FAILED. Treating a failed read as
    "no rows" would make bulk-assign re-insert every pair (and prune nothing)
    and would show an admin an empty progress board mid-round, so raise
    instead; callers fall through to their error paths. Bare lists are
    tolerated. Only dict rows are returned."""
    if isinstance(result, dict):
        if result.get("error"):
            raise JudgingReadError(str(result.get("error")))
        result = result.get("data") or []
    return [row for row in (result or []) if isinstance(row, dict)]


def _judge_directory(event_id: str) -> Dict:
    """user_id -> {name, email} for every judge application on the event
    (one query), so progress rows carry names without N lookups."""
    directory = {}
    try:
        for judge in _rows(get_volunteer_from_db_by_event(event_id, "judge", admin=True)):
            uid = judge.get("user_id")
            if not uid:
                continue
            directory[uid] = {
                "name": judge.get("name") or "",
                "email": judge.get("email") or "",
                "is_selected": bool(judge.get("isSelected")),
            }
    except Exception as e:
        # Names are decoration on the progress board; never fail it over them.
        warning(logger, "Judge directory lookup failed", event_id=event_id, error=str(e))
    return directory


def get_judging_progress(event_id: str, round_name: str) -> Dict:
    """Judge × team scoring progress for one round, computed server-side in
    three queries (assignments, scores, judge applications) + one team batch.

    A judge/team cell is `submitted` when a non-draft score doc exists,
    `draft` when only a draft exists, else `missing`. Drafts that have since
    been submitted are NOT counted as drafts."""
    if round_name not in JUDGING_ROUNDS:
        return {"error": "invalid_round"}
    try:
        assignments = [a for a in _rows(fetch_judge_assignments_by_event_id(event_id))
                       if a.get("round") == round_name and a.get("judge_id") and a.get("team_id")]
        scores = [s for s in _rows(fetch_judge_scores_by_event_id(event_id))
                  if s.get("round") == round_name and s.get("judge_id") and s.get("team_id")]

        submitted = {}  # (judge_id, team_id) -> submitted_at iso|None
        drafts = {}     # (judge_id, team_id) -> updated_at iso|None
        for s in scores:
            key = (s["judge_id"], s["team_id"])
            if s.get("is_draft"):
                drafts[key] = _iso(s.get("updated_at"))
            else:
                submitted[key] = _iso(s.get("submitted_at") or s.get("updated_at"))

        judges_by_id = {}
        teams_by_id = {}
        for a in assignments:
            j = judges_by_id.setdefault(a["judge_id"], {
                "judge_id": a["judge_id"], "assigned_team_ids": [],
                "submitted_team_ids": [], "draft_team_ids": [], "missing_team_ids": [],
                "last_submitted_at": None, "panel_ids": set(),
            })
            if a["team_id"] not in j["assigned_team_ids"]:
                j["assigned_team_ids"].append(a["team_id"])
            if a.get("panel_id"):
                j["panel_ids"].add(a["panel_id"])
            t = teams_by_id.setdefault(a["team_id"], {
                "team_id": a["team_id"], "assigned_judge_ids": [], "submitted_judge_ids": [],
            })
            if a["judge_id"] not in t["assigned_judge_ids"]:
                t["assigned_judge_ids"].append(a["judge_id"])

        for jid, j in judges_by_id.items():
            for tid in j["assigned_team_ids"]:
                key = (jid, tid)
                if key in submitted:
                    j["submitted_team_ids"].append(tid)
                    teams_by_id[tid]["submitted_judge_ids"].append(jid)
                    ts = submitted[key]
                    if ts and (j["last_submitted_at"] is None or ts > j["last_submitted_at"]):
                        j["last_submitted_at"] = ts
                elif key in drafts:
                    j["draft_team_ids"].append(tid)
                else:
                    j["missing_team_ids"].append(tid)

        # Names
        directory = _judge_directory(event_id)
        team_names = {}
        team_ids = list(teams_by_id.keys())
        if team_ids:
            try:
                for team in get_teams_batch({"team_ids": team_ids}):
                    team_names[team.get("id")] = team.get("name", "")
            except Exception as e:
                warning(logger, "Progress team batch failed", event_id=event_id, error=str(e))

        judges_out = []
        for j in judges_by_id.values():
            info_ = directory.get(j["judge_id"], {})
            assigned_n = len(j["assigned_team_ids"])
            submitted_n = len(j["submitted_team_ids"])
            judges_out.append({
                "judge_id": j["judge_id"],
                "name": info_.get("name") or "Unknown judge",
                "email": info_.get("email") or "",
                "panel_ids": sorted(j["panel_ids"]),
                "assigned_team_ids": j["assigned_team_ids"],
                "submitted_team_ids": j["submitted_team_ids"],
                "draft_team_ids": j["draft_team_ids"],
                "missing_team_ids": j["missing_team_ids"],
                "assigned": assigned_n,
                "submitted": submitted_n,
                "complete": assigned_n > 0 and submitted_n >= assigned_n,
                "last_submitted_at": j["last_submitted_at"],
            })
        judges_out.sort(key=lambda j: (j["complete"], -(j["assigned"] - j["submitted"]), j["name"].lower()))

        teams_out = []
        for t in teams_by_id.values():
            teams_out.append({
                "team_id": t["team_id"],
                "name": team_names.get(t["team_id"], ""),
                "assigned_judge_ids": t["assigned_judge_ids"],
                "submitted_judge_ids": t["submitted_judge_ids"],
                "assigned": len(t["assigned_judge_ids"]),
                "submitted": len(t["submitted_judge_ids"]),
            })
        teams_out.sort(key=lambda t: (t["name"] or "").lower())

        # Scores from judges who no longer hold an assignment (admin re-paneled)
        # still exist in the collection; surface the count so nobody is confused
        # by a Results total that is higher than the matrix.
        assigned_keys = {(a["judge_id"], a["team_id"]) for a in assignments}
        orphan_submitted = len([k for k in submitted if k not in assigned_keys])

        return {
            "event_id": event_id,
            "round": round_name,
            "lock": get_round_lock(event_id, round_name),
            "judges": judges_out,
            "teams": teams_out,
            "summary": {
                "judges_total": len(judges_out),
                "judges_complete": len([j for j in judges_out if j["complete"]]),
                "assignments_total": len(assigned_keys),
                "submitted_total": len([k for k in assigned_keys if k in submitted]),
                "drafts_total": len([k for k in assigned_keys if k in drafts and k not in submitted]),
                "orphan_submitted": orphan_submitted,
            },
        }
    except Exception as e:
        error(logger, "Error computing judging progress",
              event_id=event_id, round_name=round_name, error=str(e))
        import traceback
        traceback.print_exc()
        return {"error": "Failed to compute judging progress"}


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def bulk_create_judge_assignments(event_id: str, round_name: str, panel_id: str,
                                  judge_ids: list, team_ids: list, room: str = None,
                                  demo_times: Dict = None, replace: bool = False) -> Dict:
    """Create every missing (judge, team) assignment for a panel in one call.

    Idempotent: pairs that already exist for (panel_id, event_id, round) are
    left alone. With `replace=True` the panel's assignments for this round are
    made to EQUAL the cross product — pairs no longer in it are deleted (the
    admin removed a judge/finalist and re-saved). Scores are never touched."""
    if round_name not in JUDGING_ROUNDS:
        return {"success": False, "error": "invalid_round"}
    if not isinstance(panel_id, str) or not panel_id:
        return {"success": False, "error": "panel_id_required"}
    if not isinstance(replace, bool):
        return {"success": False, "error": "replace_must_be_boolean"}
    if not isinstance(judge_ids, list) or not isinstance(team_ids, list):
        return {"success": False, "error": "judge_ids_and_team_ids_must_be_lists"}
    if not all(isinstance(j, str) and j for j in judge_ids) or \
            not all(isinstance(t, str) and t for t in team_ids):
        return {"success": False, "error": "ids_must_be_non_empty_strings"}
    judge_ids = list(dict.fromkeys(judge_ids))
    team_ids = list(dict.fromkeys(team_ids))
    if len(judge_ids) > BULK_ASSIGN_MAX_JUDGES or len(team_ids) > BULK_ASSIGN_MAX_TEAMS:
        return {"success": False, "error": "too_many_items"}
    if not judge_ids or not team_ids:
        # An empty cross product with replace=True would wipe the panel; a
        # frontend bug must never be able to do that. Removing single judges
        # or teams goes through the explicit DELETE route instead.
        return {"success": False, "error": "empty_cross_product"}
    if room is not None and not isinstance(room, str):
        return {"success": False, "error": "room_must_be_string"}
    if demo_times is None:
        demo_times = {}
    if not isinstance(demo_times, dict) or not all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in demo_times.items()):
        return {"success": False, "error": "demo_times_must_map_team_id_to_iso_string"}

    try:
        existing = [a for a in _rows(fetch_judge_assignments_by_event_id(event_id))
                    if a.get("round") == round_name and a.get("panel_id") == panel_id]
        # Every doc per pair (duplicates can exist from the old per-pair POSTs).
        existing_by_pair = {}
        for a in existing:
            existing_by_pair.setdefault((a.get("judge_id"), a.get("team_id")), []).append(a)

        wanted = {(j, t) for j in judge_ids for t in team_ids}
        created, kept, updated, removed = 0, 0, 0, 0
        for (judge_id, team_id) in sorted(wanted):
            docs = existing_by_pair.get((judge_id, team_id)) or []
            if docs:
                current = docs[0]
                kept += 1
                # Keep room/demo_time in sync when the admin changed them.
                new_room = room if room is not None else current.get("room")
                new_demo = demo_times.get(team_id, current.get("demo_time"))
                if new_room != current.get("room") or new_demo != current.get("demo_time"):
                    assignment = JudgeAssignment.deserialize({**current, "room": new_room, "demo_time": new_demo})
                    update_judge_assignment(assignment)
                    updated += 1
                if replace:
                    # Collapse duplicates of a wanted pair down to one doc.
                    for dup in docs[1:]:
                        if dup.get("id"):
                            delete_judge_assignment(dup["id"])
                            removed += 1
                continue
            assignment = JudgeAssignment()
            assignment.judge_id = judge_id
            assignment.event_id = event_id
            assignment.team_id = team_id
            assignment.round = round_name
            assignment.panel_id = panel_id
            assignment.room = room
            assignment.demo_time = demo_times.get(team_id)
            insert_judge_assignment(assignment)
            created += 1

        if replace:
            for pair, docs in existing_by_pair.items():
                if pair in wanted:
                    continue
                for a in docs:
                    if a.get("id"):
                        delete_judge_assignment(a["id"])
                        removed += 1

        return {"success": True, "created": created, "kept": kept, "updated": updated,
                "removed": removed, "total": len(wanted)}
    except Exception as e:
        error(logger, "Error bulk-creating judge assignments",
              event_id=event_id, round_name=round_name, panel_id=panel_id, error=str(e))
        import traceback
        traceback.print_exc()
        return {"success": False, "error": "Failed to create assignments"}

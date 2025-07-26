from typing import Dict
from datetime import datetime

from common.log import get_logger, debug, warning, error
from db.db import (
    fetch_judge_assignments_by_judge_id,
    fetch_judge_assignments_by_event_and_judge,
    fetch_judge_scores_by_judge_and_event,
    fetch_judge_score,
    insert_judge_assignment,
    update_judge_assignment,
    delete_judge_assignment,
    fetch_judge_panels_by_event,
    insert_judge_panel,
    update_judge_panel,
    delete_judge_panel,
    upsert_judge_score
)
from model.judge_assignment import JudgeAssignment
from model.judge_score import JudgeScore
from model.judge_panel import JudgePanel
from api.messages.messages_service import (
    get_single_hackathon_event,
    get_team,
    get_teams_batch
)

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

        for team in teams_data:
            team_id = team.get('id')

            # Get assignment details for demo scheduling
            assignment = next(
                (a for a in assignments if a.team_id == team_id), None)

            team_data = format_team_for_judge(team, score_lookup)

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
            "round2_teams": round2_teams
        }

    except Exception as e:
        error(logger, "Error fetching judge teams",
              judge_id=judge_id, event_id=event_id, error=str(e))
        # print stack trace
        import traceback
        traceback.print_exc()
        return {"round1_teams": [], "round2_teams": [],
                "error": "Failed to fetch teams"}


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
            
            for user_id in user_ids:
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
            "video_url": team_data.get('video_url', ''),
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
                       submitted_at: str = None) -> Dict:
    """Submit or update a team score."""
    try:
        debug(logger, "Submitting judge score", judge_id=judge_id,
              team_id=team_id, event_id=event_id, round_name=round_name)

        # Validate score values (1-5 points each)
        required_fields = ['scopeImpact', 'scopeComplexity',
                           'documentationCode', 'documentationEase',
                           'polishWorkRemaining', 'polishCanUseToday',
                           'securityData', 'securityRole']

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


def save_draft_score(judge_id: str, team_id: str, event_id: str,
                     round_name: str, scores_data: Dict, updated_at: str) -> Dict:
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


def format_team_for_judge(team: Dict, score_lookup: Dict = None) -> Dict:
    """Format team data for judge API response."""
    if score_lookup is None:
        score_lookup = {}

    team_id = team.get('id')

    # Resolve team members from user IDs if present
    members = []
    user_ids = team.get('users', [])
    if user_ids:
        # Import here to avoid circular imports
        from common.utils.firebase import get_user_by_id
        
        for user_id in user_ids:
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

    return {
        "id": team_id,
        "name": team.get('name', ''),
        "problem_statement": {
            "title": team.get('problem_statement', {}).get('title', ''),
            "nonprofit": team.get('problem_statement', {}).get(
                'nonprofit', '')
        },
        "members": members,  # Now populated with actual member data
        "github_url": github_url,
        "devpost_url": team.get('devpost_link', ''),
        "video_url": team.get('video_url', ''),
        "demo_time": None,  # Will be overridden for round2
        "judged": f"{team_id}_round1" in score_lookup,
        "score": (score_lookup.get(f"{team_id}_round1").total_score
                  if f"{team_id}_round1" in score_lookup else None)
    }


# Judge Assignment Management Functions

def create_judge_assignment(judge_id: str, event_id: str, team_id: str,
                           round_name: str, demo_time: str = None,
                           room: str = None) -> Dict:
    """Create a new judge assignment."""
    try:
        debug(logger, "Creating judge assignment",
              judge_id=judge_id, event_id=event_id, team_id=team_id,
              round_name=round_name)

        assignment = JudgeAssignment()
        assignment.judge_id = judge_id
        assignment.event_id = event_id
        assignment.team_id = team_id
        assignment.round = round_name
        assignment.demo_time = demo_time
        assignment.room = room

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

        # First fetch the existing assignment
        # This is inefficient but works with current db interface
        assignments = fetch_judge_assignments_by_judge_id("")
        assignment = None
        for a in assignments:
            if a.id == assignment_id:
                assignment = a
                break

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

        formatted_panels = []
        for panel in panels:
            formatted_panel = {
                "id": panel.id,
                "event_id": panel.event_id,
                "panel_name": panel.panel_name,
                "room": panel.room,
                "judge_ids": panel.judge_ids,
                "created_at": panel.created_at.isoformat() if panel.created_at else None
            }
            formatted_panels.append(formatted_panel)

        return {"panels": formatted_panels}

    except Exception as e:
        error(logger, "Error fetching judge panels",
              event_id=event_id, error=str(e))
        return {"panels": [], "error": "Failed to fetch panels"}


def create_judge_panel(event_id: str, panel_name: str, room: str,
                      judge_ids: list) -> Dict:
    """Create a new judge panel."""
    try:
        debug(logger, "Creating judge panel",
              event_id=event_id, panel_name=panel_name, room=room)

        panel = JudgePanel()
        panel.event_id = event_id
        panel.panel_name = panel_name
        panel.room = room
        panel.judge_ids = judge_ids

        saved_panel = insert_judge_panel(panel)

        return {
            "success": True,
            "panel": {
                "id": saved_panel.id,
                "event_id": saved_panel.event_id,
                "panel_name": saved_panel.panel_name,
                "room": saved_panel.room,
                "judge_ids": saved_panel.judge_ids,
                "created_at": saved_panel.created_at.isoformat() if saved_panel.created_at else None
            }
        }

    except Exception as e:
        error(logger, "Error creating judge panel",
              event_id=event_id, panel_name=panel_name, error=str(e))
        return {"success": False, "error": "Failed to create panel"}


def update_judge_panel_details(panel_id: str, panel_name: str = None,
                              room: str = None, judge_ids: list = None) -> Dict:
    """Update judge panel details."""
    try:
        debug(logger, "Updating judge panel", panel_id=panel_id)

        # Get existing panel (this is inefficient but works with current interface)
        panels = fetch_judge_panels_by_event("")  # Would need event_id in real implementation
        panel = None
        for p in panels:
            if p.id == panel_id:
                panel = p
                break

        if not panel:
            return {"success": False, "error": "Panel not found"}

        # Update fields
        if panel_name is not None:
            panel.panel_name = panel_name
        if room is not None:
            panel.room = room
        if judge_ids is not None:
            panel.judge_ids = judge_ids

        updated_panel = update_judge_panel(panel)

        return {
            "success": True,
            "panel": {
                "id": updated_panel.id,
                "event_id": updated_panel.event_id,
                "panel_name": updated_panel.panel_name,
                "room": updated_panel.room,
                "judge_ids": updated_panel.judge_ids
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

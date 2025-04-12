import uuid
import logging
from datetime import datetime
from db.db import get_db
from api.messages.messages_service import (
    get_propel_user_details_by_id,
    get_user_doc_reference,
    get_problem_statement_from_id_old,
    get_single_npo,
    validate_github_username,
    get_hackathon_by_event_id,
    get_teams_list,
    clear_cache,
    create_github_repo,
    create_slack_channel,
    invite_user_to_channel,
    send_slack,
    send_slack_audit
)

logger = logging.getLogger("myapp")

def queue_team(propel_user_id, json):
    """
    Create a team and queue it for nonprofit pairing
    - Save to teams collection with status IN_REVIEW
    - Set active status to False
    - Send Slack notification that team is in queue
    - Do not create GitHub repo or other resources yet
    """
    send_slack_audit(action="queue_team", message="Queueing", payload=json)

    _, user_id, _, _, name, _ = get_propel_user_details_by_id(propel_user_id)
    slack_user_id = user_id
    
    root_slack_user_id = slack_user_id.replace("oauth2|slack|T1Q7936BH-", "")
    user = get_user_doc_reference(root_slack_user_id)
    
    db = get_db()
    logger.debug("Team Queue")
    logger.debug(json)
    
    doc_id = uuid.uuid1().hex  # Generate a new team id
    team_name = json["name"]
    slack_channel = json["slackChannel"]
    hackathon_event_id = json["eventId"]
    comments = json.get("comments", "")
    
    # Store nonprofit rankings if provided
    nonprofit_rankings = json.get("nonprofitRankings", [])
    
    github_username = json["githubUsername"]
    if not validate_github_username(github_username):
        return {
            "message": "Error: Invalid GitHub Username - don't give us your email, just your username without the @ symbol."
        }
    
    # Create the slack channel
    logger.info("Creating slack channel %s", slack_channel)
    create_slack_channel(slack_channel)

    logger.info("Inviting user %s to slack channel %s", slack_user_id, slack_channel)
    invite_user_to_channel(slack_user_id, slack_channel)
    
    # Add Slack admins
    slack_admins = ["UC31XTRT5"], #"UCQKX6LPR", "U035023T81Z", "UC31XTRT5", "UC2JW3T3K", "UPD90QV17", "U05PYC0LMHR"]
    for admin in slack_admins:
        logger.info("Inviting admin %s to slack channel %s", admin, slack_channel)
        invite_user_to_channel(admin, slack_channel)

    # Send a message to the team about their request being queued
    slack_message = f'''
:rocket: Team *{team_name}* has been added to the queue! :tada:

*Channel:* #{slack_channel}
*Created by:* <@{root_slack_user_id}> (add your other team members here)

Thank you for your nonprofit preferences! Our team will review your request and pair you with a nonprofit soon.
We'll notify you once the pairing process is complete.

:question: *Need help?* 
Join <#C01E5CGDQ74> or <#C07KYG3CECX> for questions and updates.

Let's make a difference! :muscle: :heart:
'''
    send_slack(slack_message, slack_channel)
    send_slack(slack_message, "log-team-creation")

    my_date = datetime.now()
    collection = db.collection('teams')
    
    # Save the team with status IN_REVIEW and active=False
    insert_res = collection.document(doc_id).set({
        "team_number": -1,
        "users": [user],
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

    logger.debug("Insert Result: %s", insert_res)

    # Link the team to the user
    new_team_doc = db.collection('teams').document(doc_id)
    user_doc = user.get()
    user_dict = user_doc.to_dict()
    user_teams = user_dict["teams"]
    user_teams.append(new_team_doc)
    user.set({
        "teams": user_teams
    }, merge=True)

    # Link the team to the hackathon event
    hackathon_db_id = get_hackathon_by_event_id(hackathon_event_id)["id"]
    event_collection = db.collection("hackathons").document(hackathon_db_id)
    event_collection_dict = event_collection.get().to_dict()

    new_teams = []
    for t in event_collection_dict["teams"]:
        new_teams.append(t)
    new_teams.append(new_team_doc)

    event_collection.set({
        "teams": new_teams
    }, merge=True)

    # Clear the cache
    logger.info("Clearing cache for event_id=%s user_doc.id=%s doc_id=%s",
                hackathon_db_id, user_doc.id, doc_id)
    clear_cache()

    # Get the team
    team = get_teams_list(doc_id)

    return {
        "message": f"Team queued successfully! Check your Slack channel --> #{slack_channel} for more details.",
        "success": True,
        "team": team,
        "user": {
            "name": user_dict["name"],
            "profile_image": user_dict["profile_image"],
        }
    }


def get_teams_by_hackathon_id(user_id, hackathon_id):
    """
    Get all teams for a specific hackathon ID.
    """
    db = get_db()
    logger.debug("Getting teams by hackathon ID")
    
    # Get the event collection
    event_collection = db.collection("hackathons").document(hackathon_id)
    event_collection_dict = event_collection.get().to_dict()
    
    # Get the teams
    teams = []
    for t in event_collection_dict["teams"]:
        team_data = t.get().to_dict()
        team_data["id"] = t.id
        # Get the team members
        team_data["users"] = []
        for user_ref in team_data["users"]:
            user_data = user_ref.get().to_dict()
            user_data["id"] = user_ref.id
            team_data["users"].append(user_data)
            # Remove badges
            if "badges" in user_data:
                del user_data["badges"]

        teams.append(team_data)
        
    return {
        "teams": teams,
        "user_id": user_id
    }


def approve_team(admin_user_id, json):
    """
    Admin function to approve a team and pair with nonprofit
    - Set status to APPROVED
    - Set active to True
    - Create GitHub repo and resources
    - Send notification to team
    """
    send_slack_audit(action="approve_team", message="Approving", payload=json)
    
    db = get_db()
    logger.debug("Team Approval")
    logger.debug(json)
    
    team_id = json["teamId"]
    nonprofit_id = json["nonprofitId"]
    problem_statement_id = json.get("problemStatementId")
    
    # Get the team
    team_doc = db.collection('teams').document(team_id)
    team_data = team_doc.get().to_dict()
    
    if not team_data:
        return {
            "message": "Error: Team not found",
            "success": False
        }
    
    # Verify the team is in IN_REVIEW status
    if team_data.get("status") != "IN_REVIEW":
        return {
            "message": f"Error: Team is not in review status (current status: {team_data.get('status')})",
            "success": False
        }
    
    # Get nonprofit details
    nonprofit = None
    nonprofit_name = ""
    if nonprofit_id:
        logger.info("Nonprofit ID provided %s", nonprofit_id)
        nonprofit = get_single_npo(nonprofit_id)["nonprofits"]
        nonprofit_name = nonprofit["name"]
        
        # If no problem statement specified, use the first one from the nonprofit
        if not problem_statement_id and "problem_statements" in nonprofit and len(nonprofit["problem_statements"]) > 0:
            problem_statement_id = nonprofit["problem_statements"][0]
            logger.info("Using problem statement ID %s", problem_statement_id)
        elif not problem_statement_id:
            return {
                "message": "Error: No problem statement available for the nonprofit",
                "success": False
            }
    else:
        return {
            "message": "Error: Nonprofit ID is required",
            "success": False
        }
    
    # Get problem statement
    problem_statement = None
    if problem_statement_id:
        problem_statement = get_problem_statement_from_id_old(problem_statement_id)
        logger.info("Problem Statement %s", problem_statement)
    
    if not problem_statement:
        return {
            "message": "Error: Problem statement not found",
            "success": False
        }
    
    # Prepare data for GitHub repo creation
    team_name = team_data["name"]
    slack_channel = team_data["slack_channel"]
    github_username = team_data["github_username"]
    hackathon_event_id = team_data["hackathon_event_id"]
    
    # Get admin user details for audit
    _, _, _, _, admin_name, _ = get_propel_user_details_by_id(admin_user_id)
    
    # Get first team member for GitHub repo creation
    first_user_ref = team_data["users"][0]
    first_user_data = first_user_ref.get().to_dict()
    first_user_name = first_user_data.get("name", "Team Member")
    
    # Generate GitHub repo name
    raw_problem_statement_title = problem_statement.get().to_dict()["title"]
    problem_statement_title = raw_problem_statement_title.replace(" ", "").replace("-", "")
    nonprofit_title = nonprofit_name.replace(" ", "").replace("-", "")[:20]
    repository_name = f"{team_name}-{nonprofit_title}-{problem_statement_title}"[:100]
    
    # Create GitHub repo
    try:
        logger.info("Creating github repo %s", repository_name)
        repo = create_github_repo(
            repository_name, 
            hackathon_event_id, 
            first_user_name, 
            team_name, 
            slack_channel, 
            problem_statement_id, 
            raw_problem_statement_title, 
            github_username, 
            nonprofit_name, 
            nonprofit_id
        )
    except ValueError as e:
        return {
            "message": f"Error creating GitHub repo: {e}",
            "success": False
        }
    
    nonprofit_url = f"https://ohack.dev/nonprofit/{nonprofit_id}"
    project_url = f"https://ohack.dev/project/{problem_statement_id}"
    
    # Send notification to the team
    slack_message = f'''
:rocket: Great news! Your team *{team_name}* has been approved and paired with a nonprofit! :tada:

*Channel:* #{slack_channel}
*Nonprofit:* <{nonprofit_url}|{nonprofit_name}>
*Project:* <{project_url}|{raw_problem_statement_title}>
*Approved by:* {admin_name}

:github_parrot: *GitHub Repository:* {repo['full_url']}
All code goes here! Remember, we're building for the public good (MIT license).

:question: *Need help?* 
Join <#C01E5CGDQ74> or <#C07KYG3CECX> for questions and updates.

:clipboard: *Next Steps:*
1. Add team to GitHub repo: <https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600|How-to guide>
2. Create DevPost project: <https://youtu.be/vCa7QFFthfU?si=bzMQ91d8j3ZkOD03|Tutorial video>
3. Submit to <https://opportunity-hack-2024-arizona.devpost.com|2024 DevPost>
4. Study your nonprofit slides and software requirements doc and chat with mentors
5. Code, collaborate, and create!
6. Share your progress on the socials: `#ohack2024` @opportunityhack
7. <https://www.ohack.dev/volunteer/track|Log volunteer hours>
8. Post-hack: Update LinkedIn with your amazing experience!
9. Update <https://www.ohack.dev/profile|your profile> for a chance to win prizes!
10. Follow the schedule at <https://www.ohack.dev/hack/2024_fall|ohack.dev/hack/2024_fall>

Let's make a difference! :muscle: :heart:
'''
    send_slack(slack_message, slack_channel)
    send_slack(slack_message, "log-team-creation")
    
    repo_name = repo["repo_name"]
    full_github_repo_url = repo["full_url"]
    
    # Update the team document
    team_doc.set({
        "status": "APPROVED",
        "active": True,
        "problem_statements": [problem_statement],
        "nonprofit_id": nonprofit_id,
        "problem_statement_id": problem_statement_id,
        "github_links": [
            {
                "link": full_github_repo_url,
                "name": repo_name
            }
        ],
        "approved_by": admin_user_id,
        "approved_at": datetime.now().isoformat()
    }, merge=True)
    
    # Clear cache
    clear_cache()
    
    # Get updated team data
    team = get_teams_list(team_id)
    
    return {
        "message": f"Team approved and paired with {nonprofit_name}. Slack notification sent to #{slack_channel}.",
        "success": True,
        "team": team
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
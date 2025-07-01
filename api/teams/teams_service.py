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
from services.users_service import (
    save_user,
    get_user_from_slack_id
)
    

logger = logging.getLogger("myapp")

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
        event_collection_dict = event_collection.get().to_dict()
        
        # Remove the team from the hackathon event
        new_teams = []
        for t in event_collection_dict["teams"]:
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
    }
    
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
    
    SLACK_USER_ID_PREFIX = "oauth2|slack|T1Q7936BH-"
    root_slack_user_id = slack_user_id.replace(SLACK_USER_ID_PREFIX, "")
    users_list = []
    user = get_user_doc_reference(root_slack_user_id)
    users_list.append(user)
    
    db = get_db()
    logger.debug("Team Queue")
    logger.debug(json)
    
    doc_id = uuid.uuid1().hex  # Generate a new team id
    team_name = json["name"]
    slack_channel = json["slackChannel"]
    hackathon_event_id = json["eventId"]
    comments = json.get("comments", "")
    teamMembers = json.get("teamMembers", []) # In addition to the user
    url_hackathon = f"https://ohack.dev/hack/{hackathon_event_id}"
    url_manage_team = f"{url_hackathon}/manageteam"
    
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

    # Look at teamMember.id to get the slack user id
    
    send_slack_audit(action="queue_team", message=f"Queueing {len(teamMembers)} team members", payload=json)
    for member in teamMembers:
        if "id" in member and member["id"] != root_slack_user_id:
            logger.info("Inviting user %s to slack channel %s", member["id"], slack_channel)
            invite_user_to_channel(member["id"], slack_channel)
            # Lookup the user in the database by user_id which is their slack user id            
            full_user_id_with_slack_prefix = f"{SLACK_USER_ID_PREFIX}{member['id']}"
            user_db_check = get_user_from_slack_id(full_user_id_with_slack_prefix)
            if not user_db_check:
                new_user = save_user(
                    user_id=full_user_id_with_slack_prefix,
                    email="",
                    last_login="",
                    profile_image="",
                    name=member["real_name"],
                    nickname=member["name"]
                )                
                users_list.append(get_user_doc_reference(new_user.user_id))
            else:
                in_db_user = get_user_doc_reference(user_db_check.user_id)
                users_list.append(in_db_user)
                logger.info("User %s already exists in the database", user_db_check)
                    

        else:
            logger.info("Skipping user: %s", member)
        
    
    # Add Slack admins
    slack_admins = ["UCQKX6LPR", "U035023T81Z", "UC31XTRT5", "UC2JW3T3K", "UPD90QV17", "UEP2U69AA"]
    for admin in slack_admins:
        logger.info("Inviting admin %s to slack channel %s", admin, slack_channel)
        invite_user_to_channel(admin, slack_channel)

    # Send a message to the team about their request being queued
    slack_message = f'''
:rocket: Team *{team_name}* has been created! :tada:

*Channel:* #{slack_channel}
*Created by:* <@{root_slack_user_id}> (add your other team members here)

- Thank you for your nonprofit preferences! 
- Our team will review your request and pair you with a nonprofit soon.
- We'll send a message to this Slack channel when the pairing process is complete.


:question: *Need help?* 
Join <#C01E5CGDQ74> for questions and updates.

- Read more about the hackathon <{url_hackathon}|here>
- Manage your team <{url_manage_team}|here>

Let's make a difference! :muscle: :heart:
'''
    send_slack(slack_message, slack_channel)
    send_slack(slack_message, "log-team-creation")

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
    

    for t in hackathon_dict["teams"]:
        team_data = t.get().to_dict()        
        team_data["id"] = t.id
        
        for user_ref in team_data["users"]:
            user_data = user_ref.get().to_dict()
            user_data["id"] = user_ref.id
            logger.debug("User data: %s", user_data)
            if user_id == user_data["user_id"]:
                del team_data["users"]
                if "problem_statements" in team_data:
                    del team_data["problem_statements"]
                teams.append(team_data)                              

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
            logger.debug("Team data: %s", team_data)
            teams.append(team_data)
        
        logger.info(f"Retrieved {len(teams)} teams for hackathon {hackathon_id}")
        return {"teams": teams}
        
    except Exception as e:
        logger.error(f"Error getting teams for hackathon {hackathon_id}: {str(e)}")
        return {"teams": []}

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
        
        # Just make sure there is at least a problem statement
        if "problem_statements" in nonprofit and len(nonprofit["problem_statements"]) > 0:           
            logger.info("Problem statements found for nonprofit %s", nonprofit_name)
        else:
            return {
                "message": "Error: No problem statement available for the nonprofit",
                "success": False
            }
    else:
        return {
            "message": "Error: Nonprofit ID is required",
            "success": False
        }
            
    # Prepare data for GitHub repo creation
    team_name = team_data["name"]
    slack_channel = team_data["slack_channel"]
    github_username = team_data["github_username"]
    hackathon_event_id = team_data["hackathon_event_id"]

    # Get hackathon event details by event ID
    hackathon_event = get_hackathon_by_event_id(hackathon_event_id)
    if not hackathon_event:
        return {
            "message": "Error: Hackathon event not found",
            "success": False
        }
    
    # Make sure that github_org is set within the event
    if not hackathon_event.get("github_org"):
        return {
            "message": "Error: GitHub organization not found for the event",
            "success": False
        }
    github_org = hackathon_event["github_org"]
    logger.info("GitHub organization: %s", github_org)

    # Look through the "links" within the hackathon event to find any url that contains "devpost"
    devpost_url = ""
    for link in hackathon_event.get("links", []):
        if "devpost" in link.get("link", ""):
            devpost_url = link["link"]
            break
    logger.info("DevPost URL: %s", devpost_url)
    
    # Get admin user details for audit
    _, _, _, _, admin_name, _ = get_propel_user_details_by_id(admin_user_id)
    
    # Get first team member for GitHub repo creation
    first_user_ref = team_data["users"][0]
    first_user_data = first_user_ref.get().to_dict()
    first_user_name = first_user_data.get("name", "Team Member")
    
    # Generate GitHub repo name        
    nonprofit_title = nonprofit_name.replace(" ", "").replace("-", "")[:20]
    repository_name = f"{team_name}-{nonprofit_title}"[:100]
    
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
            devpost_url
        )
    except ValueError as e:
        logger.error("Error creating GitHub repo: %s", e)
        if "already exists" in str(e):
            logger.error("GitHub repo already exists...continuing")
        else:
            return {
                "message": f"Error creating GitHub repo: {e}",
                "success": False
            }
    
    nonprofit_url = f"https://ohack.dev/nonprofit/{nonprofit_id}"
    

    logger.info("GitHub repo created: %s", repo["full_url"])
    
    # Send notification to the team
    slack_message = f'''
:rocket: Great news! Your team *{team_name}* has been approved and paired with a nonprofit! :tada:

*Channel:* #{slack_channel}
*Nonprofit:* <{nonprofit_url}|{nonprofit_name}>
*Approved by:* {admin_name}

:github_parrot: *GitHub Repository:* {repo['full_url']}
All code goes here! Remember, we're building for the public good (MIT license).

:question: *Need help?* 
Join <#C01E5CGDQ74> for questions and updates.

:clipboard: *Next Steps:*
1. Add team to GitHub repo: <https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600|How-to guide>
2. Create DevPost project: <https://youtu.be/vCa7QFFthfU?si=bzMQ91d8j3ZkOD03|Tutorial video>
3. Submit to <{devpost_url}|this DevPost project> - you can continue to update this until the deadline!
4. Study your nonprofit slides and software requirements doc and chat with mentors
5. Code, collaborate, and create!
6. Share your progress on the socials: `#ohack` and `@opportunityhack`
7. <https://www.ohack.dev/volunteer/track|Log volunteer hours>
8. Post-hack: Update LinkedIn with your amazing experience! You are getting real-world industry experience!
9. Update <https://www.ohack.dev/profile|your profile> for a chance to win prizes!
10. Follow the schedule at <https://www.ohack.dev/hack/{hackathon_event_id}|ohack.dev/hack/{hackathon_event_id}>

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
import uuid
from datetime import datetime

from cachetools import cached, TTLCache
from ratelimit import limits
from firebase_admin import firestore

from common.log import get_logger
from common.utils.firestore_helpers import doc_to_json, log_execution_time
from common.utils.slack import send_slack_audit, send_slack, create_slack_channel, invite_user_to_channel
from common.utils.github import create_github_repo, validate_github_username, get_all_repos
from common.utils.firebase import get_hackathon_by_event_id
from common.utils.oauth_providers import extract_slack_user_id
from db.db import get_db, get_user_doc_reference
from services.users_service import (
    get_propel_user_details_by_id,
    get_slack_user_from_propel_user_id,
    get_user_from_slack_id,
)
from services.nonprofits_service import get_single_npo
from api.messages.message import Message

logger = get_logger("teams_service")

ONE_MINUTE = 60
THIRTY_SECONDS = 30


def _clear_cache():
    from services.hackathons_service import clear_cache
    clear_cache()


@limits(calls=2000, period=THIRTY_SECONDS)
def get_teams_list(id=None):
    logger.debug(f"Teams List Start team_id={id}")
    db = get_db()
    if id is not None:
        logger.debug(f"Teams List team_id={id} | Start")
        doc = db.collection('teams').document(id).get()
        if doc is None:
            return {}
        else:
            logger.info(f"Teams List team_id={id} | End (with result):{doc_to_json(docid=doc.id, doc=doc)}")
            logger.debug(f"Teams List team_id={id} | End")
            return doc_to_json(docid=doc.id, doc=doc)
    else:
        logger.debug("Teams List | Start")
        docs = db.collection('teams').stream()
        if docs is None:
            logger.debug("Teams List | End (no results)")
            return {[]}
        else:
            results = []
            for doc in docs:
                results.append(doc_to_json(docid=doc.id, doc=doc))

            logger.debug(f"Found {len(results)} results {results}")
            return { "teams": results }


@limits(calls=2000, period=THIRTY_SECONDS)
@cached(cache=TTLCache(maxsize=100, ttl=600), key=lambda id: id)
@log_execution_time
def get_team(id):
    if id is None:
        logger.warning("get_team called with None id")
        return {"team": {}}

    logger.debug(f"Fetching team with id={id}")

    db = get_db()
    doc_ref = db.collection('teams').document(id)

    try:
        doc = doc_ref.get()
        if not doc.exists:
            logger.info(f"Team with id={id} not found")
            return {}

        team_data = doc_to_json(docid=doc.id, doc=doc)
        logger.info(f"Successfully retrieved team with id={id}")
        return {
            "team" : team_data
        }

    except Exception as e:
        logger.error(f"Error retrieving team with id={id}: {str(e)}")
        return {}

    finally:
        logger.debug(f"get_team operation completed for id={id}")


def get_teams_by_event_id(event_id):
    """Get teams for a specific hackathon event (for admin judging assignment)"""
    logger.debug(f"Getting teams for event_id={event_id}")
    db = get_db()

    try:
        docs = db.collection('teams').where('hackathon_event_id', '==', event_id).stream()

        results = []
        for doc in docs:
            team_data = doc_to_json(docid=doc.id, doc=doc)

            formatted_team = {
                "id": team_data.get("id"),
                "name": team_data.get("name", ""),
                "members": team_data.get("members", []),
                "problem_statement": {
                    "title": team_data.get("problem_statement", {}).get("title", ""),
                    "nonprofit": team_data.get("problem_statement", {}).get("nonprofit", "")
                }
            }
            results.append(formatted_team)

        logger.debug(f"Found {len(results)} teams for event {event_id}")
        return {"teams": results}

    except Exception as e:
        logger.error(f"Error fetching teams for event {event_id}: {str(e)}")
        return {"teams": [], "error": "Failed to fetch teams"}


def get_teams_batch(json):
    if "team_ids" not in json:
        logger.error("get_teams_batch called without team_ids in json")
        return []
    team_ids = json["team_ids"]
    logger.debug(f"get_teams_batch start team_ids={team_ids}")
    db = get_db()
    if not team_ids:
        logger.warning("get_teams_batch end (no team_ids provided)")
        return []
    try:
        docs = db.collection('teams').where(
            '__name__', 'in', [db.collection('teams').document(team_id) for team_id in team_ids]).stream()

        results = []
        for doc in docs:
            team_data = doc_to_json(docid=doc.id, doc=doc)
            results.append(team_data)

        logger.debug(f"get_teams_batch end (with {len(results)} results)")
        return results
    except Exception as e:
        logger.error(f"Error in get_teams_batch: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def save_team(propel_user_id, json):
    send_slack_audit(action="save_team", message="Saving", payload=json)

    email, user_id, last_login, profile_image, name, nickname = get_propel_user_details_by_id(propel_user_id)
    slack_user_id = user_id

    root_slack_user_id = extract_slack_user_id(slack_user_id)
    user = get_user_doc_reference(root_slack_user_id)

    db = get_db()
    logger.debug("Team Save")

    logger.debug(json)
    doc_id = uuid.uuid1().hex

    team_name = json["name"]



    slack_channel = json["slackChannel"]

    hackathon_event_id = json["eventId"]
    problem_statement_id = json["problemStatementId"] if "problemStatementId" in json else None
    nonprofit_id = json["nonprofitId"] if "nonprofitId" in json else None

    github_username = json["githubUsername"]
    if validate_github_username(github_username) == False:
        return {
            "message": "Error: Invalid GitHub Username - don't give us your email, just your username without the @ symbol."
        }


    nonprofit = None
    nonprofit_name = ""
    if nonprofit_id is not None:
        logger.info(f"Nonprofit ID provided {nonprofit_id}")
        nonprofit = get_single_npo(nonprofit_id)["nonprofits"]
        nonprofit_name = nonprofit["name"]
        logger.info(f"Nonprofit {nonprofit}")
        if "problem_statements" in nonprofit and len(nonprofit["problem_statements"]) > 0:
            problem_statement_id = nonprofit["problem_statements"][0]
            logger.info(f"Problem Statement ID {problem_statement_id}")
        else:
            return {
                "message": "Error: Nonprofit does not have any problem statements"
            }


    problem_statement = None
    if problem_statement_id is not None:
        problem_statement = get_problem_statement_from_id_old(problem_statement_id)
        logger.info(f"Problem Statement {problem_statement}")

    if nonprofit is None and problem_statement is None:
        return "Error: Please provide either a Nonprofit or a Problem Statement"


    team_slack_channel = slack_channel
    raw_problem_statement_title = problem_statement.get().to_dict()["title"]

    problem_statement_title = raw_problem_statement_title.replace(" ", "").replace("-", "")
    logger.info(f"Problem Statement Title: {problem_statement_title}")

    nonprofit_title = nonprofit_name.replace(" ", "").replace("-", "")
    nonprofit_title = nonprofit_title[:20]
    logger.info(f"Nonprofit Title: {nonprofit_title}")

    repository_name = f"{team_name}-{nonprofit_title}-{problem_statement_title}"
    logger.info(f"Repository Name: {repository_name}")

    repository_name = repository_name[:100]
    logger.info(f"Truncated Repository Name: {repository_name}")

    slack_name_of_creator = name

    nonprofit_url = f"https://ohack.dev/nonprofit/{nonprofit_id}"
    project_url = f"https://ohack.dev/project/{problem_statement_id}"
    try:
        logger.info(f"Creating github repo {repository_name} for {json}")
        repo = create_github_repo(repository_name, hackathon_event_id, slack_name_of_creator, team_name, team_slack_channel, problem_statement_id, raw_problem_statement_title, github_username, nonprofit_name, nonprofit_id)
    except ValueError as e:
        return {
            "message": f"Error: {e}"
        }
    logger.info(f"Created github repo {repo} for {json}")

    logger.info(f"Creating slack channel {slack_channel}")
    create_slack_channel(slack_channel)

    logger.info(f"Inviting user {slack_user_id} to slack channel {slack_channel}")
    invite_user_to_channel(slack_user_id, slack_channel)

    slack_admins = ["UC31XTRT5", "UCQKX6LPR", "U035023T81Z", "UC31XTRT5", "UC2JW3T3K", "UPD90QV17", "U05PYC0LMHR"]
    for admin in slack_admins:
        logger.info(f"Inviting admin {admin} to slack channel {slack_channel}")
        invite_user_to_channel(admin, slack_channel)

    slack_message = f'''
:rocket: Team *{team_name}* is ready for launch! :tada:

*Channel:* #{team_slack_channel}
*Nonprofit:* <{nonprofit_url}|{nonprofit_name}>
*Project:* <{project_url}|{raw_problem_statement_title}>
*Created by:* <@{root_slack_user_id}> (add your other team members here)

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

    my_date = datetime.now()
    collection = db.collection('teams')
    insert_res = collection.document(doc_id).set({
        "team_number" : -1,
        "users": [user],
        "problem_statements": [problem_statement],
        "name": team_name,
        "slack_channel": slack_channel,
        "created": my_date.isoformat(),
        "active": "True",
        "github_links": [
            {
                "link": full_github_repo_url,
                "name": repo_name
            }
        ]
    })

    logger.debug(f"Insert Result: {insert_res}")

    new_team_doc = db.collection('teams').document(doc_id)
    user_doc = user.get()
    user_dict = user_doc.to_dict()
    user_teams = user_dict["teams"]
    user_teams.append(new_team_doc)
    user.set({
        "teams": user_teams
    }, merge=True)

    hackathon_db_id = get_hackathon_by_event_id(hackathon_event_id)["id"]
    event_collection = db.collection("hackathons").document(hackathon_db_id)
    event_collection_dict = event_collection.get().to_dict()

    new_teams = []
    for t in event_collection_dict["teams"]:
        new_teams.append(t)
    new_teams.append(new_team_doc)

    event_collection.set({
        "teams" : new_teams
    }, merge=True)

    logger.info(f"Clearing cache for event_id={hackathon_db_id} problem_statement_id={problem_statement_id} user_doc.id={user_doc.id} doc_id={doc_id}")
    _clear_cache()

    team = get_teams_list(doc_id)


    return {
        "message" : f"Saved Team and GitHub repo created. See your Slack channel --> #{slack_channel} for more details.",
        "success" : True,
        "team": team,
        "user": {
            "name" : user_dict["name"],
            "profile_image": user_dict["profile_image"],
        }
        }


def get_github_repos(event_id):
    logger.info(f"Get Github Repos for event_id={event_id}")
    hackathon = get_hackathon_by_event_id(event_id)
    if hackathon is None:
        logger.warning(f"Get Github Repos End (no results)")
        return {}
    else:
        org_name = hackathon["github_org"]
        return get_all_repos(org_name)


def join_team(propel_user_id, json):
    logger.info(f"Join Team UserId: {propel_user_id} Json: {json}")
    team_id = json["teamId"]

    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    userid = get_user_from_slack_id(slack_user["sub"]).id

    team_ref = db.collection('teams').document(team_id)
    user_ref = db.collection('users').document(userid)

    @firestore.transactional
    def update_team_and_user(transaction):
        team_doc = team_ref.get(transaction=transaction)
        user_doc = user_ref.get(transaction=transaction)

        if not team_doc.exists:
            raise ValueError("Team not found")
        if not user_doc.exists:
            raise ValueError("User not found")

        team_data = team_doc.to_dict()
        user_data = user_doc.to_dict()

        team_users = team_data.get("users", [])
        user_teams = user_data.get("teams", [])

        if user_ref in team_users:
            logger.warning(f"User {userid} is already in team {team_id}")
            return False, None

        new_team_users = list(set(team_users + [user_ref]))
        new_user_teams = list(set(user_teams + [team_ref]))

        transaction.update(team_ref, {"users": new_team_users})
        transaction.update(user_ref, {"teams": new_user_teams})

        logger.debug(f"User {userid} added to team {team_id}")
        return True, team_data.get("slack_channel")

    try:
        transaction = db.transaction()
        success, team_slack_channel = update_team_and_user(transaction)
        if success:
            send_slack_audit(action="join_team", message="Added", payload=json)
            message = "Joined Team"
            if team_slack_channel:
                invite_user_to_channel(userid, team_slack_channel)
        else:
            message = "User was already in the team"
    except Exception as e:
        logger.error(f"Error in join_team: {str(e)}")
        return Message(f"Error: {str(e)}")

    _clear_cache()

    logger.debug("Join Team End")
    return Message(message)


def unjoin_team(propel_user_id, json):
    logger.info(f"Unjoin for UserId: {propel_user_id} Json: {json}")
    team_id = json["teamId"]

    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    userid = get_user_from_slack_id(slack_user["sub"]).id

    team_ref = db.collection('teams').document(team_id)
    user_ref = db.collection('users').document(userid)

    @firestore.transactional
    def update_team_and_user(transaction):
        team_doc = team_ref.get(transaction=transaction)
        user_doc = user_ref.get(transaction=transaction)

        if not team_doc.exists:
            raise ValueError("Team not found")
        if not user_doc.exists:
            raise ValueError("User not found")

        team_data = team_doc.to_dict()
        user_data = user_doc.to_dict()

        user_list = team_data.get("users", [])
        user_teams = user_data.get("teams", [])

        if user_ref not in user_list:
            logger.warning(f"User {userid} not found in team {team_id}")
            return False

        new_user_list = [u for u in user_list if u.id != userid]
        new_user_teams = [t for t in user_teams if t.id != team_id]

        transaction.update(team_ref, {"users": new_user_list})
        transaction.update(user_ref, {"teams": new_user_teams})

        logger.debug(f"User {userid} removed from team {team_id}")
        return True

    try:
        transaction = db.transaction()
        success = update_team_and_user(transaction)
        if success:
            send_slack_audit(action="unjoin_team", message="Removed", payload=json)
            message = "Removed from Team"
        else:
            message = "User was not in the team"
    except Exception as e:
        logger.error(f"Error in unjoin_team: {str(e)}")
        return Message(f"Error: {str(e)}")

    _clear_cache()

    logger.debug("Unjoin Team End")
    return Message(message)


def get_problem_statement_from_id_old(problem_id):
    """Lazy import to avoid circular dependency with messages_service."""
    from api.messages.messages_service import get_problem_statement_from_id_old as _get_ps
    return _get_ps(problem_id)

from common.utils import safe_get_env_var
from common.utils.slack import send_slack_audit, create_slack_channel, send_slack, invite_user_to_channel
from common.utils.firebase import get_hackathon_by_event_id, upsert_news
from api.messages.message import Message
import json
import uuid
from datetime import datetime, timedelta
import time

import logging
import firebase_admin
from firebase_admin import credentials, firestore
import requests

from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey

from ratelimit import limits
from datetime import datetime, timedelta

from common.utils.github import create_github_repo


logger = logging.getLogger("myapp")
logger.setLevel(logging.INFO)

auth0_domain = safe_get_env_var("AUTH0_DOMAIN")
auth0_client = safe_get_env_var("AUTH0_USER_MGMT_CLIENT_ID")
auth0_secret = safe_get_env_var("AUTH0_USER_MGMT_SECRET")

ONE_MINUTE = 1*60
THIRTY_SECONDS = 30
def get_public_message():
    logger.debug("~ Public ~")
    return Message(
        "aaThis is a public message."
    )


def get_protected_message():
    logger.debug("~ Protected ~")

    return Message(
        "This is a protected message."
    )


def get_admin_message():
    logger.debug("~ Admin ~")

    return Message(
        "This is an admin message."
    )

def hash_key(docid, doc=None, depth=0):
    return hashkey(docid)



# Generically handle a DocumentSnapshot or a DocumentReference
#@cached(cache=TTLCache(maxsize=1000, ttl=43200), key=hash_key)
@cached(cache=LRUCache(maxsize=640*1024), key=hash_key)
def doc_to_json(docid=None, doc=None, depth=0):
    # Log
    logger.debug(f"doc_to_json start docid={docid} doc={doc}")
        
    if not docid:
        logger.debug("docid is NoneType")
        return
    if not doc:
        logger.debug("doc is NoneType")
        return
        
    # Check if type is DocumentSnapshot
    if isinstance(doc, firestore.DocumentSnapshot):
        logger.debug("doc is DocumentSnapshot")
        d_json = doc.to_dict()
    # Check if type is DocumentReference
    elif isinstance(doc, firestore.DocumentReference):
        logger.debug("doc is DocumentReference")
        d = doc.get()
        d_json = d.to_dict()    
    else:        
        return doc
    
    if d_json is None:
        logger.warn(f"doc.to_dict() is NoneType | docid={docid} doc={doc}")
        return

    # If any values in d_json is a list, add only the document id to the list for DocumentReference or DocumentSnapshot
    for key, value in d_json.items():
        if isinstance(value, list):
            logger.debug(f"doc_to_json - key={key} value={value}")
            for i, v in enumerate(value):
                logger.debug(f"doc_to_json - i={i} v={v}")
                if isinstance(v, firestore.DocumentReference):
                    logger.debug(f"doc_to_json - v is DocumentReference")
                    value[i] = v.id
                elif isinstance(v, firestore.DocumentSnapshot):
                    logger.debug(f"doc_to_json - v is DocumentSnapshot")
                    value[i] = v.id
                else:
                    logger.debug(f"doc_to_json - v is not DocumentReference or DocumentSnapshot")
                    value[i] = v
            d_json[key] = value
    
            
    
    d_json["id"] = docid
    return d_json

from firebase_admin.firestore import DocumentReference, DocumentSnapshot

# handle DocumentReference or DocumentSnapshot and recursefuly call doc_to_json
def doc_to_json_recursive(doc=None):
    # Log
    logger.debug(f"doc_to_json_recursive start doc={doc}")          
    
    if not doc:
        logger.debug("doc is NoneType")
        return
        
    docid = ""
    # Check if type is DocumentSnapshot
    if isinstance(doc, DocumentSnapshot):
        logger.debug("doc is DocumentSnapshot")
        d_json = doc_to_json(docid=doc.id, doc=doc)
        docid = doc.id
    # Check if type is DocumentReference
    elif isinstance(doc, DocumentReference):
        logger.debug("doc is DocumentReference")
        d = doc.get()
        docid = d.id
        d_json = doc_to_json(docid=doc.id, doc=d)               
    else:
        logger.debug(f"Not DocumentSnapshot or DocumentReference, skipping - returning: {doc}")
        return doc
    
    d_json["id"] = docid
    return d_json


def get_db():
    #mock_db = MockFirestore()
    return firestore.client()

@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_single_problem_statement(project_id):
    logger.debug(f"get_single_problem_statement start project_id={project_id}")    
    db = get_db()      
    doc = db.collection('problem_statements').document(project_id)
    
    if doc is None:
        logger.warning("get_single_problem_statement end (no results)")
        return {}
    else:                                
        result = doc_to_json(docid=doc.id, doc=doc)
        result["id"] = doc.id
        
        logger.info(f"get_single_problem_statement end (with result):{result}")
        return result
    return {}

@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=2000, period=ONE_MINUTE)
def get_single_hackathon_id(id):
    logger.debug(f"get_single_hackathon_id start id={id}")    
    db = get_db()      
    doc = db.collection('hackathons').document(id)
    
    if doc is None:
        logger.warning("get_single_hackathon_id end (no results)")
        return {}
    else:                                
        result = doc_to_json(docid=doc.id, doc=doc)
        result["id"] = doc.id
        
        logger.info(f"get_single_hackathon_id end (with result):{result}")
        return result
    return {}

@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=2000, period=ONE_MINUTE)
def get_single_hackathon_event(hackathon_id):
    logger.debug(f"get_single_hackathon_event start hackathon_id={hackathon_id}")    
    result = get_hackathon_by_event_id(hackathon_id)
    
    if result is None:
        logger.warning("get_single_hackathon_event end (no results)")
        return {}
    else:                  
        if "nonprofits" in result:           
            result["nonprofits"] = [doc_to_json(doc=npo, docid=npo.id) for npo in result["nonprofits"]]   
        else:
            result["nonprofits"] = []
        if "teams" in result:
            result["teams"] = [doc_to_json(doc=team, docid=team.id) for team in result["teams"]]        
        else:
            result["teams"] = []

        logger.info(f"get_single_hackathon_event end (with result):{result}")
        return result
    return {}

# 12 hour cache for 100 objects LRU
@limits(calls=200, period=ONE_MINUTE)
def get_single_npo(npo_id):    
    logger.debug(f"get_npo start npo_id={npo_id}")    
    db = get_db()      
    doc = db.collection('nonprofits').document(npo_id)    
    
    if doc is None:
        logger.warning("get_npo end (no results)")
        return {}
    else:                        
        result = doc_to_json(docid=doc.id, doc=doc)

        logger.info(f"get_npo end (with result):{result}")
        return {
            "nonprofits": result
        }
    return {}


@limits(calls=200, period=ONE_MINUTE)
def get_hackathon_list(is_current_only=None):
    logger.debug("Hackathon List Start")
    db = get_db()
    
    if is_current_only == "current":                
        today = datetime.now()        
        today_str = today.strftime("%Y-%m-%d")
        logger.debug(
            f"Looking for any event that finishes after today {today_str} for most current events only.")
        docs = db.collection('hackathons').where("end_date", ">=", today_str).order_by("end_date", direction=firestore.Query.DESCENDING).stream()  # steam() gets all records
    elif is_current_only == "previous": 
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        N_DAYS_LOOK_BACKWARD = 12*30*3 # 3 years
        target_date = datetime.now() + timedelta(days=-N_DAYS_LOOK_BACKWARD)
        target_date_str = target_date.strftime("%Y-%m-%d")
        logger.debug(
            f"Looking for any event that finishes before today {target_date_str} for previous events only.")
        docs = db.collection('hackathons').where("end_date", ">=", target_date_str).where("end_date", "<=", today_str).order_by("end_date", direction=firestore.Query.DESCENDING).limit(3).stream()  # steam() gets all records       
    else:
        docs = db.collection('hackathons').order_by("start_date").stream()  # steam() gets all records
    
    
    if docs is None:
        logger.debug("Found no results, returning empty list")
        return {[]}
    else:
        results = []
        for doc in docs:
            d = doc_to_json(doc.id, doc)
            # If any value from the keys is a DocumentReference or DocumentSnapshot, call doc_to_json
            for key in d.keys():
                logger.debug(f"Checking key {key}")
                #print type of key
                logger.debug(f"Type of key {type(d[key])}")
                
                # If type is list, iterate through list and call doc_to_json
                if isinstance(d[key], list):
                    logger.debug(f"Found list for key {key}...")
                    # Process all items in list and convert with doc_to_json if they are DocumentReference or DocumentSnapshot
                    for i in range(len(d[key])):
                        logger.debug(f"Processing list item {i}: {d[key][i]}")
                        d[key][i] = doc_to_json_recursive(d[key][i])
                                                                        
                # If type is DocumentReference or DocumentSnapshot, call doc_to_json
                elif isinstance(d[key], DocumentReference) or isinstance(d[key], DocumentSnapshot):
                    logger.debug(f"Found DocumentReference or DocumentSnapshot for key {key}...")
                    d[key] = doc_to_json_recursive(d[key])
                    
                              
            results.append(d)     

    num_results = len(results)
    logger.debug(f"Found {num_results} results")
    logger.debug(f"Results: {results}")
    logger.debug(f"Hackathon List End")
    return {"hackathons": results}


@limits(calls=2000, period=THIRTY_SECONDS)
def get_teams_list(id=None):
    logger.debug(f"Teams List Start team_id={id}")
    db = get_db() 
    if id is not None:
        # Get by id
        doc = db.collection('teams').document(id).get()
        if doc is None:
            return {}
        else:
            #log
            logger.info(f"Teams List team_id={id} | End (with result):{doc_to_json(docid=doc.id, doc=doc)}")
            return doc_to_json(docid=doc.id, doc=doc)
    else:
        # Get all        
        docs = db.collection('teams').stream() # steam() gets all records   
        if docs is None:
            return {[]}
        else:                
            results = []
            for doc in docs:
                results.append(doc_to_json(docid=doc.id, doc=doc))
                                
            return { "teams": results }

@limits(calls=20, period=ONE_MINUTE)
def get_npo_list(word_length=30):
    logger.debug("NPO List Start")
    db = get_db()  
    # steam() gets all records
    docs = db.collection('nonprofits').order_by("name").stream()
    if docs is None:
        return {[]}
    else:                
        results = []
        for doc in docs:
            logger.debug(f"Processing doc {doc.id} {doc}")
            results.append(doc_to_json_recursive(doc=doc))
           
    # log result
    logger.debug(f"Found {len(results)} results {results}")
    return { "nonprofits": results }
    

@limits(calls=100, period=ONE_MINUTE)
def get_problem_statement_list():
    logger.debug("Problem Statements List")
    db = get_db()
    docs = db.collection('problem_statements').stream()  # steam() gets all records
    if docs is None:
        return {[]}
    else:
        results = []
        for doc in docs:
            results.append(doc_to_json(docid=doc.id, doc=doc))

    # log result
    logger.debug(results)        
    return { "problem_statements": results }

def save_team(json):    
    send_slack_audit(action="save_team", message="Saving", payload=json)

    db = get_db()  # this connects to our Firestore database
    logger.debug("Team Save")    

    logger.debug(json)
    doc_id = uuid.uuid1().hex # Generate a new team id

    name = json["name"]    
    slack_user_id = json["userId"]
    root_slack_user_id = slack_user_id.replace("oauth2|slack|T1Q7936BH-","")
    event_id = json["eventId"]
    slack_channel = json["slackChannel"]
    problem_statement_id = json["problemStatementId"]
    github_username = json["githubUsername"]
    
    
    user = get_user_from_slack_id(slack_user_id).reference
    if user is None:
        return

    problem_statement = get_problem_statement_from_id(problem_statement_id)
    if problem_statement is None:
        return

    # Define vars for github repo creation
    hackathon_event_id = get_single_hackathon_id(event_id)["event_id"]    
    team_name = name
    team_slack_channel = slack_channel
    raw_problem_statement_title = problem_statement.get().to_dict()["title"]
    
    # Remove all spaces from problem_statement_title
    problem_statement_title = raw_problem_statement_title.replace(" ", "").replace("-", "")

    repository_name = f"{team_name}--{problem_statement_title}"
    
    # truncate repostory name to first 100 chars to support github limits
    repository_name = repository_name[:100]

    slack_name_of_creator = user.get().to_dict()["name"]

    project_url = f"https://ohack.dev/project/{problem_statement_id}"
    # Create github repo
    try:
        repo = create_github_repo(repository_name, hackathon_event_id, slack_name_of_creator, team_name, team_slack_channel, problem_statement_id, raw_problem_statement_title, github_username)
    except ValueError as e:
        return {
            "message": f"Error: {e}"
        }
    logger.info(f"Created github repo {repo} for {json}")

    create_slack_channel(slack_channel)
    invite_user_to_channel(slack_user_id, slack_channel)
    
    # Add all Slack admins too  
    slack_admins = ["UC31XTRT5", "UCQKX6LPR", "U035023T81Z", "UC31XTRT5", "UC2JW3T3K", "UPD90QV17", "U05PYC0LMHR"]
    for admin in slack_admins:
        invite_user_to_channel(admin, slack_channel)

    # Send a slack message to the team channel
    slack_message = f'''
:astronaut-floss-dancedance: Team `{name}` | `#{team_slack_channel}` has been created in support of project `{raw_problem_statement_title}` {project_url} by <@{root_slack_user_id}>.

Github repo: {repo['full_url']}
- All code should go here!
- Everything we build is for the public good and carries an MIT license

Questions? join <#C01E5CGDQ74> or use <#C05TVU7HBML> or <#C05TZL13EUD> Slack channels. 
:partyparrot:

Your next steps:
1. Add everyone to your GitHub repo like this: https://opportunity-hack.slack.com/archives/C1Q6YHXQU/p1605657678139600
2. Create your DevPost project https://youtu.be/vCa7QFFthfU?si=bzMQ91d8j3ZkOD03
 - ASU Students use https://opportunity-hack-2023-asu.devpost.com/
 - Everyone else use https://opportunity-hack-2023-virtual.devpost.com/
3. Ask your nonprofit questions and bounce ideas off mentors!
4. Hack the night away!
5. Post any pics to your socials with `#ohack2023` and mention `@opportunityhack`
6. Track any volunteer hours - you are volunteering for a nonprofit!
7. After the hack, update your LinkedIn profile with your new skills and experience!
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
        "name": name,        
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

    # Look up the new team object that was just created
    new_team_doc = db.collection('teams').document(doc_id)
    user_doc = user.get()
    user_dict = user_doc.to_dict()    
    user_teams = user_dict["teams"]
    user_teams.append(new_team_doc)
    user.set({
        "teams": user_teams
    }, merge=True)

    # Get the hackathon (event) - add the team to the event
    event_collection = db.collection("hackathons").document(event_id)
    event_collection_dict = event_collection.get().to_dict()

    new_teams = []     
    for t in event_collection_dict["teams"]:        
        new_teams.append(t)
    new_teams.append(new_team_doc)

    event_collection.set({
        "teams" : new_teams
    }, merge=True)

    # Clear the cache
    logger.info(f"Clearing cache for event_id={event_id} problem_statement_id={problem_statement_id} user_doc.id={user_doc.id} doc_id={doc_id}")
    clear_cache()

    # get the team from get_teams_list
    team = get_teams_list(doc_id)


    return {
        "message" : f"Saved Team and GitHub repo created. See your Slack channel #{slack_channel} for more details.",
        "success" : True,
        "team": team,
        "user": {
            "name" : user_dict["name"],
            "profile_image": user_dict["profile_image"],
        }
        }
        

def join_team(userid, json):
    send_slack_audit(action="join_team", message="Adding", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("Join Team Start")

    logger.info(f"Join Team UserId: {userid} Json: {json}")
    team_id = json["teamId"]

    team_doc = db.collection('teams').document(team_id)
    team_dict = team_doc.get().to_dict()

    user_doc = get_user_from_slack_id(userid).reference
    user_dict = user_doc.get().to_dict()
    new_teams = []
    for t in user_dict["teams"]:
        new_teams.append(t)
    new_teams.append(team_doc)
    user_doc.set({
        "teams": new_teams
    }, merge=True)

    new_users = []
    if "users" in team_dict:
        for u in team_dict["users"]:
            new_users.append(u)
    new_users.append(user_doc)  

    # Avoid any duplicate additions
    new_users_set = set(new_users)

    team_doc.set({
        "users": new_users_set
    }, merge=True)    


    # Clear the cache
    logger.info(f"Clearing cache for team_id={team_id} and user_doc.id={user_doc.id}")            
    clear_cache()

    logger.debug("Join Team End")
    return Message("Joined Team")




def unjoin_team(userid, json):
    send_slack_audit(action="unjoin_team", message="Removing", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("Unjoin Team Start")
    
    logger.info(f"Unjoin for UserId: {userid} Json: {json}")
    team_id = json["teamId"]

    ## 1. Lookup Team, Remove User 
    doc = db.collection('teams').document(team_id)
    
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="unjoin_team",
                         message="Removing", payload=doc_dict)
        user_list = doc_dict["users"] if "users" in doc_dict else []

        # Look up a team associated with this user and remove that team from their list of teams
        new_users = []
        for u in user_list:
            user_doc = u.get()
            user_dict = user_doc.to_dict()

            new_teams = []
            if userid == user_dict["user_id"]:
                for t in user_dict["teams"]:
                    logger.debug(t.get().id)
                    if t.get().id == team_id:
                        logger.debug("Remove team")                        
                    else:
                        logger.debug("Keep team")
                        new_teams.append(t)
            else:
                logger.debug("Keep user")
                new_users.append(u)
            # Update users collection with new teams
            u.set({
                "teams": new_teams
                }, merge=True) # merging allows to only update this column and not blank everything else out
                    
        doc.set({
            "users": new_users
        }, merge=True)
        logger.debug(new_users)
        
    # Clear the cache
    logger.info(f"Clearing team_id={team_id} cache")         
    clear_cache()
            
    logger.debug("Unjoin Team End")

    return Message(
        "Removed from Team")


@limits(calls=100, period=ONE_MINUTE)
def save_npo(json):    
    send_slack_audit(action="save_npo", message="Saving", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("NPO Save")    
    # TODO: In this current form, you will overwrite any information that matches the same NPO name

    doc_id = uuid.uuid1().hex

    name = json["name"]
    email = json["email"]
    npoName = json["npoName"]
    slack_channel = json["slack_channel"]
    website = json["website"]
    description = json["description"]
    temp_problem_statements = json["problem_statements"]
    

    # We need to convert this from just an ID to a full object
    # Ref: https://stackoverflow.com/a/59394211
    problem_statements = []
    for ps in temp_problem_statements:
        problem_statements.append(db.collection("problem_statements").document(ps))
     
    collection = db.collection('nonprofits')
    
    insert_res = collection.document(doc_id).set({
        "contact_email": [email], # TODO: Support more than one email
        "contact_people": [name], # TODO: Support more than one name
        "name": npoName,
        "slack_channel" :slack_channel,
        "website": website,
        "description": description,
        "problem_statements": problem_statements
    })

    logger.debug(f"Insert Result: {insert_res}")

    return Message(
        "Saved NPO"
    )

def clear_cache():        
    get_profile_metadata.cache_clear()
    doc_to_json.cache_clear()
    get_single_hackathon_event.cache_clear()
    get_single_hackathon_id.cache_clear()
    

@limits(calls=100, period=ONE_MINUTE)
def remove_npo(json):
    logger.debug("Start NPO Delete")    
    doc_id = json["id"]
    db = get_db()  # this connects to our Firestore database
    doc = db.collection('nonprofits').document(doc_id)
    if doc:
        send_slack_audit(action="remove_npo", message="Removing", payload=doc.get().to_dict())
        doc.delete()

    # TODO: Add a way to track what has been deleted
    # Either by calling Slack or by using another DB/updating the DB with a hidden=True flag, etc.

    logger.debug("End NPO Delete")
    return Message(
        "Delete NPO"
    )


@limits(calls=100, period=ONE_MINUTE)
def save_helping_status(json):
    logger.debug(f"save_helping_status {json}")

    helping_status = json["status"] # helping or not_helping
    user_id = json["user_id"] # Slack user id
    problem_statement_id = json["problem_statement_id"]
    mentor_or_hacker = json["type"]

    npo_id =  json["npo_id"] if "npo_id" in json else ""
    
    user_obj = get_user_from_slack_id(user_id)
    my_date = datetime.now()


    to_add = {
        "user": user_obj.id,
        "slack_user": user_id,
        "type": mentor_or_hacker,
        "timestamp": my_date.isoformat()
    }

    db = get_db() 
    problem_statement_doc = db.collection(
        'problem_statements').document(problem_statement_id)
    
    ps_dict = problem_statement_doc.get().to_dict()
    helping_list = []
    if "helping" in ps_dict:
        helping_list = ps_dict["helping"]
        logger.debug(f"Start Helping list: {helping_list}")

        if "helping" == helping_status:            
            helping_list.append(to_add)
        else:
            helping_list = [
                d for d in helping_list if d['user'] not in user_obj.id]            

    else:
        logger.debug(f"Start Helping list: {helping_list} * New list created for this problem")
        if "helping" == helping_status:
            helping_list.append(to_add)


    logger.debug(f"End Helping list: {helping_list}")
    problem_result = problem_statement_doc.update({
        "helping": helping_list
    })

    clear_cache()
    

    send_slack_audit(action="helping", message=user_id, payload=to_add)


    slack_user_id = user_id.split("-")[1]  # Example user_id = oauth2|slack|T1Q7116BH-U041117EYTQ
    slack_message = f"<@{slack_user_id}>"
    problem_statement_title = ps_dict["title"]
    problem_statement_slack_channel = ps_dict["slack_channel"]

    url = ""
    if npo_id == "":
        url = f"for project https://ohack.dev/project/{problem_statement_id}"
    else:
        url = f"for project https://ohack.dev/project/{problem_statement_id} and nonprofit: https://ohack.dev/nonprofit/{npo_id}"

    if "helping" == helping_status:
        slack_message = f"{slack_message} is helping as a *{mentor_or_hacker}* on *{problem_statement_title}* {url}"
    else:
        slack_message = f"{slack_message} is _no longer able to help_ on *{problem_statement_title}* {url}"

    invite_user_to_channel(user_id=slack_user_id,
                           channel_name=problem_statement_slack_channel)

    send_slack(message=slack_message,
                channel=problem_statement_slack_channel)

    return Message(
        "Updated helping status"
    )


@limits(calls=100, period=ONE_MINUTE)
def link_problem_statements_to_events(json):    
    # JSON format should be in the format of
    # problemStatementId -> [ <eventTitle1>|<eventId1>, <eventTitle2>|<eventId2> ]
    logger.debug(f"Linking payload {json}")
    
    db = get_db()  # this connects to our Firestore database
    data = json["mapping"]
    for problemId, eventList in data.items():
        problem_statement_doc = db.collection(
            'problem_statements').document(problemId)
        
        eventObsList = []
        
        for event in eventList:
            logger.info(f"Checking event: {event}")
            if "|" in event:
                eventId = event.split("|")[1]
            else:
                eventId = event
            event_doc = db.collection('hackathons').document(eventId)
            eventObsList.append(event_doc)

        logger.info(f" Events to add: {eventObsList}")
        problem_result = problem_statement_doc.update({
            "events": eventObsList
        });
        
    clear_cache()

    return Message(
        "Updated Problem Statement to Event Associations"
    )

    

@limits(calls=100, period=ONE_MINUTE)
def update_npo(json):
    db = get_db()  # this connects to our Firestore database

    logger.debug("Clearing cache")    
    clear_cache()

    logger.debug("Done Clearing cache")
    logger.debug("NPO Edit")
    send_slack_audit(action="update_npo", message="Updating", payload=json)
    
    doc_id = json["id"]
    temp_problem_statements = json["problem_statements"]

    doc = db.collection('nonprofits').document(doc_id)

    # We need to convert this from just an ID to a full object
    # Ref: https://stackoverflow.com/a/59394211
    problem_statements = []
    for ps in temp_problem_statements:
        problem_statements.append(db.collection(
            "problem_statements").document(ps))
    

    update_result = doc.update({      
        "problem_statements": problem_statements
    })

    logger.debug(f"Update Result: {update_result}")

    return Message(
        "Updated NPO"
    )


@limits(calls=50, period=ONE_MINUTE)
def save_hackathon(json):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Hackathon Save")
    send_slack_audit(action="save_hackathon", message="Saving", payload=json)
    # TODO: In this current form, you will overwrite any information that matches the same NPO name

    doc_id = uuid.uuid1().hex

    devpost_url = json["devpost_url"]
    location = json["location"]
    
    start_date = json["start_date"]
    end_date = json["end_date"]
    event_type = json["event_type"]
    image_url = json["image_url"]
    
    temp_nonprofits = json["nonprofits"]
    temp_teams = json["teams"]

    # We need to convert this from just an ID to a full object
    # Ref: https://stackoverflow.com/a/59394211
    nonprofits = []
    for ps in temp_nonprofits:
        nonprofits.append(db.collection(
            "nonprofits").document(ps))

    teams = []
    for ps in temp_teams:
        teams.append(db.collection(
            "teams").document(ps))


    collection = db.collection('hackathons')

    insert_res = collection.document(doc_id).set({
        "links":{
            "name":"DevPost",
            "link":"devpost_url"
        },        
        "location": location,
        "start_date": start_date,
        "end_date": end_date,                    
        "type": event_type,
        "image_url": image_url,
        "nonprofits": nonprofits,
        "teams": teams
    })

    logger.debug(f"Insert Result: {insert_res}")

    return Message(
        "Saved Hackathon"
    )


@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement(json):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Problem Statement Save")

    logger.debug("Clearing cache")    
    clear_cache()
    logger.debug("Done Clearing cache")


    send_slack_audit(action="save_problem_statement",
                     message="Saving", payload=json)
    # TODO: In this current form, you will overwrite any information that matches the same NPO name

    doc_id = uuid.uuid1().hex
    title = json["title"]
    description = json["description"]
    first_thought_of = json["first_thought_of"]
    github = json["github"]
    references = json["references"]
    status = json["status"]
        

    collection = db.collection('problem_statements')

    insert_res = collection.document(doc_id).set({
        "title": title,
        "description": description,
        "first_thought_of": first_thought_of,
        "github": github,
        "references": references,
        "status": status        
    })

    logger.debug(f"Insert Result: {insert_res}")

    return Message(
        "Saved Problem Statement"
    )


def get_token():
    logger.debug("get_token start")

    url = f"https://{auth0_domain}/oauth/token"
    myobj = {
        "client_id": auth0_client,
        "client_secret": auth0_secret,
        "grant_type": "client_credentials",
        "audience": f"https://{auth0_domain}/api/v2/"
    }
    x = requests.post(url, data=myobj)
    x_j = x.json()
    logger.debug("get_token end")
    return x_j["access_token"]

def get_problem_statement_from_id(problem_id):
    db = get_db()    
    doc = db.collection('problem_statements').document(problem_id)
    return doc

def get_user_from_slack_id(user_id):
    db = get_db()  # this connects to our Firestore database
    # Even though there is 1 record, we always will need to iterate on it
    docs = db.collection('users').where("user_id", "==", user_id).stream()
    
    for doc in docs:
        return doc
        
    return None


# Ref: https://stackoverflow.com/questions/59138326/how-to-set-google-firebase-credentials-not-with-json-file-but-with-python-dict
# Instead of giving the code a json file, we use environment variables so we don't have to source control a secrets file
cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))


#We don't want this to be a file, we want to use env variables for security (we would have to check in this file)
#cred = credentials.Certificate("./api/messages/ohack-dev-firebase-adminsdk-hrr2l-933367ee29.json")
cred = credentials.Certificate(cert_env)
# Check if firebase is already initialized
if not firebase_admin._apps:
    firebase_admin.initialize_app(credential=cred)


@limits(calls=50, period=ONE_MINUTE)
def save(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    logger.info(f"User Save for {user_id} {email} {last_login} {profile_image} {name} {nickname}")
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc


    if user_id is None or email is None or last_login is None or profile_image is None:
        logger.error(
            f"Empty values provided for user_id: {user_id},\
                email: {email}, or last_login: {last_login}\
                    or profile_image: {profile_image}")
        return

    db = get_db()  # this connects to our Firestore database
    
    # Even though there is 1 record, we always will need to iterate on it
    docs = db.collection('users').where("user_id","==",user_id).stream()
    
    for doc in docs:
        res = doc.to_dict()
        logger.debug(res)
        if res:
            # Found result already in DB, update
            logger.debug(f"Found user (_id={doc.id}), updating last_login")
            update_res = db.collection("users").document(doc.id).update(
                {
                    "last_login": last_login,
                    "profile_image": profile_image,
                    "name": name,
                    "nickname": nickname
            })
            logger.debug(f"Update Result: {update_res}")
        
        logger.debug("User Save End")
        return doc.id # Should only have 1 record, but break just for safety 

    default_badge = db.collection('badges').document("fU7c3ne90Rd1TB5P7NTV")

    doc_id = uuid.uuid1().hex
    insert_res = db.collection('users').document(doc_id).set({
        "email_address": email,
        "last_login": last_login,
        "user_id": user_id,
        "profile_image": profile_image,
        "name": name,
        "nickname": nickname,
        "badges": [
            default_badge
        ],
        "teams": []
    })
    logger.debug(f"Insert Result: {insert_res}")
    return doc_id
    


# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_history(db_id):
    logger.debug("Get History Start")
    db = get_db()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(db_id)
    doc_get = doc.get()
    res = doc_get.to_dict()

    _hackathons=[]
    if "hackathons" in res:
        for h in res["hackathons"]:
            rec = h.get().to_dict()
            nonprofits = []
            problem_statements = []

            for n in rec["nonprofits"]:
                npo_doc = n.get()
                npo_id = npo_doc.id
                npo = n.get().to_dict()
                npo["id"] = npo_id
                                
                if npo and "problem_statements" in npo:
                    # This is duplicate date as we should already have this
                    del npo["problem_statements"]
                nonprofits.append(npo)

                
            _hackathons.append({
                "nonprofits": nonprofits,                
                "links": rec["links"],
                "location": rec["location"],
                "start_date": rec["start_date"]
            })

    _badges=[]
    if "badges" in res:
        for h in res["badges"]:
            _badges.append(h.get().to_dict())

    result = {
        "id": doc.id,
        "user_id": res["user_id"],
        "profile_image": res["profile_image"],
        "email_address" : res["email_address"],
        "history": res["history"] if "history" in res else "",
        "badges" : _badges,
        "hackathons" : _hackathons        
    }

    logger.debug(f"RESULT\n{result}")
    return result


def get_auth0_details_by_slackid(slack_user_id):
    token = get_token()

    # Call Auth0 to get user metadata about the Slack account they used to login
    url = f"https://{auth0_domain}/api/v2/users/{slack_user_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    x = requests.get(url, headers=headers)
    x_j = x.json()

    logger.debug(f"Auth0 Metadata Response: {x_j}")

    email = x_j["email"]
    user_id = x_j["user_id"]    
    last_login = x_j["last_login"]
    profile_image = x_j["image_192"]
    name = x_j["name"]
    nickname = x_j["nickname"]
    return email, user_id, last_login, profile_image, name, nickname

def get_user_by_id(id):
    # Log
    logger.debug(f"Get User By ID: {id}")
    db = get_db()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(id)
    doc_get = doc.get()
    res = doc_get.to_dict()
    # Only keep these fields since this is a public api
    fields = ["name", "profile_image", "user_id", "nickname"]
    # Check if the field is in the response first
    res = {k: res[k] for k in fields if k in res}

    
    logger.debug(f"Get User By ID Result: {res}")
    return res    

# 10 minute cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=100, period=ONE_MINUTE)
def get_profile_metadata(slack_user_id):
    logger.debug("Profile Metadata")
    
    email, user_id, last_login, profile_image, name, nickname = get_auth0_details_by_slackid(slack_user_id)
    
    send_slack_audit(
        action="login", message=f"User went to profile: {user_id} with email: {email}")
    

    logger.debug(f"Auth0 Account Details:\
            \nEmail: {email}\nSlack User ID: {user_id}\n\
            Last Login:{last_login}\
            Image:{profile_image}")

    # Call firebase to see if account exists and save these details
    db_id = save(
            user_id=user_id,
            email=email,
            last_login=last_login,
            profile_image=profile_image,
            name=name,
            nickname=nickname)

    # Get all of the user history and profile data from the DB
    response = get_history(db_id)
    logger.debug(f"get_profile_metadata {response}")


    return Message(response)

def save_news(json):
    # Take in Slack message and summarize it using GPT-3.5
    # Make sure these fields exist title, description, links (optional), slack_ts, slack_permalink, slack_channel
    check_fields = ["title", "description", "slack_ts", "slack_permalink", "slack_channel"]
    for field in check_fields:
        if field not in json:
            logger.error(f"Missing field {field} in {json}")
            return Message("Missing field")
    upsert_news(json)

    return Message("Saved News")

@cached(cache=TTLCache(maxsize=100, ttl=32600), key=lambda news_limit: f"get_news_{news_limit}")
def get_news(news_limit=3):
    logger.debug("Get News")
    db = get_db()  # this connects to our Firestore database
    collection = db.collection('news')
    docs = collection.order_by("slack_ts", direction=firestore.Query.DESCENDING).limit(news_limit).stream()
    results = []
    for doc in docs:
        results.append(doc.to_dict())
    logger.debug(f"Get News Result: {results}")
    return Message(results)
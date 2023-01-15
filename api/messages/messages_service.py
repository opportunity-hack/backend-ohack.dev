from heapq import merge
from common.utils import safe_get_env_var, send_slack_audit, send_slack, invite_user_to_channel
from api.messages.message import Message
import json
import uuid
from datetime import datetime
import time

import logging
import firebase_admin
from firebase_admin import credentials, firestore
import requests

from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey

from ratelimit import limits
from datetime import datetime, timedelta


logger = logging.getLogger("myapp")

auth0_domain = safe_get_env_var("AUTH0_DOMAIN")
auth0_client = safe_get_env_var("AUTH0_USER_MGMT_CLIENT_ID")
auth0_secret = safe_get_env_var("AUTH0_USER_MGMT_SECRET")

ONE_MINUTE = 1*60

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


def problem_statement_key(docid, document):
    return hashkey(docid)

# 12 hour cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=43200), key=problem_statement_key)
def nonprofit_to_json(docid, d):            
    e_json = {}
    
    if not d:
        logger.error("Dict is NoneType")
        return

    if "name" in d:        
        e_json["problem_statements"] = ""  # Don't bother adding this
        e_json["id"] = docid 
        e_json["name"] = d["name"]
    return e_json


@cached(cache=TTLCache(maxsize=100, ttl=43200), key=problem_statement_key)
def events_to_json(docid, d):
    events = []
    if "events" in d:
        logger.debug("Events:")
        logger.debug(d["events"])

        for e in d["events"]:            
            logger.debug("Event:")
            logger.debug(e)
            

            e_doc = e.get()
            if not e or not e_doc or not e_doc.to_dict():
                logger.warning("No event object found for this reference in the DB")
            else:
                e_json = e_doc.to_dict()
                logger.debug("Event JSON:")
                logger.debug(e_json)

                e_json["id"] = e.id            
                e_json["nonprofits"] = ""  # Don't bother adding this

                _teams = []
                # You want to try and handle cases where the DB might have some weird stuff in it
                # This is defensive coding
                teamsa = e_json["teams"] if "teams" in e_json else []
                if teamsa:
                    for t in teamsa:
                        for t in e_json["teams"]:
                            try:
                                t_doc = t.get()
                                t_dict = t_doc.to_dict()
                                _teams.append({
                                    "id": t_doc.id,
                                    "name": t_dict["name"],
                                    "active": t_dict["active"],
                                    "slack_channel": t_dict["slack_channel"],
                                    "users" : users_to_json(f"t_doc.id_users", t_dict["users"])
                                })
                            except e:
                                logger.debug(f"Could not add this to teams list: {e}")

                logger.debug(f"Teams: {teamsa}")
                e_json["teams"] = _teams  # Don't bother adding this
                events.append(e_json)
    return events


@cached(cache=TTLCache(maxsize=100, ttl=43200), key=problem_statement_key)
def users_to_json(docid, d):
    users = []
    if "users" in d:
        for u in d["users"]:            
            u_doc = u.get()
            u_json = u_doc.to_dict()
            u_json["id"] = u.id
            u_json["slack_id"] = u_json["user_id"]
            u_json["profile_image"] = u_json["profile_image"]
            u_json["badges"] = "" # Don't bother adding this
            u_json["teams"] = ""  # Don't bother adding this
            users.append(u_json)
    return users



def problem_statement_to_json(ps):    
    logger.debug("Problem Statement:")
    logger.debug(ps)

    ps_doc = ps.get()
    ps_json = ps_doc.to_dict()
    ps_json["id"] = ps_doc.id
    logger.debug(f"* Found Problem Statement {ps_doc.id}")

    event_list = []
    if "events" in ps_json:                
        for e in ps_json["events"]:
            logger.debug("Events: ")
            logger.debug(ps_json["events"])

            event_doc = e.get()
            event_id = event_doc.id 
            logger.debug(f"Event ID: {event_id}")
            
            event = event_doc.to_dict()
            
            if not event:
                logger.warning(f"Unable to find event reference for problem statement {ps_doc.id}")
                continue

            team_list = []
            if "teams" in event:
                for t in event["teams"]:
                    logger.debug("Teams:")
                    logger.debug(event["teams"])

                    team_doc = t.get()
                    team_id = team_doc.id 
                    logger.debug(f"Team ID: {team_id}")

                    team = team_doc.to_dict()
                    user_list = []
                    for u in team["users"]:
                        logger.debug("Users Within Team: ")
                        logger.debug(team["users"])
                        user_doc = u.get()
                        user_doc_json = user_doc.to_dict()
                        user_list.append({
                                    "user_id": user_doc.id,
                                    "slack_id": user_doc_json["user_id"],
                                    "profile_image": user_doc_json["profile_image"],
                                    "name": user_doc_json["name"] if "name" in user_doc_json else "",
                                    "nickname": user_doc_json["nickname"] if "nickname" in user_doc_json else ""
                                }
                            )

                    team_problem_statements = []
                    for p in team["problem_statements"]:
                        problem_statement_doc = p.get()
                        team_problem_statements.append(problem_statement_doc.id)
                        #team_problem_statements.append(
                        #    {"id": problem_statement_doc.id})


                    slack_channel = team["slack_channel"] if "slack_channel" in team else ""
                    team_list.append({
                        "id": team_doc.id,
                        "active": team["active"],
                        "name": team["name"],
                        "slack_channel": slack_channel,
                        "github_links": team["github_links"],
                        "team_number": team["team_number"],
                        "users": user_list,
                        "problem_statements": team_problem_statements
                    }
                    )

            event_list.append({
                "id": event_doc.id,
                "teams": team_list,
                "type": event["type"],
                "location": event["location"],
                
                "devpost_url": event["devpost_url"] if "devpost_url" in event else "",
                "links": event["links"] if "links" in event else "",
                "start_date": event["start_date"],
                "end_date": event["end_date"],
                "image_url": event["image_url"]                        
            }
            )
    ps_json["events"] = event_list

    ps_json["description"] = ps_json["description"]
    return ps_json

# 12 hour cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=43200), key=problem_statement_key)
def problem_statements_to_json(docid, d):
    problem_statements = []

    if "problem_statements" in d:
        start = time.time()
        for ps in d["problem_statements"]:            
            ps_json = problem_statement_to_json(ps)
            problem_statements.append(ps_json)  

        total_time = time.time() - start             
        logger.debug(f"{total_time} sec")

    return problem_statements


def get_db():
    #mock_db = MockFirestore()
    return firestore.client()


def get_single_problem_statement(project_id):
    logger.debug(f"get_single_problem_statement start project_id={project_id}")    
    db = get_db()      
    doc = db.collection('problem_statements').document(project_id)
    
    if doc is None:
        logger.warning("get_single_problem_statement end (no results)")
        return {}
    else:                                
        result = problem_statement_to_json(doc)
        result["id"] = doc.id
        

        logger.debug(f"get_single_problem_statement end (with result):{result}")
        return result
    return {}
    

# 12 hour cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=43200))
@limits(calls=100, period=ONE_MINUTE)
def get_single_npo(npo_id):    
    logger.debug(f"get_npo start npo_id={npo_id}")    
    db = get_db()      
    doc = db.collection('nonprofits').document(npo_id)    
    
    if doc is None:
        logger.warning("get_npo end (no results)")
        return {}
    else:                        
        d_doc = doc.get()
        d = d_doc.to_dict()
        result = d
        result["id"] = doc.id
        result["problem_statements"] = problem_statements_to_json(d_doc.id, d)

        logger.debug(f"get_npo end (with result):{result}")
        return {
            "nonprofits": result
        }
    return {}


@limits(calls=100, period=ONE_MINUTE)
def get_hackathon_list(is_current_only):
    logger.debug("Hackathon List Start")
    db = get_db()
    
    if is_current_only:        
        N_DAYS_LOOK_FORWARD = 4*30
        today = datetime.now()        
        today_str = today.strftime("%Y-%m-%d")
        logger.debug(
            f"Looking for any event that finishes after today {today_str} for most current events only.")
        docs = db.collection('hackathons').where("end_date", ">=", today_str).order_by(
            "end_date").stream()  # steam() gets all records
    else:
        docs = db.collection('hackathons').order_by("start_date").stream()  # steam() gets all records
    
    
    if docs is None:
        logger.debug("Found no results, returning empty list")
        return {[]}
    else:
        results = []
        for doc in docs:
            d_doc = doc
            d = d_doc.to_dict()

            nonprofits = []
            if "nonprofits" in d:
                logger.debug("Found these nonprofits:")
                logger.debug(d["nonprofits"])
                for npo in d["nonprofits"]:                    
                    npo_doc = npo.get()
                    logger.debug(npo_doc.id)
                    nonprofits.append(nonprofit_to_json(
                        npo_doc.id, npo_doc.to_dict()))

            results.append(
                {
                    "id": doc.id,
                    "type": d["type"],
                    "location": d["location"],                    
                    "links": d["links"] if "links" in d else "",
                    "start_date": d["start_date"],
                    "end_date": d["end_date"],
                    "image_url": d["image_url"],
                    "title": d["title"] if "title" in d else "",                    
                    "donation_goals": d["donation_goals"] if "donation_goals" in d else "",
                    "donation_current": d["donation_current"] if "donation_current" in d else "",

                    "nonprofits": nonprofits,
                    "teams":[] #TODO
                }

            )
        num_results = len(results)
        logger.debug(f"Found {num_results} results")
        logger.debug(f"Hackathon List End")
        return {"hackathons": results}

def save_team(json):
    send_slack_audit(action="save_team", message="Saving", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("Team Save")    

    logger.debug(json)
    doc_id = uuid.uuid1().hex # Generate a new team id

    name = json["name"]    
    slack_user_id = json["userId"]
    event_id = json["eventId"]
    slack_channel = json["slackChannel"]
    problem_statement_id = json["problemStatementId"]


    user = get_user_from_slack_id(slack_user_id).reference
    if user is None:
        return

    problem_statement = get_problem_statement_from_id(problem_statement_id)
    if problem_statement is None:
        return
    
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
                "link": "",
                "name": ""
            }
        ]
    })

    logger.debug(f"Insert Result: {insert_res}")

    # Look up the new team object that was just created
    new_team_doc = db.collection('teams').document(doc_id)
    user_dict = user.get().to_dict()
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


    clear_cache()

    return {
        "message":"Saved Team",
        "team": {
            "name": name,
            "slack_channel": slack_channel
        },
        "user": {
            "name" : user_dict["name"],
            "profile_image": user_dict["profile_image"],
        }
        }
        

def join_team(userid, json):
    send_slack_audit(action="join_team", message="Adding", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("Join Team Start")

    logger.debug(f"UserId: {userid} Json: {json}")
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

    problem_statements_to_json.cache_clear()
    users_to_json.cache_clear()

    logger.debug("Join Team End")
    return Message(
    "Joined Team")




def unjoin_team(userid, json):
    send_slack_audit(action="unjoin_team", message="Removing", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("Unjoin Team Start")
    
    logger.debug(f"UserId: {userid} Json: {json}")
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
            user_dict = u.get().to_dict()

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
        clear_cache()
            

    logger.debug("Unjoin Team End")

    return Message(
        "Removed from Team")

    


@limits(calls=100, period=ONE_MINUTE)
def get_teams_list():
    logger.debug("Teams List Start")
    db = get_db()  
    docs = db.collection('teams').stream() # steam() gets all records   
    if docs is None:
        return {[]}
    else:                
        results = []
        for doc in docs:
            d_doc = doc
            d = d_doc.to_dict()

            results.append(
                {
                    "id": doc.id,
                    "name": d["name"],
                    "active": d["active"],
                    "slack_channel": d["slack_channel"],
                    "github_links": d["github_links"],
                    "team_number": d["team_number"],
                    "users": users_to_json(d_doc.id, d),
                    "problem_statements": problem_statements_to_json(d_doc.id, d)
                }
                    
            )
        logger.debug(f"Teams List End")
        return { "teams": results }



@limits(calls=20, period=ONE_MINUTE)
@cached(cache=TTLCache(maxsize=100, ttl=43200))  # 12 hr cache
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
            d_doc = doc
            d = d_doc.to_dict()
            npo_name = d["name"]

            logger.debug(f"Nonprofit Name: {npo_name}")            

            description = d["description"]
            words = description.split(" ")
            only_first_words = " ".join(words[0:word_length])
            if len(only_first_words) < len(description):
                only_first_words = only_first_words.rstrip(',').strip() + "..."

            slack_channel = d["slack_channel"] if "slack_channel" in d else ""
            results.append(
                {
                    "id": doc.id,
                    "name": d["name"],
                    "description": only_first_words,
                    "slack_channel": slack_channel,
                    "website": d["website"],
                    "contact_people": ", ".join(d["contact_people"]),
                    "problem_statements": problem_statements_to_json(d_doc.id, d)
                }
                    
            )
        logger.debug(f"NPO List End")
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
            d_doc = doc
            d = d_doc.to_dict()

            slack_channel = d["slack_channel"] if "slack_channel" in d else ""
            github = d["github"] if "github" in d else ""
            helping_list = d["helping"] if "helping" in d else ""
            

            results.append(
                {
                    "id": doc.id,
                    "title": d["title"],
                    "slack_channel": slack_channel,
                    "description": d["description"],
                    "first_thought_of": d["first_thought_of"],
                    "github": github,
                    "references": d["references"],
                    "status": d["status"],
                    "helping": helping_list,
                    "events": events_to_json(d_doc.id, d)
                }

            )
        return { "problem_statements": results }


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
    problem_statements_to_json.cache_clear()
    get_single_npo.cache_clear()
    get_npo_list.cache_clear()
    events_to_json.cache_clear()
    get_profile_metadata.cache_clear()
    users_to_json.cache_clear()

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
    npo_id = json["npo_id"]
    
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
    if "helping" == helping_status:
        slack_message = f"{slack_message} is helping as a *{mentor_or_hacker}* on *{problem_statement_title}* https://ohack.dev/nonprofit/{npo_id}"
    else:
        slack_message = f"{slack_message} is _no longer able to help_ on *{problem_statement_title}* https://ohack.dev/nonprofit/{npo_id}"

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
            print(f"Checking event: {event}")
            if "|" in event:
                eventId = event.split("|")[1]
            else:
                eventId = event
            event_doc = db.collection('hackathons').document(eventId)
            eventObsList.append(event_doc)

        print(f" Events to add: {eventObsList}")
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
    problem_statements_to_json.cache_clear()
    get_single_npo.cache_clear()

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
    problem_statements_to_json.cache_clear()
    get_single_npo.cache_clear()
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
firebase_admin.initialize_app(credential=cred)


@limits(calls=50, period=ONE_MINUTE)
def save(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    logger.debug("User Save Start")
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
        print(res)        
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
    logger.debug("Get Hackathons Start")
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
                npo_r = n.get().to_dict()
                
                if npo_r and "problem_statements" in npo_r:
                    # This is duplicate date as we should already have this
                    del npo_r["problem_statements"]
                nonprofits.append(npo_r)
            for ps in rec["problem_statements"]:
                problem_statements.append(ps.get().to_dict())

            _hackathons.append({
                "nonprofits": nonprofits,
                "problem_statements": problem_statements,
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

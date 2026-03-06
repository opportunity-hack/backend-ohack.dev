from common.utils import safe_get_env_var
from common.utils.slack import send_slack_audit, send_slack, async_send_slack, invite_user_to_channel, get_user_info
from common.utils.firebase import upsert_news, upsert_praise, get_github_contributions_for_user, get_user_by_user_id, get_recent_praises, get_praises_by_user_id
from common.utils.openai_api import generate_and_save_image_to_cdn
from api.messages.message import Message
from google.cloud.exceptions import NotFound

from services.users_service import get_propel_user_details_by_id, get_slack_user_from_propel_user_id, get_user_from_slack_id, save_user
import json
import uuid
from datetime import datetime, timedelta
import pytz
import time
from functools import wraps

from common.log import get_logger, info, debug, warning, error, exception
import firebase_admin
from firebase_admin.firestore import DocumentReference, DocumentSnapshot
from firebase_admin import credentials, firestore
from common.utils.validators import validate_email, validate_url, validate_hackathon_data
from common.exceptions import InvalidInputError

from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey

from ratelimit import limits
from datetime import datetime, timedelta
import os

from db.db import fetch_user_by_user_id, get_db
import resend


logger = get_logger("messages_service")

resend_api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")
if not resend_api_key:
    logger.error("RESEND_WELCOME_EMAIL_KEY not set")    
else:
    resend.api_key = resend_api_key

CDN_SERVER = os.getenv("CDN_SERVER")
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

from common.utils.firestore_helpers import (
    hash_key,
    log_execution_time,
    doc_to_json,
    doc_to_json_recursive,
    clear_all_caches as _clear_all_caches,
)





def clear_cache():
    from services.hackathons_service import clear_cache as _hackathon_clear_cache
    _hackathon_clear_cache()

# Ref: https://stackoverflow.com/questions/59138326/how-to-set-google-firebase-credentials-not-with-json-file-but-with-python-dict
# Instead of giving the code a json file, we use environment variables so we don't have to source control a secrets file
cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))


#We don't want this to be a file, we want to use env variables for security (we would have to check in this file)
#cred = credentials.Certificate("./api/messages/ohack-dev-firebase-adminsdk-hrr2l-933367ee29.json")
cred = credentials.Certificate(cert_env)
# Check if firebase is already initialized
if not firebase_admin._apps:
    firebase_admin.initialize_app(credential=cred)

def save_news(json):
    # Take in Slack message and summarize it using GPT-3.5
    # Make sure these fields exist title, description, links (optional), slack_ts, slack_permalink, slack_channel
    check_fields = ["title", "description", "slack_ts", "slack_permalink", "slack_channel"]
    for field in check_fields:
        if field not in json:
            logger.error(f"Missing field {field} in {json}")
            return Message("Missing field")
        
    cdn_dir = "ohack.dev/news"
    news_image = generate_and_save_image_to_cdn(cdn_dir,json["title"])
    json["image"] = f"{CDN_SERVER}/{cdn_dir}/{news_image}"
    json["last_updated"] = datetime.now().isoformat()
    upsert_news(json)

    logger.info("Updated news successfully")

    get_news.cache_clear()
    logger.info("Cleared cache for get_news")

    return Message("Saved News")

def save_praise(json):
    # Make sure these fields exist praise_receiver, praise_channel, praise_message
    check_fields = ["praise_receiver", "praise_channel", "praise_message"]
    for field in check_fields:
        if field not in json:
            logger.error(f"Missing field {field} in {json}")
            return Message("Missing field")
        
    logger.debug(f"Detected required fields, attempting to save praise")
    json["timestamp"] = datetime.now(pytz.utc).astimezone().isoformat()
    
    # Add ohack.dev user IDs for both sender and receiver
    try:
        # Get ohack.dev user ID for praise receiver
        receiver_user = get_user_by_user_id(json["praise_receiver"])
        if receiver_user and "id" in receiver_user:
            json["praise_receiver_ohack_id"] = receiver_user["id"]
            logger.debug(f"Added praise_receiver_ohack_id: {receiver_user['id']}")
        else:
            logger.warning(f"Could not find ohack.dev user for praise_receiver: {json['praise_receiver']}")
            json["praise_receiver_ohack_id"] = None
            
        # Get ohack.dev user ID for praise sender
        sender_user = get_user_by_user_id(json["praise_sender"])
        if sender_user and "id" in sender_user:
            json["praise_sender_ohack_id"] = sender_user["id"]
            logger.debug(f"Added praise_sender_ohack_id: {sender_user['id']}")
        else:
            logger.warning(f"Could not find ohack.dev user for praise_sender: {json['praise_sender']}")
            json["praise_sender_ohack_id"] = None
            
    except Exception as e:
        logger.error(f"Error getting ohack.dev user IDs: {str(e)}")
        json["praise_receiver_ohack_id"] = None
        json["praise_sender_ohack_id"] = None
    
    logger.info(f"Attempting to save the praise with the json object {json}")
    upsert_praise(json)

    logger.info("Updated praise successfully")

    get_praises_about_user.cache_clear()
    logger.info("Cleared cache for get_praises_by_user_id")

    get_all_praises.cache_clear()
    logger.info("Cleared cache for get_all_praises")

    return Message("Saved praise")


@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_all_praises():    
    # Get the praises about user with user_id
    results = get_recent_praises()

    # Get unique list of praise_sender and praise_receiver    
    slack_ids = set()
    for r in results:
        slack_ids.add(r["praise_receiver"])
        slack_ids.add(r["praise_sender"])

    logger.info(f"SlackIDS: {slack_ids}")    
    slack_user_info = get_user_info(slack_ids)
    logger.info(f"Slack User Info; {slack_user_info}")

    for r in results:
        r['praise_receiver_details'] = slack_user_info[r['praise_receiver']]
        r['praise_sender_details'] = slack_user_info[r['praise_sender']]
    
    logger.info(f"Here are the 20 most recently written praises: {results}")
    return Message(results)    

@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_praises_about_user(user_id):
    
    # Get the praises about user with user_id
    results = get_praises_by_user_id(user_id)

    slack_ids = set()
    for r in results:
        slack_ids.add(r["praise_receiver"])
        slack_ids.add(r["praise_sender"])
    logger.info(f"Slack IDs: {slack_ids}")
    slack_user_info = get_user_info(slack_ids)
    logger.info(f"Slack User Info: {slack_user_info}")
    for r in results:
        r['praise_receiver_details'] = slack_user_info[r['praise_receiver']]
        r['praise_sender_details'] = slack_user_info[r['praise_sender']]

    logger.info(f"Here are all praises related to {user_id}: {results}")
    return Message(results)    

# -------------------- Praises methods end here --------------------------- #

@cached(cache=TTLCache(maxsize=100, ttl=32600), key=lambda news_limit, news_id: f"{news_limit}-{news_id}")
def get_news(news_limit=3, news_id=None):
    logger.debug("Get News")
    db = get_db()  # this connects to our Firestore database
    if news_id is not None:
        logger.info(f"Getting single news item for news_id={news_id}")
        collection = db.collection('news')
        doc = collection.document(news_id).get()
        if doc is None:
            return Message({})
        else:
            return Message(doc.to_dict())
    else:
        collection = db.collection('news')
        docs = collection.order_by("slack_ts", direction=firestore.Query.DESCENDING).limit(news_limit).stream()
        results = []
        for doc in docs:
            doc_json = doc.to_dict()
            doc_json["id"] = doc.id
            results.append(doc_json)
        logger.debug(f"Get News Result: {results}")
        return Message(results)

# --------------------------- Problem Statement functions to be deleted -----------------  #
@limits(calls=100, period=ONE_MINUTE)
def save_helping_status_old(propel_user_id, json):
    logger.info(f"save_helping_status {propel_user_id} // {json}")
    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    user_id = slack_user["sub"]

    helping_status = json["status"] # helping or not_helping
    
    problem_statement_id = json["problem_statement_id"]
    mentor_or_hacker = json["type"]

    npo_id =  json["npo_id"] if "npo_id" in json else ""
    
    user_obj = fetch_user_by_user_id(user_id)
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

    if "slack_channel" in ps_dict:
        problem_statement_slack_channel = ps_dict["slack_channel"]

        url = ""
        if npo_id == "":
            url = f"for project https://ohack.dev/project/{problem_statement_id}"
        else:
            url = f"for nonprofit https://ohack.dev/nonprofit/{npo_id} on project https://ohack.dev/project/{problem_statement_id}"

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

@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement_old(json):
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

def get_problem_statement_from_id_old(problem_id):
    db = get_db()    
    doc = db.collection('problem_statements').document(problem_id)
    return doc

@cached(cache=TTLCache(maxsize=100, ttl=600))
def get_single_problem_statement_old(project_id):
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

@limits(calls=100, period=ONE_MINUTE)
def get_problem_statement_list_old():
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

@cached(cache=TTLCache(maxsize=100, ttl=10))
@limits(calls=100, period=ONE_MINUTE)
def get_github_profile(github_username):
    logger.debug(f"Getting Github Profile for {github_username}")

    return {
        "github_history": get_github_contributions_for_user(github_username)
    }


# -------------------- User functions to be deleted ---------------------------------------- #

# 10 minute cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=100, period=ONE_MINUTE)
def get_profile_metadata_old(propel_id):
    logger.debug("Profile Metadata")

    email, user_id, last_login, profile_image, name, nickname = get_propel_user_details_by_id(propel_id)

    send_slack_audit(
        action="login", message=f"User went to profile: {user_id} with email: {email}")


    logger.debug(f"Account Details:\
            \nEmail: {email}\nSlack User ID: {user_id}\n\
            Last Login:{last_login}\
            Image:{profile_image}")

    # Call firebase to see if account exists and save these details
    db_id = save_user_old(
            user_id=user_id,
            email=email,
            last_login=last_login,
            profile_image=profile_image,
            name=name,
            nickname=nickname,
            propel_id=propel_id
            )

    # Get all of the user history and profile data from the DB
    response = get_history_old(db_id)
    logger.debug(f"get_profile_metadata {response}")


    return {
        "text" : response
    }


def get_all_profiles():
    db = get_db()
    docs = db.collection('users').stream()  # steam() gets all records
    if docs is None:
        return {[]}
    else:
        results = []
        for doc in docs:
            results.append(doc_to_json(docid=doc.id, doc=doc))

    # log result
    logger.info(results)        
    return { "profiles": results }


# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_history_old(db_id):
    logger.debug("Get History Start")

    # Check if db_id is None first
    if db_id is None:
        logger.error("get_history_old called with None db_id")
        return None

    db = get_db()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(db_id)
    doc_get = doc.get()

    # Check if document exists before calling to_dict()
    if not doc_get.exists:
        logger.warning(f"User document not found for db_id: {db_id}")
        return None

    res = doc_get.to_dict()

    # Additional safety check in case to_dict() returns None
    if res is None:
        logger.warning(f"User document exists but to_dict() returned None for db_id: {db_id}")
        return None

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
        "hackathons" : _hackathons,
        "expertise": res["expertise"] if "expertise" in res else "",
        "education": res["education"] if "education" in res else "",
        "shirt_size": res["shirt_size"] if "shirt_size" in res else "",
        "linkedin_url": res["linkedin_url"] if "linkedin_url" in res else "",
        "instagram_url": res["instagram_url"] if "instagram_url" in res else "",        
        "github": res["github"] if "github" in res else "",
        "why": res["why"] if "why" in res else "",
        "role": res["role"] if "role" in res else "",
        "company": res["company"] if "company" in res else "",
        "propel_id": res["propel_id"] if "propel_id" in res else "",
        "street_address": res["street_address"] if "street_address" in res else "",
        "street_address_2": res["street_address_2"] if "street_address_2" in res else "",
        "city": res["city"] if "city" in res else "",
        "state": res["state"] if "state" in res else "",
        "postal_code": res["postal_code"] if "postal_code" in res else "",
        "country": res["country"] if "country" in res else "",
        "want_stickers": res["want_stickers"] if "want_stickers" in res else "",
    }

    # Clear cache    

    logger.debug(f"RESULT\n{result}")
    return result


@limits(calls=50, period=ONE_MINUTE)
def save_user_old(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None,
        propel_id=None
        ):
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
                    "nickname": nickname,
                    "propel_id": propel_id,
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
        "teams": [],
        "propel_id": propel_id,
    })
    logger.debug(f"Insert Result: {insert_res}")
    return doc_id

def save_profile_metadata_old(propel_id, json):
    send_slack_audit(action="save_profile_metadata", message="Saving", payload=json)
    db = get_db()  # this connects to our Firestore database
    oauth_user = get_slack_user_from_propel_user_id(propel_id)
    if oauth_user is None:
        logger.warning(f"Could not get OAuth user details for propel_id: {propel_id}")
        return None
    oauth_user_id = oauth_user["sub"]

    logger.info(f"Save Profile Metadata for {oauth_user_id} {json}")

    json = json["metadata"]

    # See if the user exists
    user = get_user_from_slack_id(oauth_user_id)
    if user is None:
        return
    else:
        logger.info(f"User exists: {user.id}")        

    # Only update metadata that is in the json
    metadataList = [
        "role", "expertise", "education", "company", "why", "shirt_size", "github", "linkedin_url", "instagram_url", "propel_id",
        "street_address", "street_address_2", "city", "state", "postal_code", "country", "want_stickers"
        ]

    d = {}

    for m in metadataList:        
        if m in json:
            d[m] = json[m]

    logger.info(f"Metadata: {d}")
    update_res = db.collection("users").document(user.id).set( d, merge=True)

    logger.info(f"Update Result: {update_res}")

    # Clear cache for get_profile_metadata
    get_profile_metadata_old.cache_clear()
    get_user_by_id_old.cache_clear()

    return Message(
        "Saved Profile Metadata"
    )

@cached(cache=TTLCache(maxsize=100, ttl=600), key=lambda id: id)
def get_user_by_id_old(id):
    logger.debug(f"Attempting to get user by ID: {id}")
    db = get_db()
    doc_ref = db.collection('users').document(id)

    try:
        doc = doc_ref.get()
        if not doc.exists:
            logger.warning(f"User with ID {id} not found")
            return {}

        fields = ["name", "profile_image", "user_id", "nickname", "github"]
        res = {}
        for field in fields:
            try:
                value = doc.get(field)
                if value is not None:
                    res[field] = value
            except KeyError:
                logger.info(f"Field '{field}' not found for user {id}")

        res["id"] = doc.id
        logger.debug(f"Successfully retrieved user data: {res}")
        return res

    except NotFound:
        logger.error(f"Document with ID {id} not found in 'users' collection")
        return {}
    except Exception as e:
        logger.error(f"Error retrieving user data for ID {id}: {str(e)}")
        return {}


@limits(calls=50, period=ONE_MINUTE)
def save_feedback(propel_user_id, json):
    db = get_db()
    logger.info("Saving Feedback")
    send_slack_audit(action="save_feedback", message="Saving", payload=json)

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    user_db_id = get_user_from_slack_id(slack_user["sub"]).id 
    feedback_giver_id = slack_user["sub"]

    doc_id = uuid.uuid1().hex
    feedback_receiver_id = json.get("feedback_receiver_id")
    relationship = json.get("relationship")
    duration = json.get("duration")
    confidence_level = json.get("confidence_level")
    is_anonymous = json.get("is_anonymous", False)
    feedback_data = json.get("feedback", {})

    collection = db.collection('feedback')
    
    insert_res = collection.document(doc_id).set({
        "feedback_giver_slack_id": feedback_giver_id,
        "feedback_giver_id": user_db_id,
        "feedback_receiver_id": feedback_receiver_id,
        "relationship": relationship,
        "duration": duration,
        "confidence_level": confidence_level,
        "is_anonymous": is_anonymous,
        "feedback": feedback_data,
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Insert Result: {insert_res}")
    
    
    notify_feedback_receiver(feedback_receiver_id)

    # Clear cache
    get_user_feedback.cache_clear()

    return Message("Feedback saved successfully")

def notify_feedback_receiver(feedback_receiver_id):
    db = get_db()
    user_doc = db.collection('users').document(feedback_receiver_id).get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        user_email = user_data.get('email_address', '')
        slack_user_id = user_data.get('user_id', '').split('-')[-1]  # Extract Slack user ID
        logger.info(f"User with ID {feedback_receiver_id} found")
        logger.info(f"Sending notification to user {slack_user_id}")
        
        message = (
            f"Hello <@{slack_user_id}>! You've received new feedback. "
            "Visit https://www.ohack.dev/myfeedback to view it."
        )
        
        # Send Slack message
        send_slack(message=message, channel=slack_user_id)
        logger.info(f"Notification sent to user {slack_user_id}")

        # Also send an email notification if user_email is available
        if user_email:
            subject = "New Feedback Received"
            # Think like a senior UX person and re-use the email template from the welcome email and send using resend
            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>New Feedback Received</title>    
            </head>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
                <h1 style="color: #0088FE;">New Feedback Received</h1>
                <p>Hello,</p>
                <p>You have received new feedback. Please visit <a href="https://www.ohack.dev/myfeedback">My Feedback</a> to view it.</p>
                <p>Thank you for being a part of the Opportunity Hack community!</p>
                <p>Best regards,<br>The Opportunity Hack Team</p>
            </body>
            </html>
            """
            params = {
                "from": "Opportunity Hack <welcome@notifs.ohack.org>",
                "to": f"{user_data.get('name', 'User')} <{user_email}>",
                "bcc": "greg@ohack.org",
                "subject": subject,
                "html": html_content,
            }
            email = resend.Emails.SendParams(params)
            resend.Emails.send(email)
            logger.info(f"Email notification sent to {user_email}")
    else:
        logger.warning(f"User with ID {feedback_receiver_id} not found")

@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=100, period=ONE_MINUTE)
def get_user_feedback(propel_user_id):
    logger.info(f"Getting feedback for propel_user_id: {propel_user_id}")    
    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    db_user_id = get_user_from_slack_id(slack_user["sub"]).id
    
    feedback_docs = db.collection('feedback').where("feedback_receiver_id", "==", db_user_id).order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
    
    feedback_list = []
    for doc in feedback_docs:
        feedback = doc.to_dict()
        if feedback.get("is_anonymous", False):
            if "feedback_giver_id" in feedback:
                feedback.pop("feedback_giver_id")
            if "feedback_giver_slack_id" in feedback:
                feedback.pop("feedback_giver_slack_id")
        feedback_list.append(feedback)
    
    return {"feedback": feedback_list}


def save_giveaway(propel_user_id, json):
    db = get_db()
    logger.info("Submitting Giveaway")
    send_slack_audit(action="submit_giveaway", message="Submitting", payload=json)

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    user_db_id = get_user_from_slack_id(slack_user["sub"]).id 
    giveaway_id = json.get("giveaway_id")
    giveaway_data = json.get("giveaway_data", {})
    entries = json.get("entries", 0)

    collection = db.collection('giveaways')
    
    doc_id = uuid.uuid1().hex
    insert_res = collection.document(doc_id).set({
        "user_id": user_db_id,
        "giveaway_id": giveaway_id,
        "entries": entries,
        "giveaway_data": giveaway_data,
        "timestamp": datetime.now().isoformat()
    })

    logger.info(f"Insert Result: {insert_res}")

    return Message("Giveaway submitted successfully")

def get_user_giveaway(propel_user_id):
    logger.info(f"Getting giveaway for propel_user_id: {propel_user_id}")    
    db = get_db()

    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    db_user_id = get_user_from_slack_id(slack_user["sub"]).id
    
    giveaway_docs = db.collection('giveaways').where("user_id", "==", db_user_id).stream()
    
    giveaway_list = []
    for doc in giveaway_docs:
        giveaway = doc.to_dict()
        giveaway_list.append(giveaway)
    
    return { "giveaways" : giveaway_list}

def get_all_giveaways():
    logger.info("Getting all giveaways")
    db = get_db()
    # Order by timestamp in descending order
    docs = db.collection('giveaways').order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
   
    # Get the most recent giveaway for each user
    giveaways = {}
    for doc in docs:
        giveaway = doc.to_dict()
        user_id = giveaway["user_id"]
        if user_id not in giveaways:
            user = get_user_by_id_old(user_id)
            giveaway["user"] = user
            giveaways[user_id] = giveaway



    return { "giveaways" : list(giveaways.values()) }


def upload_image_to_cdn(request):
    """
    Upload an image to CDN. Accepts binary data, base64, or standard image formats.
    Returns the CDN URL of the uploaded image.
    """
    import base64
    import tempfile
    import mimetypes
    from werkzeug.utils import secure_filename
    from common.utils.cdn import upload_to_cdn
    
    logger.info("Starting image upload to CDN")
    
    try:
        # Check if file is in request.files (multipart/form-data)
        if 'file' in request.files:
            _directory = request.form.get("directory", "images")
            _filename = request.form.get("filename", None)
            logger.debug("Processing multipart file upload")
            file = request.files['file']
            if file.filename == '':
                logger.warning("Upload failed: No file selected")
                return {"success": False, "error": "No file selected"}, 400
            
            filename = secure_filename(_filename or file.filename)
            if not filename:
                logger.warning(f"Upload failed: Invalid filename provided: {file.filename}")
                return {"success": False, "error": "Invalid filename"}, 400
            
            logger.debug(f"Processing file upload: {filename}")
            
            # Check if it's an image
            if not _is_image_file(filename):
                logger.warning(f"Upload failed: File is not an image: {filename}")
                return {"success": False,"error": "File must be an image"}, 400
            
            # Create a properly named temporary file
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_filepath = os.path.join(temp_dir, filename)
            
            logger.debug(f"Saving file to temporary location: {temp_filepath}")
            file.save(temp_filepath)

            # Get just the file name without the path as the destination
            destination_filename = os.path.basename(temp_filepath)
            logger.debug(f"Destination filename for CDN upload: {destination_filename}")
            
            try:
                # Upload to CDN using the properly named temp file
                logger.info(f"Uploading {filename} to CDN from {temp_filepath}")
                cdn_url = upload_to_cdn(_directory, temp_filepath, destination_filename)
                
                logger.info(f"Successfully uploaded image to CDN: {cdn_url}")
                return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
            finally:
                # Clean up temp file
                if os.path.exists(temp_filepath):
                    os.unlink(temp_filepath)
                    logger.debug(f"Cleaned up temporary file: {temp_filepath}")
        
        # Check if data is in JSON body (base64 or binary)
        elif request.is_json:
            logger.debug("Processing JSON request")
            data = request.get_json()
            
            if 'base64' in data:
                logger.debug("Processing base64 encoded image")
                # Handle base64 encoded image
                base64_data = data['base64']
                filename = data.get('filename', 'uploaded_image.png')
                
                logger.debug(f"Processing base64 image with filename: {filename}")
                
                # Remove data URL prefix if present
                if base64_data.startswith('data:image'):
                    logger.debug("Removing data URL prefix from base64 string")
                    base64_data = base64_data.split(',')[1]
                
                # Decode base64
                try:
                    image_data = base64.b64decode(base64_data)
                    logger.debug(f"Successfully decoded base64 data, size: {len(image_data)} bytes")
                except Exception as e:
                    logger.error(f"Failed to decode base64 data: {str(e)}")
                    return {"success": False, "error": "Invalid base64 data"}, 400
                
                filename = secure_filename(filename)
                if not _is_image_file(filename):
                    logger.warning(f"Upload failed: File is not an image: {filename}")
                    return {"success": False, "error": "File must be an image"}, 400
                
                # Create a properly named temporary file
                temp_dir = tempfile.gettempdir()
                temp_filepath = os.path.join(temp_dir, filename)
                
                logger.debug(f"Saving base64 image to temporary file: {temp_filepath}")
                with open(temp_filepath, 'wb') as temp_file:
                    temp_file.write(image_data)
                
                # Get just the file name without the path as the destination
                destination_filename = os.path.basename(temp_filepath)
                logger.debug(f"Destination filename for CDN upload: {destination_filename}")

                try:
                    # Upload to CDN using the properly named temp file
                    logger.info(f"Uploading base64 image {filename} to CDN from {temp_filepath}")
                    cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                    
                    logger.info(f"Successfully uploaded base64 image to CDN: {cdn_url}")
                    return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
                finally:
                    # Clean up temp file
                    if os.path.exists(temp_filepath):
                        os.unlink(temp_filepath)
                        logger.debug(f"Cleaned up temporary file: {temp_filepath}")
            
            elif 'binary' in data:
                logger.debug("Processing binary image data")
                # Handle binary data
                binary_data = data['binary']
                filename = data.get('filename', 'uploaded_image.png')
                
                logger.debug(f"Processing binary image with filename: {filename}")
                
                filename = secure_filename(filename)
                if not _is_image_file(filename):
                    logger.warning(f"Upload failed: File is not an image: {filename}")
                    return {"success": False, "error": "File must be an image"}, 400
                
                # Convert binary data to bytes if it's a string
                if isinstance(binary_data, str):
                    logger.debug("Converting string binary data to bytes")
                    binary_data = binary_data.encode('latin1')
                
                logger.debug(f"Binary data size: {len(binary_data)} bytes")
                
                # Create a properly named temporary file
                temp_dir = tempfile.gettempdir()
                temp_filepath = os.path.join(temp_dir, filename)
                
                logger.debug(f"Saving binary image to temporary file: {temp_filepath}")
                with open(temp_filepath, 'wb') as temp_file:
                    temp_file.write(binary_data)

                # Get just the file name without the path as the destination
                destination_filename = os.path.basename(temp_filepath)
                logger.debug(f"Destination filename for CDN upload: {destination_filename}")
                
                try:
                    # Upload to CDN using the properly named temp file
                    logger.info(f"Uploading binary image {filename} to CDN from {temp_filepath}")
                    cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                    
                    logger.info(f"Successfully uploaded binary image to CDN: {cdn_url}")
                    return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
                finally:
                    # Clean up temp file
                    if os.path.exists(temp_filepath):
                        os.unlink(temp_filepath)
                        logger.debug(f"Cleaned up temporary file: {temp_filepath}")
            
            else:
                logger.warning("Upload failed: Missing 'base64' or 'binary' field in JSON data")
                return {"success": False, "error": "Missing 'base64' or 'binary' field in JSON data"}, 400
        
        # Check if raw binary data is sent
        elif request.content_type and request.content_type.startswith('image/'):
            logger.debug(f"Processing raw binary image data with content-type: {request.content_type}")
            # Handle raw binary image data
            filename = f"uploaded_image_{uuid.uuid4().hex}.png"
            
            image_data = request.get_data()
            logger.debug(f"Received raw image data, size: {len(image_data)} bytes")
            
            # Create a properly named temporary file
            temp_dir = tempfile.gettempdir()
            temp_filepath = os.path.join(temp_dir, filename)
            
            logger.debug(f"Saving raw image to temporary file: {temp_filepath}")
            with open(temp_filepath, 'wb') as temp_file:
                temp_file.write(image_data)

            # Get just the file name without the path as the destination
            destination_filename = os.path.basename(temp_filepath)
            logger.debug(f"Destination filename for CDN upload: {destination_filename}")
            
            try:
                # Upload to CDN using the properly named temp file
                logger.info(f"Uploading raw image {filename} to CDN from {temp_filepath}")
                cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                
                logger.info(f"Successfully uploaded raw image to CDN: {cdn_url}")
                return {
                    "success": True,
                    "url": cdn_url, 
                    "message": "Image uploaded successfully"
                    }
            finally:
                # Clean up temp file
                if os.path.exists(temp_filepath):
                    os.unlink(temp_filepath)
                    logger.debug(f"Cleaned up temporary file: {temp_filepath}")
        
        else:
            logger.warning(f"Upload failed: No valid image data found in request. Content-type: {request.content_type}")
            return {"success": False, "error": "No valid image data found in request"}, 400
            
    except Exception as e:
        logger.error(f"Unexpected error during image upload: {str(e)}", exc_info=True)
        return {"success": False, "error": "Failed to upload image"}, 500


def _is_image_file(filename):
    """Check if the filename has an image extension"""
    allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
    is_image = any(filename.lower().endswith(ext) for ext in allowed_extensions)
    logger.debug(f"File extension check for {filename}: {'valid' if is_image else 'invalid'} image file")
    return is_image

@limits(calls=50, period=ONE_MINUTE)
def save_onboarding_feedback(json_data):
    """
    Save or update onboarding feedback to Firestore.
    
    The function handles the new data format and implements logic to either:
    1. Create new feedback if no existing feedback is found
    2. Update existing feedback if found based on contact info or client info
    
    Expected json_data format:
    {
        "overallRating": int,
        "usefulTopics": [str],
        "missingTopics": str,
        "easeOfUnderstanding": str,
        "improvements": str,
        "additionalFeedback": str,
        "contactForFollowup": {
            "willing": bool,
            "name": str (optional),
            "email": str (optional)
        },
        "clientInfo": {
            "userAgent": str,
            "ipAddress": str
        }
    }
    """
    db = get_db()
    logger.info("Processing onboarding feedback submission")
    debug(logger, "Onboarding feedback data", data=json_data)
    
    try:
        # Extract contact information
        contact_info = json_data.get("contactForFollowup", {})
        client_info = json_data.get("clientInfo", {})
        
        # Check if user provided contact information
        has_contact_info = (
            contact_info.get("name") and 
            contact_info.get("email") and 
            contact_info.get("name").strip() and 
            contact_info.get("email").strip()
        )
        
        existing_feedback = None
        
        if has_contact_info:
            # Search for existing feedback by name and email
            logger.info("Searching for existing feedback by contact info")
            existing_docs = db.collection('onboarding_feedbacks').where(
                "contactForFollowup.name", "==", contact_info["name"]
            ).where(
                "contactForFollowup.email", "==", contact_info["email"]
            ).limit(1).stream()
            
            for doc in existing_docs:
                existing_feedback = doc
                logger.info(f"Found existing feedback by contact info: {doc.id}")
                break
        else:
            # Search for existing feedback by client info (anonymous user)
            logger.info("Searching for existing feedback by client info")
            existing_docs = db.collection('onboarding_feedbacks').where(
                "clientInfo.userAgent", "==", client_info.get("userAgent", "")
            ).where(
                "clientInfo.ipAddress", "==", client_info.get("ipAddress", "")
            ).limit(1).stream()
            
            for doc in existing_docs:
                existing_feedback = doc
                logger.info(f"Found existing feedback by client info: {doc.id}")
                break
        
        # Prepare the feedback data
        feedback_data = {
            "overallRating": json_data.get("overallRating"),
            "usefulTopics": json_data.get("usefulTopics", []),
            "missingTopics": json_data.get("missingTopics", ""),
            "easeOfUnderstanding": json_data.get("easeOfUnderstanding", ""),
            "improvements": json_data.get("improvements", ""),
            "additionalFeedback": json_data.get("additionalFeedback", ""),
            "contactForFollowup": contact_info,
            "clientInfo": client_info,
            "timestamp": datetime.now(pytz.utc)
        }
        
        if existing_feedback:
            # Update existing feedback
            logger.info(f"Updating existing feedback: {existing_feedback.id}")
            existing_feedback.reference.update(feedback_data)
            message = "Onboarding feedback updated successfully"
        else:
            # Create new feedback
            logger.info("Creating new onboarding feedback")
            db.collection('onboarding_feedbacks').add(feedback_data)
            message = "Onboarding feedback submitted successfully"
        
        return Message(message)
        
    except Exception as e:
        error(logger, f"Error saving onboarding feedback: {str(e)}", exc_info=True)
        return Message("Failed to save onboarding feedback", status="error")
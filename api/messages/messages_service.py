from common.utils import safe_get_env_var
from common.utils.slack import send_slack_audit, create_slack_channel, send_slack, async_send_slack, invite_user_to_channel, get_user_info
from common.utils.firebase import get_hackathon_by_event_id, upsert_news, upsert_praise, get_github_contributions_for_user,get_volunteer_from_db_by_event, get_volunteer_checked_in_from_db_by_event, get_user_by_user_id, get_recent_praises, get_praises_by_user_id
from common.utils.openai_api import generate_and_save_image_to_cdn
from common.utils.github import create_github_repo, get_all_repos, validate_github_username
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
import requests

from common.utils.validators import validate_email, validate_url, validate_hackathon_data
from common.exceptions import InvalidInputError

from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey

from ratelimit import limits
from datetime import datetime, timedelta
import os

from db.db import fetch_user_by_user_id, get_user_doc_reference
import resend
import random


logger = get_logger("messages_service")

resend_api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")
if not resend_api_key:
    logger.error("RESEND_WELCOME_EMAIL_KEY not set")    
else:
    resend.api_key = resend_api_key

google_recaptcha_key = safe_get_env_var("GOOGLE_CAPTCHA_SECRET_KEY")

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

def hash_key(docid, doc=None, depth=0):
    return hashkey(docid)

def log_execution_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logger.debug(f"{func.__name__} execution time: {execution_time:.4f} seconds")
        return result
    return wrapper

# Generically handle a DocumentSnapshot or a DocumentReference
@cached(cache=TTLCache(maxsize=2000, ttl=3600), key=hash_key)
def doc_to_json(docid=None, doc=None, depth=0):            
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
            #logger.debug(f"doc_to_json - key={key} value={value}")
            for i, v in enumerate(value):
                logger.debug(f"doc_to_json - i={i} v={v}")
                if isinstance(v, firestore.DocumentReference):
                    #logger.debug(f"doc_to_json - v is DocumentReference")
                    value[i] = v.id
                elif isinstance(v, firestore.DocumentSnapshot):
                    #logger.debug(f"doc_to_json - v is DocumentSnapshot")
                    value[i] = v.id
                else:
                    #logger.debug(f"doc_to_json - v is not DocumentReference or DocumentSnapshot")
                    value[i] = v
            d_json[key] = value
    
            
    
    d_json["id"] = docid
    return d_json




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


# Global variable to store singleton instance
_db_client = None

def get_db():
    """
    Returns a singleton instance of the Firestore client.
    This prevents creating too many connections.
    """
    global _db_client
    
    if _db_client is None:
        if safe_get_env_var("ENVIRONMENT") == "test":
            from mockfirestore import MockFirestore
            _db_client = MockFirestore()
        else:
            _db_client = firestore.client()
            
    return _db_client


def add_nonprofit_to_hackathon(json):
    hackathonId = json["hackathonId"]
    nonprofitId = json["nonprofitId"]

    logger.info(f"Add Nonprofit to Hackathon Start hackathonId={hackathonId} nonprofitId={nonprofitId}")

    db = get_db()
    # Get the hackathon document
    hackathon_doc = db.collection('hackathons').document(hackathonId)
    # Get the nonprofit document
    nonprofit_doc = db.collection('nonprofits').document(nonprofitId)
    # Check if the hackathon document exists
    hackathon_data = hackathon_doc.get()
    if not hackathon_data.exists:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Hackathon not found"
        }
    # Check if the nonprofit document exists
    nonprofit_data = nonprofit_doc.get()
    if not nonprofit_data.exists:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Nonprofit not found"
        }
    # Get the hackathon document data
    hackathon_dict = hackathon_data.to_dict()
    # Add the nonprofit document reference to the hackathon document
    if "nonprofits" not in hackathon_dict:
        hackathon_dict["nonprofits"] = []
    # Check if the nonprofit is already in the hackathon document
    if nonprofit_doc in hackathon_dict["nonprofits"]:
        logger.warning(f"Add Nonprofit to Hackathon End (no results)")
        return {
            "message": "Nonprofit already in hackathon"
        }
    # Add the nonprofit document reference to the hackathon document
    hackathon_dict["nonprofits"].append(nonprofit_doc)
    # Update the hackathon document
    hackathon_doc.set(hackathon_dict, merge=True)


    return {
        "message": "Nonprofit added to hackathon"
    }

def remove_nonprofit_from_hackathon(json):
    hackathonId = json["hackathonId"]
    nonprofitId = json["nonprofitId"]

    logger.info(f"Remove Nonprofit from Hackathon Start hackathonId={hackathonId} nonprofitId={nonprofitId}")

    db = get_db()
    # Get the hackathon document
    hackathon_doc = db.collection('hackathons').document(hackathonId)
    # Get the nonprofit document
    nonprofit_doc = db.collection('nonprofits').document(nonprofitId)
    
    # Check if the hackathon document exists
    if not hackathon_doc:
        logger.warning(f"Remove Nonprofit from Hackathon End (hackathon not found)")
        return {
            "message": "Hackathon not found"
        }
    
    # Get the hackathon document data
    hackathon_data = hackathon_doc.get().to_dict()
    
    # Check if nonprofits array exists
    if "nonprofits" not in hackathon_data or not hackathon_data["nonprofits"]:
        logger.warning(f"Remove Nonprofit from Hackathon End (no nonprofits in hackathon)")
        return {
            "message": "No nonprofits in hackathon"
        }
    
    # Check if the nonprofit is in the hackathon document
    nonprofit_found = False
    updated_nonprofits = []
    
    for np in hackathon_data["nonprofits"]:
        if np.id != nonprofitId:
            updated_nonprofits.append(np)
        else:
            nonprofit_found = True
    
    if not nonprofit_found:
        logger.warning(f"Remove Nonprofit from Hackathon End (nonprofit not found in hackathon)")
        return {
            "message": "Nonprofit not found in hackathon"
        }
    
    # Update the hackathon document with the filtered nonprofits list
    hackathon_data["nonprofits"] = updated_nonprofits
    hackathon_doc.set(hackathon_data, merge=True)
    
    logger.info(f"Remove Nonprofit from Hackathon End (nonprofit removed)")
    return {
        "message": "Nonprofit removed from hackathon"
    }


@cached(cache=TTLCache(maxsize=100, ttl=20))
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

@cached(cache=TTLCache(maxsize=100, ttl=10))
@limits(calls=2000, period=ONE_MINUTE)
def get_volunteer_by_event(event_id, volunteer_type, admin=False):
    logger.debug(f"get {volunteer_type} start event_id={event_id}")   

    if event_id is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []
     
    results = get_volunteer_from_db_by_event(event_id, volunteer_type, admin=admin)

    if results is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []
    else:                
        logger.debug(f"get {volunteer_type} end (with result):{results}")        
        return results

@cached(cache=TTLCache(maxsize=100, ttl=5))
def get_volunteer_checked_in_by_event(event_id, volunteer_type):
    logger.debug(f"get {volunteer_type} start event_id={event_id}")   

    if event_id is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []

    results = get_volunteer_checked_in_from_db_by_event(event_id, volunteer_type)

    if results is None:
        logger.warning(f"get {volunteer_type} end (no results)")
        return []
    else:
        logger.debug(f"get {volunteer_type} end (with result):{results}")
        return results

@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=2000, period=ONE_MINUTE)
def get_single_hackathon_event(hackathon_id):
    logger.debug(f"get_single_hackathon_event start hackathon_id={hackathon_id}")    
    result = get_hackathon_by_event_id(hackathon_id)
    
    if result is None:
        logger.warning("get_single_hackathon_event end (no results)")
        return {}
    else:                  
        if "nonprofits" in result and result["nonprofits"]:           
            result["nonprofits"] = [doc_to_json(doc=npo, docid=npo.id) for npo in result["nonprofits"]]   
        else:
            result["nonprofits"] = []
        if "teams" in result and result["teams"]:
            result["teams"] = [doc_to_json(doc=team, docid=team.id) for team in result["teams"]]        
        else:
            result["teams"] = []

        logger.info(f"get_single_hackathon_event end (with result):{result}")
        return result
    return {}

# 12 hour cache for 100 objects LRU
@limits(calls=1000, period=ONE_MINUTE)
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


@cached(cache=TTLCache(maxsize=100, ttl=3600), key=lambda is_current_only: str(is_current_only))
@limits(calls=200, period=ONE_MINUTE)
@log_execution_time
def get_hackathon_list(is_current_only=None):
    """
    Retrieve a list of hackathons based on specified criteria.
    
    Args:
        is_current_only: Filter type - 'current', 'previous', or None for all hackathons
        
    Returns:
        Dictionary containing list of hackathons with document references resolved
    """
    logger.debug(f"Hackathon List - Getting {is_current_only or 'all'} hackathons")
    db = get_db()
    
    # Prepare query based on filter type
    query = db.collection('hackathons')
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if is_current_only == "current":
        logger.debug(f"Querying current events (end_date >= {today_str})")
        query = query.where(filter=firestore.FieldFilter("end_date", ">=", today_str)).order_by("end_date", direction=firestore.Query.ASCENDING)

    elif is_current_only == "previous":
        # Look back 3 years for previous events
        target_date = datetime.now() + timedelta(days=-3*365)
        target_date_str = target_date.strftime("%Y-%m-%d")
        logger.debug(f"Querying previous events ({target_date_str} <= end_date <= {today_str})")
        query = query.where("end_date", ">=", target_date_str).where("end_date", "<=", today_str)
        query = query.order_by("end_date", direction=firestore.Query.DESCENDING).limit(50)
    
    else:
        query = query.order_by("start_date")
    
    # Execute query
    try:
        logger.debug(f"Executing query: {query}")
        docs = query.stream()
        results = _process_hackathon_docs(docs)
        logger.debug(f"Retrieved {len(results)} hackathon results")
        return {"hackathons": results}
    except Exception as e:
        logger.error(f"Error retrieving hackathons: {str(e)}")
        return {"hackathons": [], "error": str(e)}


def _process_hackathon_docs(docs):
    """
    Process hackathon documents and resolve references.
    
    This helper function processes document references and nested objects
    more efficiently without excessive logging.
    
    Args:
        docs: Firestore document stream
        
    Returns:
        List of processed hackathon documents with references resolved
    """
    if not docs:
        return []
    
    results = []
    for doc in docs:
        try:
            d = doc_to_json(doc.id, doc)
            
            # Process lists of references more efficiently
            for key, value in d.items():
                if isinstance(value, list):
                    d[key] = [doc_to_json_recursive(item) for item in value]
                elif isinstance(value, (DocumentReference, DocumentSnapshot)):
                    d[key] = doc_to_json_recursive(value)
            
            results.append(d)
        except Exception as e:
            logger.error(f"Error processing hackathon doc {doc.id}: {str(e)}")
            # Continue processing other docs instead of failing completely
            
    return results


@limits(calls=2000, period=THIRTY_SECONDS)
def get_teams_list(id=None):
    logger.debug(f"Teams List Start team_id={id}")
    db = get_db() 
    if id is not None:
        logger.debug(f"Teams List team_id={id} | Start")
        # Get by id
        doc = db.collection('teams').document(id).get()
        if doc is None:
            return {}
        else:
            #log
            logger.info(f"Teams List team_id={id} | End (with result):{doc_to_json(docid=doc.id, doc=doc)}")
            logger.debug(f"Teams List team_id={id} | End")
            return doc_to_json(docid=doc.id, doc=doc)
    else:
        # Get all        
        logger.debug("Teams List | Start")
        docs = db.collection('teams').stream() # steam() gets all records   
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
        # Query teams by hackathon_event_id field
        docs = db.collection('teams').where('hackathon_event_id', '==', event_id).stream()
        
        results = []
        for doc in docs:
            team_data = doc_to_json(docid=doc.id, doc=doc)
            
            # Format team data for admin judging assignment interface
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
    # Handle json["team_ids"] will have a list of teamids

    if "team_ids" not in json:
        logger.error("get_teams_batch called without team_ids in json")
        # TODO: Return for a batch of team ids to speed up the frontend
        return []
    team_ids = json["team_ids"]
    logger.debug(f"get_teams_batch start team_ids={team_ids}")
    db = get_db()
    if not team_ids:
        logger.warning("get_teams_batch end (no team_ids provided)")
        return []
    # Get all teams by team_ids, using correct Firestore Python syntax and where FieldPath doesn't exist also make sure the id works for id fields
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
        # print stack trace
        import traceback
        traceback.print_exc()
        return []



@limits(calls=40, period=ONE_MINUTE)
def get_npos_by_hackathon_id(id):
    logger.debug(f"get_npos_by_hackathon_id start id={id}")    
    db = get_db()      
    doc = db.collection('hackathons').document(id)    
    
    try:
        doc_dict = doc.get().to_dict()
        if doc_dict is None:
            logger.warning("get_npos_by_hackathon_id end (no results)")
            return {
                "nonprofits": []
            }
        
        # Get all nonprofits from hackathon
        npos = []
        if "nonprofits" in doc_dict and doc_dict["nonprofits"]:
            npo_refs = doc_dict["nonprofits"]
            logger.info(f"get_npos_by_hackathon_id found {len(npo_refs)} nonprofit references")
            
            # Get all nonprofits            
            for npo_ref in npo_refs:
                try:
                    # Convert DocumentReference to dict
                    npo_doc = npo_ref.get()
                    if npo_doc.exists:
                        npo = doc_to_json(docid=npo_doc.id, doc=npo_doc)
                        npos.append(npo)
                except Exception as e:
                    logger.error(f"Error processing nonprofit reference: {e}")
                    continue
                                        
        return {
            "nonprofits": npos
        }
    except Exception as e:
        logger.error(f"Error in get_npos_by_hackathon_id: {e}")
        return {
            "nonprofits": []
        }


@limits(calls=40, period=ONE_MINUTE)
def get_npo_by_hackathon_id(id):
    logger.debug(f"get_npo_by_hackathon_id start id={id}")    
    db = get_db()      
    doc = db.collection('hackathons').document(id)    
    
    if doc is None:
        logger.warning("get_npo_by_hackathon_id end (no results)")
        return {}
    else:                        
        result = doc_to_json(docid=doc.id, doc=doc)
        
        logger.info(f"get_npo_by_hackathon_id end (with result):{result}")
        return result
    return {}    


@limits(calls=20, period=ONE_MINUTE)
def get_npo_list(word_length=30):
    logger.debug("NPO List Start")
    db = get_db()  
    # steam() gets all records
    docs = db.collection('nonprofits').order_by( "rank" ).stream()
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

def save_team(propel_user_id, json):    
    send_slack_audit(action="save_team", message="Saving", payload=json)    

    email, user_id, last_login, profile_image, name, nickname = get_propel_user_details_by_id(propel_user_id)
    slack_user_id = user_id
    
    root_slack_user_id = slack_user_id.replace("oauth2|slack|T1Q7936BH-","")
    user = get_user_doc_reference(root_slack_user_id)
    
    db = get_db()  # this connects to our Firestore database
    logger.debug("Team Save")    

    logger.debug(json)
    doc_id = uuid.uuid1().hex # Generate a new team id

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
    
    
    #TODO: This is a hack, but get the nonprofit if provided, then get the first problem statement
    nonprofit = None
    nonprofit_name = ""    
    if nonprofit_id is not None:
        logger.info(f"Nonprofit ID provided {nonprofit_id}")
        nonprofit = get_single_npo(nonprofit_id)["nonprofits"]        
        nonprofit_name = nonprofit["name"]
        logger.info(f"Nonprofit {nonprofit}")
        # See if the nonprofit has a least 1 problem statement
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
    
    # Remove all spaces from problem_statement_title
    problem_statement_title = raw_problem_statement_title.replace(" ", "").replace("-", "")
    logger.info(f"Problem Statement Title: {problem_statement_title}")

    nonprofit_title = nonprofit_name.replace(" ", "").replace("-", "")
    # Truncate nonprofit name to first 20 chars to support github limits
    nonprofit_title = nonprofit_title[:20]
    logger.info(f"Nonprofit Title: {nonprofit_title}")

    repository_name = f"{team_name}-{nonprofit_title}-{problem_statement_title}"
    logger.info(f"Repository Name: {repository_name}")
    
    # truncate repostory name to first 100 chars to support github limits
    repository_name = repository_name[:100]
    logger.info(f"Truncated Repository Name: {repository_name}")  

    slack_name_of_creator = name

    nonprofit_url = f"https://ohack.dev/nonprofit/{nonprofit_id}"
    project_url = f"https://ohack.dev/project/{problem_statement_id}"
    # Create github repo
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
    
    # Add all Slack admins too  
    slack_admins = ["UC31XTRT5", "UCQKX6LPR", "U035023T81Z", "UC31XTRT5", "UC2JW3T3K", "UPD90QV17", "U05PYC0LMHR"]
    for admin in slack_admins:
        logger.info(f"Inviting admin {admin} to slack channel {slack_channel}")
        invite_user_to_channel(admin, slack_channel)

    # Send a slack message to the team channel
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

    # Clear the cache
    logger.info(f"Clearing cache for event_id={hackathon_db_id} problem_statement_id={problem_statement_id} user_doc.id={user_doc.id} doc_id={doc_id}")
    clear_cache()

    # get the team from get_teams_list
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
    # Get hackathon by event_id
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

    # Get user ID once
    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    userid = get_user_from_slack_id(slack_user["sub"]).id

    # Reference the team document
    team_ref = db.collection('teams').document(team_id)
    user_ref = db.collection('users').document(userid)

    @firestore.transactional
    def update_team_and_user(transaction):
        # Read operations
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

        # Check if user is already in team
        if user_ref in team_users:
            logger.warning(f"User {userid} is already in team {team_id}")
            return False

        # Prepare updates
        new_team_users = list(set(team_users + [user_ref]))
        new_user_teams = list(set(user_teams + [team_ref]))

        # Write operations
        transaction.update(team_ref, {"users": new_team_users})
        transaction.update(user_ref, {"teams": new_user_teams})

        logger.debug(f"User {userid} added to team {team_id}")
        return True

    # Execute the transaction
    try:
        transaction = db.transaction()
        success = update_team_and_user(transaction)
        if success:
            send_slack_audit(action="join_team", message="Added", payload=json)
            message = "Joined Team"
            # Add person to Slack channel
            team_slack_channel = team_ref.get().to_dict()["slack_channel"]
            invite_user_to_channel(userid, team_slack_channel)
        else:
            message = "User was already in the team"
    except Exception as e:
        logger.error(f"Error in join_team: {str(e)}")
        return Message(f"Error: {str(e)}")

    # Clear caches
    clear_cache()

    logger.debug("Join Team End")
    return Message(message)


def unjoin_team(propel_user_id, json):
    logger.info(f"Unjoin for UserId: {propel_user_id} Json: {json}")
    team_id = json["teamId"]

    db = get_db()

    # Get user ID once
    slack_user = get_slack_user_from_propel_user_id(propel_user_id)
    userid = get_user_from_slack_id(slack_user["sub"]).id

    # Reference the team document
    team_ref = db.collection('teams').document(team_id)
    user_ref = db.collection('users').document(userid)

    @firestore.transactional
    def update_team_and_user(transaction):
        # Read operations
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

        # Check if user is in team
        if user_ref not in user_list:
            logger.warning(f"User {userid} not found in team {team_id}")
            return False

        # Prepare updates
        new_user_list = [u for u in user_list if u.id != userid]
        new_user_teams = [t for t in user_teams if t.id != team_id]

        # Write operations
        transaction.update(team_ref, {"users": new_user_list})
        transaction.update(user_ref, {"teams": new_user_teams})

        logger.debug(f"User {userid} removed from team {team_id}")
        return True

    # Execute the transaction
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

    # Clear caches
    clear_cache()

    logger.debug("Unjoin Team End")
    return Message(message)

@limits(calls=100, period=ONE_MINUTE)
def update_npo_application( application_id, json, propel_id):
    send_slack_audit(action="update_npo_application", message="Updating", payload=json)
    db = get_db()
    logger.info("NPO Application Update")
    doc = db.collection('project_applications').document(application_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_npo_application",
                         message="Updating", payload=doc_dict)
        doc.update(json)
    
    # Clear cache for get_npo_applications
    logger.info(f"Clearing cache for application_id={application_id}")    

    clear_cache()

    return Message(
        "Updated NPO Application"
    )


@limits(calls=100, period=ONE_MINUTE)
def get_npo_applications():
    logger.info("get_npo_applications Start")
    db = get_db()    
    
    # Use a transaction to ensure consistency
    @firestore.transactional
    def get_latest_docs(transaction):
        docs = db.collection('project_applications').get(transaction=transaction)
        return [doc_to_json(docid=doc.id, doc=doc) for doc in docs]

    
    # Use a transaction to get the latest data
    transaction = db.transaction()
    results = get_latest_docs(transaction)

    if not results:
        return {"applications": []}
    
    logger.info(results)
    logger.info("get_npo_applications End")
    
    return {"applications": results}


@limits(calls=100, period=ONE_MINUTE)
def save_npo_application(json):
    send_slack_audit(action="save_npo_application", message="Saving", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.debug("NPO Application Save")

    # Check Google Captcha
    token = json["token"]
    recaptcha_response = requests.post(
        f"https://www.google.com/recaptcha/api/siteverify?secret={google_recaptcha_key}&response={token}")
    recaptcha_response_json = recaptcha_response.json()
    logger.info(f"Recaptcha Response: {recaptcha_response_json}")

    if recaptcha_response_json["success"] == False:
        return Message(
            "Recaptcha failed"
        )


    '''
    Save this data into the Firestore database in the project_applications collection
    name: '',
    email: '',
    organization: '',
    idea: '',
    isNonProfit: false,    
    '''
    doc_id = uuid.uuid1().hex

    name = json["name"]
    email = json["email"]
    organization = json["organization"]
    idea = json["idea"]
    isNonProfit = json["isNonProfit"]

    collection = db.collection('project_applications')
    
    insert_res = collection.document(doc_id).set({
        "name": name,
        "email": email,
        "organization": organization,
        "idea": idea,
        "isNonProfit": isNonProfit,
        "timestamp": datetime.now().isoformat()        
    })

    logger.info(f"Insert Result: {insert_res}")

    logger.info(f"Sending welcome email to {name} {email}")

    send_nonprofit_welcome_email(organization, name, email)

    logger.info(f"Sending slack message to nonprofit-form-submissions")

    # Send a Slack message to nonprofit-form-submissions with all content
    slack_message = f'''
:rocket: New NPO Application :rocket:
Name: `{name}`
Email: `{email}`
Organization: `{organization}`
Idea: `{idea}`
Is Nonprofit: `{isNonProfit}`
'''
    send_slack(channel="nonprofit-form-submissions", message=slack_message, icon_emoji=":rocket:")

    logger.info(f"Sent slack message to nonprofit-form-submissions")

    return Message(
        "Saved NPO Application"
    )

@limits(calls=100, period=ONE_MINUTE)
def save_npo(json):
    send_slack_audit(action="save_npo", message="Saving", payload=json)
    db = get_db()  # this connects to our Firestore database
    logger.info("NPO Save - Starting")

    try:
        # Input validation and sanitization
        required_fields = ['name', 'description', 'website', 'slack_channel']
        for field in required_fields:
            if field not in json or not json[field].strip():
                raise InvalidInputError(f"Missing or empty required field: {field}")

        name = json['name'].strip()
        description = json['description'].strip()
        website = json['website'].strip()
        slack_channel = json['slack_channel'].strip()
        contact_people = json.get('contact_people', [])
        contact_email = json.get('contact_email', [])
        problem_statements = json.get('problem_statements', [])
        image = json.get('image', '').strip()
        rank = int(json.get('rank', 0))

        # Validate email addresses
        contact_email = [email.strip() for email in contact_email if validate_email(email.strip())]

        # Validate URL
        if not validate_url(website):
            raise InvalidInputError("Invalid website URL")

        # Convert problem_statements from IDs to DocumentReferences
        problem_statement_refs = [
            db.collection("problem_statements").document(ps)
            for ps in problem_statements
            if ps.strip()
        ]

        # Prepare data for Firestore
        npo_data = {
            "name": name,
            "description": description,
            "website": website,
            "slack_channel": slack_channel,
            "contact_people": contact_people,
            "contact_email": contact_email,
            "problem_statements": problem_statement_refs,
            "image": image,
            "rank": rank,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP
        }

        # Use a transaction to ensure data consistency
        @firestore.transactional
        def save_npo_transaction(transaction):
            # Check if NPO with the same name already exists
            existing_npo = db.collection('nonprofits').where("name", "==", name).limit(1).get()
            if len(existing_npo) > 0:
                raise InvalidInputError(f"Nonprofit with name '{name}' already exists")

            # Generate a new document ID
            new_doc_ref = db.collection('nonprofits').document()
            
            # Set the data in the transaction
            transaction.set(new_doc_ref, npo_data)
            
            return new_doc_ref

        # Execute the transaction
        transaction = db.transaction()
        new_npo_ref = save_npo_transaction(transaction)

        logger.info(f"NPO Save - Successfully saved nonprofit: {new_npo_ref.id}")
        send_slack_audit(action="save_npo", message="Saved successfully", payload={"id": new_npo_ref.id})

        # Clear cache
        clear_cache()

        return Message(f"Saved NPO with ID: {new_npo_ref.id}")

    except InvalidInputError as e:
        logger.error(f"NPO Save - Invalid input: {str(e)}")
        return Message(f"Failed to save NPO: {str(e)}", status="error")
    except Exception as e:
        logger.exception("NPO Save - Unexpected error occurred")
        return Message("An unexpected error occurred while saving the NPO", status="error")

def clear_cache():
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
    

@limits(calls=20, period=ONE_MINUTE)
def update_npo(json):
    db = get_db()  # this connects to our Firestore database

    logger.debug("NPO Edit")
    send_slack_audit(action="update_npo", message="Updating", payload=json)
    
    doc_id = json["id"]
    temp_problem_statements = json["problem_statements"]

    doc = db.collection('nonprofits').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_npo", message="Updating", payload=doc_dict)
        
        # Extract all fields from the json
        name = json.get("name", None)
        contact_email = json.get("contact_email", None)
        contact_people = json.get("contact_people", None)
        slack_channel = json.get("slack_channel", None)
        website = json.get("website", None)
        description = json.get("description", None)
        image = json.get("image", None)
        rank = json.get("rank", None)

        # Convert contact_email and contact_people to lists if they're not already
        if isinstance(contact_email, str):
            contact_email = [email.strip() for email in contact_email.split(',')]
        if isinstance(contact_people, str):
            contact_people = [person.strip() for person in contact_people.split(',')]

        # We need to convert this from just an ID to a full object
        # Ref: https://stackoverflow.com/a/59394211
        problem_statements = []
        for ps in temp_problem_statements:
            problem_statements.append(db.collection("problem_statements").document(ps))

        update_data = {
            "contact_email": contact_email,
            "contact_people": contact_people,
            "name": name,
            "slack_channel": slack_channel,
            "website": website,
            "description": description,
            "problem_statements": problem_statements,
            "image": image,
            "rank": rank
        }

        # Remove any fields that are None to avoid overwriting with null values
        update_data = {k: v for k, v in update_data.items() if v is not None}
        logger.debug(f"Update data: {update_data}")

        doc.update(update_data)

        logger.debug("NPO Edit - Update successful")
        send_slack_audit(action="update_npo", message="Update successful", payload=update_data)

        # Clear cache
        clear_cache()

        return Message("Updated NPO")
    else:
        logger.error(f"NPO Edit - Document with id {doc_id} not found")
        return Message("NPO not found", status="error")

@limits(calls=100, period=ONE_MINUTE)
def single_add_volunteer(event_id, json, volunteer_type, propel_id):
    db = get_db()
    logger.info("Single Add Volunteer")
    logger.info("JSON: " + str(json))
    send_slack_audit(action="single_add_volunteer", message="Adding", payload=json)

    # Since we know this user is an admin, prefix all vars with admin_
    admin_email, admin_user_id, admin_last_login, admin_profile_image, admin_name, admin_nickname = get_propel_user_details_by_id(propel_id)
    
    if volunteer_type not in ["mentor", "volunteer", "judge"]:
        return Message (
            "Error: Must be volunteer, mentor, judge"
        )

    
    json["volunteer_type"] = volunteer_type
    json["event_id"] = event_id
    name = json["name"]
        
      
    # Add created_by and created_timestamp
    json["created_by"] = admin_name
    json["created_timestamp"] = datetime.now().isoformat()

    fields_that_should_always_be_present = ["name", "timestamp"]

    logger.info(f"Checking to see if person {name} is already in DB for event: {event_id}")
    # We don't want to add the same name for the same event_id, so check that first
    doc = db.collection('volunteers').where("event_id", "==", event_id).where("name", "==", name).stream()
    # If we don't have a duplicate, then return
    if len(list(doc)) > 0:
        logger.warning("Volunteer already exists")
        return Message("Volunteer already exists")
    
    logger.info(f"Checking to see if the event_id '{event_id}' provided exists")
    # Query for event_id column in hackathons to ensure it exists
    doc = db.collection('hackathons').where("event_id", "==", event_id).stream()
    # If we don't find the event, return
    if len(list(doc)) == 0:
        logger.warning("No hackathon found")
        return Message("No Hackathon Found")
    
    logger.info(f"Looks good! Adding to volunteers collection JSON: {json}")
    # Add the volunteer
    doc = db.collection('volunteers').add(json)
    
    get_volunteer_by_event.cache_clear()

    return Message(
        "Added Hackathon Volunteer"
    )


@limits(calls=50, period=ONE_MINUTE)
def update_hackathon_volunteers(event_id, volunteer_type, json, propel_id):
    db = get_db()
    logger.info(f"update_hackathon_volunteers for event_id={event_id} propel_id={propel_id}")
    logger.info("JSON: " + str(json))
    send_slack_audit(action="update_hackathon_volunteers", message="Updating", payload=json)

    if "id" not in json:
        logger.error("Missing id field")
        return Message("Missing id field")

    volunteer_id = json["id"]

    # Since we know this user is an admin, prefix all vars with admin_
    admin_email, admin_user_id, admin_last_login, admin_profile_image, admin_name, admin_nickname = get_propel_user_details_by_id(propel_id)

    # Query for event_id column    
    doc_ref = db.collection("volunteers").document(volunteer_id)
    doc = doc_ref.get()
    doc_dict = doc.to_dict()
    doc_volunteer_type = doc_dict.get("volunteer_type", "participant").lower()

    # If we don't find the event, return
    if doc_ref is None:
        return Message("No volunteer for Hackathon Found")
                        
    
    # Update doc with timestamp and admin_name
    json["updated_by"] = admin_name
    json["updated_timestamp"] = datetime.now().isoformat()

    # Update the volunteer record with the new data
    doc_ref.update(json)

    slack_user_id = doc.get('slack_user_id')

    hackathon_welcome_message = f"🎉 Welcome <@{slack_user_id}> [{doc_volunteer_type}]."

    # Base welcome direct message
    base_message = f"🎉 Welcome to Opportunity Hack {event_id}! You're checked in as a {doc_volunteer_type}.\n\n"
    
    # Role-specific guidance
    if doc_volunteer_type == 'mentor':
        role_guidance = """🧠 As a Mentor:
• Help teams with technical challenges and project direction
• Share your expertise either by staying with a specific team, looking at GitHub to find a team that matches your skills, or asking for who might need a mentor in #ask-a-mentor
• Guide teams through problem-solving without doing the work for them
• Connect with teams in their Slack channels or in-person"""
    
    elif doc_volunteer_type == 'judge':
        role_guidance = """⚖️ As a Judge:
• Review team presentations and evaluate projects
• Focus on impact, technical implementation, and feasibility
• Provide constructive feedback during judging sessions
• Join the judges' briefing session for scoring criteria"""
    
    elif doc_volunteer_type == 'volunteer':
        role_guidance = """🙋 As a Volunteer:
• Help with event logistics and participant support
• Assist with check-in, meals, and general questions
• Support the organizing team throughout the event
• Be a friendly face for participants who need help"""
    
    else:  # hacker/participant
        role_guidance = """💻 As a Hacker:
• Form or join a team to work on nonprofit challenges
• Collaborate with your team to build meaningful tech solutions
• Attend mentor sessions and utilize available resources
• Prepare for final presentations and judging"""
    
    slack_message_content = f"""{base_message}{role_guidance}

📅 Important Links:
• Full schedule: https://www.ohack.dev/hack/{event_id}#countdown
• Slack channels: Watch #general for updates
• Need help? Ask in #help or find an organizer

🚀 Ready to code for good? Let's build technology that makes a real difference for nonprofits and people in the world!
"""

    # If the incoming json has a value checkedIn that is true, and the doc from the db either doesn't have this field or it's false, we should send a Slack message to welcome this person to the hackathon giving them details about the event and their role.
    # This should both be a DM to the person using their Slack ID and a message in the #hackathon-welcome channel.
    if json.get("checkedIn") is True and "checkedIn" not in doc_dict.keys() and doc_dict.get("checkedIn") != True:
        logger.info(f"Volunteer {volunteer_id} just checked in, sending welcome message to {slack_user_id}")
        invite_user_to_channel(slack_user_id, "hackathon-welcome")
        
        logger.info(f"Sending Slack message to volunteer {volunteer_id} in channel #{slack_user_id} and #hackathon-welcome")
        async_send_slack(
            channel="#hackathon-welcome",
            message=hackathon_welcome_message
        )

        logger.info(f"Sending Slack DM to volunteer {volunteer_id} in channel #{slack_user_id}")
        async_send_slack(
            channel=slack_user_id,
            message=slack_message_content
        )
    else:
        logger.info(f"Volunteer {volunteer_id} checked in again, no welcome message sent.")

    # Clear cache for get_volunteer_by_event
    get_volunteer_by_event.cache_clear()

    return Message(
        "Updated Hackathon Volunteers"
    )

def send_hackathon_request_email(contact_name, contact_email, request_id):    
    """
    Send a specialized confirmation email to someone who has submitted a hackathon request.
    
    Args:
        contact_name: Name of the requestor
        contact_email: Email address of the requestor
        request_id: The unique ID of the hackathon request for edit link
        
    Returns:
        True if email was sent successfully, False otherwise
    """    

    # Rotate between images for better engagement
    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"hackathon_request_image_{image_number}"
    
    # Build the edit link for the hackathon request
    base_url = os.getenv("FRONTEND_URL", "https://www.ohack.dev")
    edit_link = f"{base_url}/hack/request/{request_id}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Thank You for Your Hackathon Request</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=image_utm_content)}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">
        
        <h1 style="color: #0088FE;">Thank You for Your Hackathon Request!</h1>
        
        <p>Dear {contact_name},</p>
        
        <p>We're thrilled you're interested in hosting an Opportunity Hack event! Your request has been received and our team is reviewing it now.</p>
        
        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h2 style="color: #0088FE; margin-top: 0;">Next Steps:</h2>
            <ol style="margin-bottom: 0;">
                <li>A member of our team will reach out within 3-5 business days</li>
                <li>We'll schedule a call to discuss your goals and requirements</li>
                <li>Together, we'll create a customized hackathon plan for your community</li>
            </ol>
        </div>
        
        <p><strong>Need to make changes to your request?</strong><br>
        You can <a href="{add_utm(edit_link, medium='email', campaign='hackathon_request', content=image_utm_content)}" style="color: #0088FE; font-weight: bold;">edit your request here</a> at any time.</p>
        
        <h2 style="color: #0088FE;">Why Host an Opportunity Hack?</h2>
        <ul>
            <li>Connect local nonprofits with skilled tech volunteers</li>
            <li>Build lasting technology solutions for social good</li>
            <li>Create meaningful community engagement opportunities</li>
            <li>Develop technical skills while making a difference</li>
        </ul>
        
        <p>Have questions in the meantime? Feel free to reply to this email or reach out through our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack community</a>.</p>
        
        <p>Together, we can create positive change through technology!</p>
        
        <p>Warm regards,<br>The Opportunity Hack Team</p>
        
        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """

    # If name is none, or an empty string, or an unassigned string, or a unprintable character like a space string set it to "Event Organizer"
    if contact_name is None or contact_name == "" or contact_name == "Unassigned" or contact_name.isspace():
        contact_name = "Event Organizer"

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{contact_name} <{contact_email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": "Your Opportunity Hack Event Request - Next Steps",
        "html": html_content,
    }

    try:
        email = resend.Emails.SendParams(params)
        resend.Emails.send(email)
        logger.info(f"Sent hackathon request confirmation email to {contact_email}")
        return True
    except Exception as e:
        logger.error(f"Error sending hackathon request email via Resend: {str(e)}")
        return False

def create_hackathon(json):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Hackathon Create")
    send_slack_audit(action="create_hackathon", message="Creating", payload=json)

    # Save payload for potential hackathon in the database under the hackathon_requests collection
    doc_id = uuid.uuid1().hex
    collection = db.collection('hackathon_requests')
    json["created"] = datetime.now().isoformat()
    json["status"] = "pending"    
    insert_res = collection.document(doc_id).set(json)
    json["id"] = doc_id

    if "contactEmail" in json and "contactName" in json:
        # Send the specialized hackathon request email instead of the general welcome email
        send_hackathon_request_email(json["contactName"], json["contactEmail"], doc_id)

    send_slack(
        message=":rocket: New Hackathon Request :rocket: with json: " + str(json), channel="log-hackathon-requests", icon_emoji=":rocket:")        
    logger.debug(f"Insert Result: {insert_res}")

    return {
        "message": "Hackathon Request Created",
        "success": True,
        "id": doc_id
    }

def get_hackathon_request_by_id(doc_id):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Hackathon Request Get")
    doc = db.collection('hackathon_requests').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="get_hackathon_request_by_id", message="Getting", payload=doc_dict)
        return doc_dict
    else:
        return None


def update_hackathon_request(doc_id, json):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Hackathon Request Update")
    doc = db.collection('hackathon_requests').document(doc_id)
    if doc:
        doc_dict = doc.get().to_dict()
        send_slack_audit(action="update_hackathon_request", message="Updating", payload=doc_dict)
        # Send email for the update too
        send_hackathon_request_email(json["contactName"], json["contactEmail"], doc_id)
        # Update the date
        doc_dict["updated"] = datetime.now().isoformat()

        doc.update(json)
        return doc_dict
    else:
        return None



@limits(calls=50, period=ONE_MINUTE)
def save_hackathon(json_data, propel_id):
    db = get_db()
    logger.info("Hackathon Save/Update initiated")
    logger.debug(json_data)
    send_slack_audit(action="save_hackathon", message="Saving/Updating", payload=json_data)

    try:
        # Validate input data
        validate_hackathon_data(json_data)

        # Check if this is an update or a new hackathon
        doc_id = json_data.get("id") or uuid.uuid1().hex
        is_update = "id" in json_data

        # Prepare data for Firestore
        hackathon_data = {
            "title": json_data["title"],
            "description": json_data["description"],
            "location": json_data["location"],
            "start_date": json_data["start_date"],
            "end_date": json_data["end_date"],
            "type": json_data["type"],
            "image_url": json_data["image_url"],
            "event_id": json_data["event_id"],
            "links": json_data.get("links", []),
            "countdowns": json_data.get("countdowns", []),
            "constraints": json_data.get("constraints", {
                "max_people_per_team": 5,
                "max_teams_per_problem": 10,
                "min_people_per_team": 2,
            }),
            "donation_current": json_data.get("donation_current", {
                "food": "0",
                "prize": "0",
                "swag": "0",
                "thank_you": "",
            }),
            "donation_goals": json_data.get("donation_goals", {
                "food": "0",
                "prize": "0",
                "swag": "0",
            }),
            "last_updated": firestore.SERVER_TIMESTAMP,
            "last_updated_by": propel_id,
        }

        # Handle nonprofits and teams
        if "nonprofits" in json_data:
            hackathon_data["nonprofits"] = [db.collection("nonprofits").document(npo) for npo in json_data["nonprofits"]]
        if "teams" in json_data:
            hackathon_data["teams"] = [db.collection("teams").document(team) for team in json_data["teams"]]

        # Use a transaction for atomic updates
        @firestore.transactional
        def update_hackathon(transaction):
            hackathon_ref = db.collection('hackathons').document(doc_id)
            if is_update:
                # For updates, we need to merge with existing data
                transaction.set(hackathon_ref, hackathon_data, merge=True)
            else:
                # For new hackathons, we can just set the data
                hackathon_data["created_at"] = firestore.SERVER_TIMESTAMP
                hackathon_data["created_by"] = propel_id
                transaction.set(hackathon_ref, hackathon_data)

        # Run the transaction
        transaction = db.transaction()
        update_hackathon(transaction)

        # Clear cache for get_single_hackathon_event
        get_single_hackathon_event.cache_clear()

        # Clear cache for get_hackathon_list
        doc_to_json.cache_clear()


        logger.info(f"Hackathon {'updated' if is_update else 'created'} successfully. ID: {doc_id}")
        return Message(
        "Saved Hackathon"
    )

        return {
            "message": f"Hackathon {'updated' if is_update else 'saved'} successfully",
            "id": doc_id
        }

    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        return {"error": str(ve)}, 400
    except Exception as e:
        logger.error(f"Error saving/updating hackathon: {str(e)}")
        return {"error": "An unexpected error occurred"}, 500
    
    


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
    # Make sure these fields exist praise_receiver, praise_sender, praise_channel, praise_message
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

async def save_lead(json):
    token = json["token"]

    # If any field is missing, return False
    if "name" not in json or "email" not in json:
        # Log which fields are missing
        logger.error(f"Missing field name or email {json}")        
        return False
    
    # If name or email length is not long enough, return False
    if len(json["name"]) < 2 or len(json["email"]) < 3:
        # Log
        logger.error(f"Name or email too short name:{json['name']} email:{json['email']}")
        return False
    
    recaptcha_response = requests.post(
        f"https://www.google.com/recaptcha/api/siteverify?secret={google_recaptcha_key}&response={token}")
    recaptcha_response_json = recaptcha_response.json()
    logger.info(f"Recaptcha Response: {recaptcha_response_json}")    

    if recaptcha_response_json["success"] == False:
        return False
    else:
        logger.info("Recaptcha Success, saving...")
        # Save lead to Firestore
        db = get_db()
        collection = db.collection('leads')
        # Remove token from json
        del json["token"]

        # Add timestamp
        json["timestamp"] = datetime.now().isoformat()
        insert_res = collection.add(json) 
        # Log name and email as success
        logger.info(f"Lead saved for {json}")

        # Sent slack message to #ohack-dev-leads
        slack_message = f"New lead! Name:`{json['name']}` Email:`{json['email']}`"
        send_slack(slack_message, "ohack-dev-leads")

        success_send_email = send_welcome_email( json["name"], json["email"] )
        if success_send_email:
            logger.info(f"Sent welcome email to {json['email']}")
            # Update db to add when email was sent
            collection.document(insert_res[1].id).update({
                "welcome_email_sent": datetime.now().isoformat()
            })
        return True

# Create an event loop and run the save_lead function asynchronously
@limits(calls=30, period=ONE_MINUTE)
async def save_lead_async(json):
    await save_lead(json)

def add_utm(url, source="email", medium="welcome", campaign="newsletter_signup", content=None):
    utm_string = f"utm_source={source}&utm_medium={medium}&utm_campaign={campaign}"
    if content:
        utm_string += f"&utm_content={content}"
    return f"{url}?{utm_string}"

# This was only needed to send the first wave of emails for leads, no longer needed
def send_welcome_emails():
    logger.info("Sending welcome emails")
    # Get all leads where welcome_email_sent is None
    db = get_db()    
    
    # Get all leads from the DB
    query = db.collection('leads').stream()
    # Go through each lead and remove the ones that have already been sent
    leads = []
    for lead in query:
        lead_dict = lead.to_dict()        
        if "welcome_email_sent" not in lead_dict and "email" in lead_dict and lead_dict["email"] is not None and lead_dict["email"] != "":
            leads.append(lead)

    
    send_email = False # Change to True to send emails

    # Don't send to duplicate emails - case insensitive
    emails = set()

    for lead in leads:
        lead_dict = lead.to_dict()
        email = lead_dict["email"].lower()        
        if email in emails:
            logger.info(f"Skipping duplicate email {email}")
            continue

        logger.info(f"Sending welcome email to '{lead_dict['name']}' {email} for {lead.id}")

        if send_email:
            success_send_email = send_welcome_email(lead_dict["name"], email)
            if success_send_email:
                logger.info(f"Sent welcome email to {email}")
                # Update db to add when email was sent
                lead.reference.update({
                    "welcome_email_sent": datetime.now().isoformat()
                })
        emails.add(email)

def send_nonprofit_welcome_email(organization_name, contact_name, email):    
    resend.api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")

    subject = "Welcome to Opportunity Hack: Tech Solutions for Your Nonprofit!"

    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"nonprofit_header_image_{image_number}"

    '''
    TODO: Add these pages and then move these down into the email we send

    <li><a href="{add_utm('https://ohack.dev/nonprofits/dashboard', content=image_utm_content)}">Access Your Nonprofit Dashboard</a> - Track your project's progress</li>
    <li><a href="{add_utm('https://ohack.dev/nonprofits/resources', content=image_utm_content)}">Nonprofit Resources</a> - Helpful guides for working with tech teams</li>

    <li><a href="{add_utm('https://ohack.dev/nonprofits/project-submission', content=image_utm_content)}">Submit or Update Your Project</a></li>
    <li><a href="{add_utm('https://ohack.dev/nonprofits/volunteer-communication', content=image_utm_content)}">Tips for Communicating with Volunteers</a></li>
    '''

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to Opportunity Hack</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=f'nonprofit_header_image_{image_number}')}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">
        
        <h1 style="color: #0088FE;">Welcome {organization_name} to Opportunity Hack!</h1>
        
        <p>Dear {contact_name},</p>
        
        <p>We're excited to welcome {organization_name} to the Opportunity Hack community! We're here to connect your nonprofit with skilled tech volunteers to bring your ideas to life.</p>
        
        <h2 style="color: #0088FE;">What's Next?</h2>
        <ul>            
            <li><a href="{add_utm('https://ohack.dev/office-hours', content=image_utm_content)}">Join our weekly Office Hours</a> - Get your questions answered</li>
            <li><a href="{add_utm('https://ohack.dev/about/process', content=image_utm_content)}">Understanding Our Process</a> - Learn how we match you with volunteers</li>
            <li><a href="{add_utm('https://ohack.dev/nonprofits', content=image_utm_content)}">Explore Nonprofit Projects</a> - See what we've worked on</li>                        
        </ul>
        
        <h2 style="color: #0088FE;">Important Links:</h2>
        <ul>
            <li><a href="{add_utm('https://www.ohack.dev/about/success-stories', content=image_utm_content)}">Success Stories</a> - See how other nonprofits have benefited</li>
            <li><a href="{add_utm('https://www.ohack.dev/hack', content=image_utm_content)}">Upcoming Hackathons and Events</a></li>
        </ul>
        
        <p>Questions or need assistance? Reach out on our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack channel</a> or email us at support@ohack.org.</p>
        
        <p>We're excited to work with you to create tech solutions that amplify your impact!</p>
        
        <p>Best regards,<br>The Opportunity Hack Team</p>
        
        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """

    # If organization_name is none, or an empty string, or an unassigned string, or a unprintable character like a space string set it to "Nonprofit Partner"
    if organization_name is None or organization_name == "" or organization_name == "Unassigned" or organization_name.isspace():
        organization_name = "Nonprofit Partner"
    
    # If contact_name is none, or an empty string, or an unassigned string, or a unprintable character like a space string set it to "Nonprofit Friend"
    if contact_name is None or contact_name == "" or contact_name == "Unassigned" or contact_name.isspace():
        contact_name = "Nonprofit Friend"

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{contact_name} <{email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": subject,
        "html": html_content,
    }
    logger.info(f"Sending nonprofit application email to {email}")

    email = resend.Emails.SendParams(params)
    resend.Emails.send(email)    

    logger.info(f"Sent nonprofit application email to {email}")
    return True

def send_welcome_email(name, email):    
    resend.api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")

    subject = "Welcome to Opportunity Hack: Code for Good!"

    # Rotate between images
    images = [
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_2.webp",
        "https://cdn.ohack.dev/ohack.dev/2023_hackathon_3.webp"
    ]
    chosen_image = random.choice(images)
    image_number = images.index(chosen_image) + 1
    image_utm_content = f"header_image_{image_number}"


    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to Opportunity Hack</title>
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <img src="{add_utm(chosen_image, content=f'header_image_{image_number}')}" alt="Opportunity Hack Event" style="width: 100%; max-width: 600px; height: auto; margin-bottom: 20px;">
        
        <h1 style="color: #0088FE;">Hey {name}!! Welcome to Opportunity Hack!</h1>
        
        <p>We're thrilled you've joined our community of tech volunteers making a difference!</p>
        
        <p>At Opportunity Hack, we believe in harnessing the power of code for social good. Our mission is simple: connect skilled volunteers like you with nonprofits that need tech solutions.</p>
        
        <h2 style="color: #0088FE;">Ready to dive in?</h2>
        <ul>
            <li><a href="{add_utm('https://ohack.dev/nonprofits', content=image_utm_content)}">Explore Nonprofit Projects</a></li>
            <li><a href="{add_utm('https://ohack.dev/about/hearts', content=image_utm_content)}">Learn about our Hearts System</a></li>
            <li><a href="{add_utm('https://ohack.dev/office-hours', content=image_utm_content)}">Join our weekly Office Hours</a></li>
            <li><a href="{add_utm('https://ohack.dev/profile', content=image_utm_content)}">Update your profile</a></li>
            <li><a href="{add_utm('https://github.com/opportunity-hack/frontend-ohack.dev/issues', content=image_utm_content)}">Jump in: check out our open GitHub Issues</a></li>
        </ul>
        
        <p>Got questions? Reach out on our <a href="{add_utm('https://ohack.dev/signup', content=image_utm_content)}">Slack channel</a>.</p>
        
        <p>Together, we can code for change!</p>
        
        <p>The Opportunity Hack Team</p>
        
        <!-- Tracking pixel for email opens -->
        <img src="{add_utm('https://ohack.dev/track/open.gif', content=image_utm_content)}" alt="" width="1" height="1" border="0" style="height:1px!important;width:1px!important;border-width:0!important;margin-top:0!important;margin-bottom:0!important;margin-right:0!important;margin-left:0!important;padding-top:0!important;padding-bottom:0!important;padding-right:0!important;padding-left:0!important"/>
    </body>
    </html>
    """


    # If name is none, or an empty string, or an unassigned string, or a unprintable character like a space string set it to "OHack Friend"
    if name is None or name == "" or name == "Unassigned" or name.isspace():
        name = "OHack Friend"
    

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{name} <{email}>",
        "cc": "questions@ohack.org",
        "reply_to": "questions@ohack.org",
        "subject": subject,
        "html": html_content,
    }

    email = resend.Emails.SendParams(params)
    resend.Emails.send(email)
    debug(logger, "Processing email", email=email)
    return True


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
    slack_user = get_slack_user_from_propel_user_id(propel_id)
    slack_user_id = slack_user["sub"]

    logger.info(f"Save Profile Metadata for {slack_user_id} {json}")

    json = json["metadata"]

    # See if the user exists
    user = get_user_from_slack_id(slack_user_id)
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
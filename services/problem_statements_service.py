from datetime import datetime
from ratelimit import limits
from common.utils.slack import invite_user_to_channel, send_slack, send_slack_audit
from model.problem_statement import ProblemStatement
from model.user import User
from db.db import (delete_helping, fetch_hackathon, fetch_problem_statements,
                  insert_helping, delete_problem_statement, fetch_problem_statement,
                  insert_problem_statement, update_problem_statement,
                  insert_problem_statement_hackathon, update_problem_statement_hackathons)
import logging
from cachetools import cached, TTLCache
from cachetools.keys import hashkey
import uuid
from services import users_service
from common.log import get_logger
from common.exceptions import InvalidInputError

logger = get_logger(__name__)

ONE_MINUTE = 60
CACHE_TTL = 600  # 10 minutes

@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement(d):
    """
    Create or update a problem statement.
    Raises InvalidInputError if validation fails.
    """
    try:
        validate_problem_statement(d)
        
        p = ProblemStatement()
        p.update(d)

        if p.id is None:
            p = insert_problem_statement(p)
        else:
            p = update_problem_statement(p)

        # Clear relevant caches
        get_problem_statement.cache_clear()
        #get_problem_statements.cache_clear()

        send_slack_audit(action="save_problem_statement",
                        message="Saving", payload=d)

        return p

    except Exception as e:
        logger.error(f"Error saving problem statement: {str(e)}")
        raise

def validate_problem_statement(data):
    """Validate problem statement data"""
    required_fields = ['title', 'description']
    
    for field in required_fields:
        if field not in data or not data[field].strip():
            raise InvalidInputError(f"Missing or empty required field: {field}")

    # Add any additional validation logic here
    return True

@cached(cache=TTLCache(maxsize=100, ttl=CACHE_TTL))
def get_problem_statement(id):
    """Get a single problem statement by ID"""
    logger.debug(f"get_problem_statement start id={id}")    
    
    problem_statement = fetch_problem_statement(id)
    
    if problem_statement is None:
        logger.warning("get_problem_statement end (no results)")
    else:                                
        logger.info(f"get_problem_statement end (with result):{problem_statement}")
        
    return problem_statement

def remove_problem_statement(id):
    """Delete a problem statement"""
    try:
        result = delete_problem_statement(id)
        
        # Clear caches
        get_problem_statement.cache_clear()
        #get_problem_statements.cache_clear()
        
        return result
    except Exception as e:
        logger.error(f"Error deleting problem statement: {str(e)}")
        raise

# @cached(cache=TTLCache(maxsize=1, ttl=CACHE_TTL), key=lambda: hashkey('all_problem_statements'))
def get_problem_statements():
    """Get all problem statements"""
    return fetch_problem_statements()

@limits(calls=50, period=ONE_MINUTE)
def update_problem_statement_fields(d):
    
    problem_statement = None
    if 'id' in d and d['id'] is not None:
        problem_statement = fetch_problem_statement(d['id'])
    
    if problem_statement is not None:
        problem_statement.update(d)
        problem_statement.id = d['id']
        return update_problem_statement(problem_statement)
    else:
        return None
    
@limits(calls=100, period=ONE_MINUTE)
def save_helping_status(propel_user_id, d):
    logger.info(f"save_helping_status {propel_user_id} // {d}")
    user = users_service.get_user_from_propel_user_id(propel_user_id)

    slack_message = None

    # Do the actual data wrangling
    problem_statement = save_user_helping_status(user, d)

    problem_statement_title = problem_statement.title
    problem_statement_slack_channel = problem_statement.slack_channel

    helping_status = d["status"] # helping or not_helping

    problem_statement_id = d["problem_statement_id"]

    npo_id =  d["npo_id"] if "npo_id" in d else ""

    mentor_or_hacker = d["type"]

    url = ""
    if npo_id == "":
        url = f"for project https://ohack.dev/project/{problem_statement_id}"
    else:
        url = f"for nonprofit https://ohack.dev/nonprofit/{npo_id} on project https://ohack.dev/project/{problem_statement_id}"


    slack_user_id = None
    try:
        slack_user_id = user.user_id.split("-")[1]  # Example user_id = oauth2|slack|T1Q7116BH-U041117EYTQ
        invite_user_to_channel(user_id=slack_user_id,
                            channel_name=problem_statement_slack_channel)
    except Exception:
        pass # Don't return error if slack invite fails.


    if slack_user_id is not None:
        try:
            slack_message = f"<@{slack_user_id}>"

            if "helping" == helping_status:
                slack_message = f"{slack_message} is helping as a *{mentor_or_hacker}* on *{problem_statement_title}* {url}"
            else:
                slack_message = f"{slack_message} is _no longer able to help_ on *{problem_statement_title}* {url}"

            send_slack(message=slack_message,
                    channel=problem_statement_slack_channel)
        except:
            pass # Don't return error if slack message fails.

    return problem_statement

def save_user_helping_status(user: User, d):

    logger.info(f"save_user_helping_status {user.serialize()} // {d}")
    helping_status = d["status"] # helping or not_helping
    
    problem_statement_id = d["problem_statement_id"]
    mentor_or_hacker = d["type"]

    helping_date = datetime.now().isoformat()
    
    to_add = {
        "user": user.id,
        "slack_user": user.user_id,
        "type": mentor_or_hacker,
        "timestamp": helping_date
    }
    
    try: 
        send_slack_audit(action="helping", message=user.user_id, payload=to_add)
    except Exception:
        pass

    problem_statement: ProblemStatement | None = None
  
    if "helping" == helping_status:
        problem_statement = insert_helping(problem_statement_id, user, mentor_or_hacker, helping_date)
    else:
        problem_statement = delete_helping(problem_statement_id, user)

    return problem_statement

# TODO: Look at reshaping JSON payload to be more natural. Do we actually need the event title?
@limits(calls=100, period=ONE_MINUTE)
def link_problem_statements_to_events(json):    
    # JSON should be in the format of
    # {
    #   "mapping": {
    #     "<problemStatementId>" : [ "<eventTitle1>|<eventId1>", "<eventTitle2>|<eventId2>" ]
    #   }
    # }
    logger.debug(f"Linking payload {json}")
    
    result = []
    
    data = json["mapping"]
    for problemId, eventList in data.items():
        
        problem_statement = fetch_problem_statement(problemId)
        
        if problem_statement is not None:

            hackathons = []
            
            for event in eventList:
                logger.info(f"Checking event: {event}")
                eventId = None
                # <eventTitle>|<eventId>
                if "|" in event:
                    eventId = event.split("|")[1]
                else:
                    eventId = event

                hackathon = fetch_hackathon(eventId)
                hackathons.append(hackathon)

            update_problem_statement_hackathons(problem_statement, hackathons)

            result.append(fetch_problem_statement(problem_statement.id))

    return result
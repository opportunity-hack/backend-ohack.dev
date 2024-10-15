from datetime import datetime
import os
from ratelimit import limits
import requests
from common.utils.slack import send_slack_audit
from model.user import User
from db.db import delete_user_by_db_id, delete_user_by_user_id, fetch_user_by_user_id, fetch_user_by_db_id, fetch_users, insert_user, update_user, get_user_profile_by_db_id, upsert_profile_metadata
import logging
import pytz
from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey
from common.log import get_log_level

logger = logging.getLogger("myapp")
logger.setLevel(logging.INFO)

#TODO consts file?
ONE_MINUTE = 1*60

#TODO: get last part of this from env
USER_ID_PREFIX = "oauth2|slack|T1Q7936BH-" #the followed by UXXXXX for slack

def clear_cache():        
    get_profile_metadata.cache_clear()

def finish_saving_insert(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    user = User()
    user.user_id = user_id
    user.email_address = email
    user.last_login = last_login
    user.profile_image = profile_image
    user.name = name
    user.nickname = nickname
    return insert_user(user)

def finish_saving_update(
        user,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        return update_user(user)

@limits(calls=50, period=ONE_MINUTE)
def update_user_fields(
        id=None,
        user_id=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    
    user = None
    if id is not None:
        user = fetch_user_by_db_id(id)
    else:
        user = fetch_user_by_user_id(user_id)

    if user is not None:
        return finish_saving_update(user, last_login, profile_image, name, nickname)
    else:
        return None

@limits(calls=50, period=ONE_MINUTE)
def save_user(
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
        return None

    # TODO: Call get_user from db here
    user = fetch_user_by_user_id(user_id)

    if user is not None:
        user = finish_saving_update(user, last_login, profile_image, name, nickname)
        
    else:
        user = finish_saving_insert(user_id, email, last_login, profile_image, name, nickname)

    return user if user is not None else None

def get_slack_user_from_token(token):
    resp = requests.get(
        "https://slack.com/api/openid.connect.userInfo", 
        headers={"Authorization": f"Bearer {token}"}
    )
    '''
    {'ok': True, 'sub': 'UC31XTRT5', 'https://slack.com/user_id': 'UC31XTRT5', 'https://slack.com/team_id': 'T1Q7936BH', 'email': 'greg.vannoni@gmail.com', 'email_verified': True, 'date_email_verified': 1632009763, 'name': 'Greg V [Staff/Mentor]', 'picture': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_512.png', 'given_name': 'Greg', 'family_name': 'V [Staff/Mentor]', 'locale': 'en-US', 'https://slack.com/team_name': 'Opportunity Hack', 'https://slack.com/team_domain': 'opportunity-hack', 'https://slack.com/user_image_24': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_24.png', 'https://slack.com/user_image_32': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_32.png', 'https://slack.com/user_image_48': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_48.png', 'https://slack.com/user_image_72': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_72.png', 'https://slack.com/user_image_192': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_192.png', 'https://slack.com/user_image_512': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_512.png', 'https://slack.com/user_image_1024': 'https://avatars.slack-edge.com/2020-10-18/1442299648180_56142a4494226a9ea4b5_1024.png', 'https://slack.com/team_image_34': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_34.png', 'https://slack.com/team_image_44': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_44.png', 'https://slack.com/team_image_68': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_68.png', 'https://slack.com/team_image_88': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_88.png', 'https://slack.com/team_image_102': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_102.png', 'https://slack.com/team_image_132': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_132.png', 
    'https://slack.com/team_image_230': 'https://avatars.slack-edge.com/2017-09-26/246651063104_30aaa970e3bcf4a8ac6b_230.png', 'https://slack.com/team_image_default': False}
    '''
    
    json = resp.json()
    if not json["ok"]:
        logger.warning(f"Error getting user details from Slack or Propel APIs: {json}")
        return None    
    
    if "sub" not in json:
        logger.warning(f"Error getting user details from Slack or Propel APIs: {json}")
        return None

    # Add prefix to sub 
    json["sub"] = USER_ID_PREFIX + json["sub"]

    logger.info(f"Slack RESP: {json}")
    return json

def get_user_from_propel_user_id(propel_id):
    slack_user = get_slack_user_from_propel_user_id(propel_id)
    user_id = slack_user["sub"]
    return get_user_from_slack_id(user_id)

def get_slack_user_from_propel_user_id(propel_id):
    #TODO: Do we want to be using os.getenv here?

    url = f"{os.getenv('PROPEL_AUTH_URL')}/api/backend/v1/user/{propel_id}/oauth_token"

    logger.debug(f"Propel URL: {url}")

    resp = requests.get(
        url, 
        headers={"Authorization": f"Bearer {os.getenv('PROPEL_AUTH_KEY')}"}
        )    
    logger.debug(f"Propel RESP: {resp}")
    json = resp.json()    
    logger.debug(f"Propel RESP JSON: {json}")
    
    slack_token = json['slack']['access_token']
    return get_slack_user_from_token(slack_token)

def get_propel_user_details_by_id(propel_id):    
    slack_user = get_slack_user_from_propel_user_id(propel_id)    
    user_id = slack_user["sub"]
    
    email = slack_user["email"]
    # Use todays date in UTC  (Z)
    last_login = datetime.now().isoformat() + "Z"
    # Print the last login in native format and in Arizona time
    
    logger.debug(f"Last Login: {last_login} {datetime.now().astimezone(pytz.timezone('US/Arizona')).isoformat()}")  

    # https://slack.com/user_image_192
    profile_image = slack_user["https://slack.com/user_image_192"]
    name = slack_user["name"]
    nickname = slack_user["given_name"]

    return email, user_id, last_login, profile_image, name, nickname

def get_profile_by_db_id(id):
    # Log
    logger.debug(f"Get User By ID: {id}")
    u = get_user_profile_by_db_id(id)

    res = None

    # Only keep these fields since this is a public api
    fields = ["name", "profile_image", "user_id", "nickname", "github"] #TODO: Wait. We are getting extended profile data (e.g. hackathons above just to pitch it out here?)

    if u is not None:
        # Check if the field is in the response first
        temp = vars(u)
        res = {k: temp[k] for k in fields if k in temp}

    
    logger.debug(f"Get User By ID Result: {res}")
    return res    
    
# 10 minute cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=600))
@limits(calls=100, period=ONE_MINUTE)
def get_profile_metadata(propel_id):
    logger.debug("Profile Metadata")
    
    email, user_id, last_login, profile_image, name, nickname = get_propel_user_details_by_id(propel_id)
    
    send_slack_audit(
        action="login", message=f"User went to profile: {user_id} with email: {email}")
    

    logger.debug(f"Account Details:\
            \nEmail: {email}\nSlack User ID: {user_id}\n\
            Last Login:{last_login}\
            Image:{profile_image}")

    # Call firebase to see if account exists and save these details
    db_id = save_user(
            user_id=user_id,
            email=email,
            last_login=last_login,
            profile_image=profile_image,
            name=name,
            nickname=nickname)

    # Get all of the user history and profile data from the DB
    response = get_history(db_id)
    logger.debug(f"get_profile_metadata {response}")

    return response #TODO: Breaking API change

# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_history(db_id):
    logger.debug("Get History Start")
    result = get_user_profile_by_db_id(db_id)

    logger.debug(f"RESULT\n{result}")
    return result

def save_profile_metadata(propel_id, json):
    
    send_slack_audit(action="save_profile_metadata", message="Saving", payload=json)
    
    slack_user = get_slack_user_from_propel_user_id(propel_id)
    slack_user_id = slack_user["sub"]

    logger.info(f"Save Profile Metadata for {slack_user_id} {json}")

    json = json["metadata"]
        
    # See if the user exists
    user = fetch_user_by_user_id(slack_user_id)
    if user is None:
        return
    else:
        logger.info(f"User exists: {user.id}")   
        user.update_from_metadata(json)     
        upsert_profile_metadata(user)
    
        # Clear cache for get_profile_metadata
        get_profile_metadata.cache_clear()
            
    return user #TODO: Breaking API change

def get_user_by_db_id(id):
    return fetch_user_by_db_id(id)

def get_user_from_slack_id(user_id):
    return fetch_user_by_user_id(user_id)

def remove_user_by_db_id(id):
    return delete_user_by_db_id(id)

def remove_user_by_slack_id(user_id):
    return delete_user_by_user_id(user_id)

@limits(calls=100, period=ONE_MINUTE)
def get_users():
    return fetch_users()

def save_volunteering_time(propel_id, json):
    logger.info(f"Save Volunteering Time for {propel_id} {json}")
    slack_user = get_slack_user_from_propel_user_id(propel_id)
    slack_user_id = slack_user["sub"]

    logger.info(f"Save Volunteering Time for {slack_user_id} {json}")

    # Get the user
    user = fetch_user_by_user_id(slack_user_id)
    if user is None:
        logger.error(f"User not found for {slack_user_id}")
        return

    timestamp = datetime.now().isoformat() + "Z"  
    reason = json["reason"] # The kind of volunteering being done  
    
    if "finalHours" in json:
        finalHours = json["finalHours"] # This is sent at when volunteering is done        
        if finalHours is None:
            logger.error(f"finalHours is None for {slack_user_id}")
            return
            
        user.volunteering.append({
            "timestamp": timestamp,
            "finalHours": round(finalHours,2),
            "reason": reason
            })

        # Add to the total
        upsert_profile_metadata(user)

    # We keep track of what the user is committing to do but we don't show this
    # The right way to do this is likely to get a session id when they start volunteering and the frontend uses that to close out the volunteering session when it is done
    # But this way is simpler for now
    elif "commitmentHours" in json:
        commitmentHours = json["commitmentHours"] # This is sent at the start of volunteering        
        if commitmentHours is None:
            logger.error(f"commitmentHours is None for {slack_user_id}")
            return
        
        user.volunteering.append({
            "timestamp": timestamp,
            "commitmentHours": round(commitmentHours,2),
            "reason": reason
            })
        upsert_profile_metadata(user)

    # Clear cache for get_profile_metadata
    get_profile_metadata.cache_clear()

    return user


def get_volunteering_time(propel_id, start_date, end_date):
    logger.info(f"Get Volunteering Time for {propel_id} {start_date} {end_date}")
    slack_user = get_slack_user_from_propel_user_id(propel_id)
    slack_user_id = slack_user["sub"]

    logger.info(f"Get Volunteering Time for {slack_user_id} start: {start_date} end: {end_date}")

    # Get the user
    user = fetch_user_by_user_id(slack_user_id)
    if user is None:
        return

    # Filter the volunteering data
    volunteeringActiveTime = []
    for v in user.volunteering:
        if "finalHours" in v:
            if start_date is not None and end_date is not None:
                if v["timestamp"] >= start_date and v["timestamp"] <= end_date:
                    volunteeringActiveTime.append(v)
            else:
                volunteeringActiveTime.append(v)

    volunteeringCommittmentTime = []
    for v in user.volunteering:
        if "commitmentHours" in v:
            if start_date is not None and end_date is not None:
                if v["timestamp"] >= start_date and v["timestamp"] <= end_date:
                    volunteeringCommittmentTime.append(v)
            else:
                volunteeringCommittmentTime.append(v)
    
    totalActiveHours = sum([v["finalHours"] for v in volunteeringActiveTime])    
    totalCommitmentHours = sum([v["commitmentHours"] for v in volunteeringCommittmentTime]) 

    # Merge volunteeringActiveTime and volunteeringCommittmentTime
    # This is a bit of a hack but it is easier to do it this way than to try to do it in the frontend
    allVolunteering = volunteeringActiveTime + volunteeringCommittmentTime

    logger.debug(f"allVolunteering: {allVolunteering}  || volunteeringActiveTime: {volunteeringActiveTime} volunteeringCommittmentTime: {volunteeringCommittmentTime} Total Active Hours: {totalActiveHours} Total Commitment Hours: {totalCommitmentHours}")   
    
    return allVolunteering, totalActiveHours, totalCommitmentHours
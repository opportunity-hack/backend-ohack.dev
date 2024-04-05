from datetime import datetime
import os
from ratelimit import limits
import requests
from model.user import User
from db.db import get_user, save_user, upsert_user, get_user_by_doc_id
import logging
import pytz

#TODO: service level logging?
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

#TODO consts file?
ONE_MINUTE = 1*60

#TODO: get last part of this from env
USER_ID_PREFIX = "oauth2|slack|T1Q7936BH-" #the followed by UXXXXX for slack

@limits(calls=50, period=ONE_MINUTE)
def save_user(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    
    res = None

    logger.info(f"User Save for {user_id} {email} {last_login} {profile_image} {name} {nickname}")
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc


    if user_id is None or email is None or last_login is None or profile_image is None:
        logger.error(
            f"Empty values provided for user_id: {user_id},\
                email: {email}, or last_login: {last_login}\
                    or profile_image: {profile_image}")
        return None

    # TODO: Call get_user from db here
    user = get_user(user_id)

    if user is not None:
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        res = upsert_user(user)
    else:
        user = User()
        user.user_id = user_id
        user.email_address = email
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        res = save_user(user)

    return user.id if res is not None else None

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

def get_slack_user_from_propel_user_id(propel_id):
    #TODO: Do we want to be using os.getenv here?
    resp = requests.get(
        f"{os.getenv('PROPEL_AUTH_URL')}/api/backend/v1/user/{propel_id}/oauth_token", headers={"Authorization": f"Bearer {os.getenv('PROPEL_AUTH_KEY')}"}
        
        )    
    json = resp.json()    
    logger.debug(f"Propel RESP: {json}")
    
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

def get_user_by_id(id):
    # Log
    logger.debug(f"Get User By ID: {id}")
    u = get_user_by_doc_id(id)

    res = None

    # Only keep these fields since this is a public api
    fields = ["name", "profile_image", "user_id", "nickname"]

    if u is not None:
        # Check if the field is in the response first
        temp = u.to_dict()
        res = {k: temp[k] for k in fields if k in temp}

    
    logger.debug(f"Get User By ID Result: {res}")
    return res    

def get_user_from_slack_id(user_id):
    return get_user(user_id)
    

import requests
from . import safe_get_env_var
import datetime, json
from ratelimiter import RateLimiter
from slack_sdk import WebClient
from slack_sdk.models.blocks import SectionBlock
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
from requests.exceptions import ConnectionError
from cachetools import TTLCache, cached
from ratelimit import limits, sleep_and_retry

load_dotenv()

SLACK_URL = safe_get_env_var("SLACK_WEBHOOK")

# add logger
import logging
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)

# TODO: What is the purpose of this message?
def send_slack_audit(action="", message="", payload=None):
    if not SLACK_URL or SLACK_URL == "":
        logger.warning("SLACK_URL not set, returning")
        return

    json = {
        "text": f"[{action}] {message}"
    }

    if payload:
        json = {
            "text": f"[{action}] {message}\n{payload}"
        }

    try: 
        requests.post(json=json, url=SLACK_URL)
    except ConnectionError:
        pass #Eat this error. The request from the frontend should not fail if we can't contact slack


@RateLimiter(max_calls=40, period=60)
def presence(user_id=None):
    if user_id is None:
        return
    client = get_client()
    return client.users_getPresence(user=user_id)

@RateLimiter(max_calls=20, period=60)
def userlist():
    client = get_client()
    return client.users_list()


def get_active_users():
    aresult = []
    counter = 0
    for member in userlist()["members"]:
        # get updated time in seconds and print as date
        updated = datetime.datetime.fromtimestamp(
            member["updated"]).strftime('%Y-%m-%d %H:%M:%S')

        # Print the 5th member raw details
        here = False        
        #print(json.dumps(member, indent=4, sort_keys=True))              

        deleted = True if ("deleted" in member and member["deleted"]) else False            
        if not deleted:            
            here = presence(user_id=member["id"])["presence"] != "away"
        
        
        is_email_confirmed = member["is_email_confirmed"] if "is_email_confirmed" in member else ""
        display_name = member["profile"]["display_name_normalized"] if "display_name" in member["profile"] else ""
        real_name = member["profile"]["real_name_normalized"] if "real_name" in member["profile"] else ""

        # If last updated in the last 30 days, add to list
        #if (datetime.datetime.now() - datetime.datetime.fromtimestamp(member["updated"])).days < 30:
        if(here):
            aresult.append(f"@{real_name} | {member['name']} ({member['id']}) - {updated}")
        
    print(len(aresult))        

    return aresult


def get_slack_token():
    slack_token = safe_get_env_var("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, returning")
        return
    return slack_token


def slack_id_from_user_id(user_id):
    if user_id is None:
        return
    else:
        # Example user_id = oauth2|slack|T2Q7222BH-U012127EYAQ
        return user_id.split("|")[2].split("-")[1]

def get_client():
    token = get_slack_token()
    # logger.info(f"Creating new client, token: {token}")
    client = WebClient(token=token)
    return client


def get_channel_id_from_channel_name(channel_name):
    # Get channel name
    client = get_client()
    result = client.conversations_list(exclude_archived=True, limit=1000)

    logger.info(f"Looking for slack channel {channel_name}...")
    for c in result["channels"]:
        if c["name"] == channel_name:
            logger.info(f"Found Channel! {channel_name}")
            return c["id"]
    return None


def invite_user_to_channel(user_id, channel_name):
    logger.debug("invite_user_to_channel start")
    client = get_client()
    channel_id = get_channel_id_from_channel_name(channel_name)
    logger.info(f"Channel ID: {channel_id}")

    # If user_id has a - in it, use split to get the last part
    if "-" in user_id:
        user_id = user_id.split("-")[1]        

    try:
        client.conversations_join(channel=channel_id)
        result = client.conversations_invite(channel=channel_id, users=user_id)        
    except Exception as e:
        logger.error(
            "Caught exception - this might be okay if the user is already in the channel.")
        #log error
        logger.error(e)

    logger.debug("invite_user_to_channel end")

def create_slack_channel(channel_name):
    logger.debug("create_slack_channel start")
    client = get_client()
    
    # See if channel exists
    channel_id = get_channel_id_from_channel_name(channel_name)
    if channel_id is not None:
        logger.info(f"Channel {channel_name} already exists with id {channel_id}")
        return channel_id

    try:
        result = client.conversations_create(name=channel_name)
        logger.info(f"Created channel {channel_name} with id {result['channel']['id']}")
        return result["channel"]["id"]
    except Exception as e:
        logger.error("Caught exception")
        logger.error(e)
    logger.debug("create_slack_channel end")


def send_slack(message="", channel="", icon_emoji=None, username="Hackathon Bot"):
    client = get_client()
    channel_id = get_channel_id_from_channel_name(channel)
    logger.info(f"Got channel id {channel_id}")
    
    if channel_id is None:
        logger.warning("Unable to get channel id from name, might be a user?")
        channel_id = channel
    
    logger.info("Sending message...")
    try:
        response = client.chat_postMessage(
            channel=channel_id,
            blocks=[
                SectionBlock(
                    text={
                        "type": "mrkdwn",
                        "text": message
                    }
                )
            ],
            username=username,
            icon_emoji=icon_emoji
        )
    except SlackApiError as e:
        logger.error(e.response["error"])
        assert e.response["error"]




# Assuming 50 calls per minute for Slack API
CALLS = 50
RATE_LIMIT = 60

@sleep_and_retry
@limits(calls=CALLS, period=RATE_LIMIT)
def rate_limited_get_user_info(user_id):
    client = get_client()
    try:
        result = client.users_info(user=user_id)
        return result["user"]
    except SlackApiError as e:
        logger.error(f"Error fetching info for user {user_id}: {e}")
        return None

def get_user_info(user_ids):
    """
    Fetch user information for a list of Slack user IDs.
    
    :param user_ids: List of Slack user IDs (e.g., ["U049S78NLCA", "U049S78NLCB"])
    :return: Dictionary of user information, keyed by user ID
    """
    client = get_client()    

    # Fetch user info for all unique slack_ids
    users_info = {}
    for user_id in user_ids:
        user_info = rate_limited_get_user_info(user_id)
        if user_info:
            users_info[user_id] = {
                "id": user_info["id"],
                "name": user_info["name"],
                "real_name": user_info.get("real_name", ""),
                "display_name": user_info["profile"].get("display_name", ""),
                "email": user_info["profile"].get("email", ""),
                "is_admin": user_info.get("is_admin", False),
                "is_owner": user_info.get("is_owner", False),
                "is_bot": user_info.get("is_bot", False),
                "updated": user_info.get("updated", 0)
            }

    return users_info
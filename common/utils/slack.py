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
import threading

load_dotenv()

SLACK_URL = safe_get_env_var("SLACK_WEBHOOK")

# add logger
import logging
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)


def send_slack_audit(action="", message="", payload=None):
    if not SLACK_URL or SLACK_URL == "":
        logger.warning("SLACK_URL not set, returning")
        return

    json = {
        "text": f"[{action}] {message}"
    }

    if payload:
        # Create a copy to avoid mutating the original payload
        payload_copy = payload.copy()               

        if "recaptchaToken" in payload_copy:
            del payload_copy["recaptchaToken"]

        json = {
            "text": f"[{action}] {message}\n{payload_copy}"
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
    users = []
    next_cursor = None
    
    while True:
        response = client.users_list(limit=1000, cursor=next_cursor)
        users.extend(response["members"])
        
        # Check if there are more pages
        next_cursor = response.get("response_metadata", {}).get("next_cursor")
        if not next_cursor:
            break
    
    return {"members": users}


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

def get_slack_user_by_email(email):
    """
    Get Slack user by email address.
    
    :param email: Email address of the user
    :return: User information if found, None otherwise
    """
    client = get_client()
    try:
        result = client.users_lookupByEmail(email=email)
        return result["user"]
    except SlackApiError as e:
        logger.error(f"Error fetching user by email {email}: {e}")
        return None
    
    
@cached(cache=TTLCache(maxsize=100, ttl=2))  # Cache for 5 minutes
@sleep_and_retry
@limits(calls=20, period=60)  # Rate limiting
def get_channel_id_from_channel_name(channel_name):
    """
    Get channel ID from channel name with caching and pagination support.
    
    :param channel_name: Name of the channel to find
    :return: Channel ID if found, None otherwise
    """
    client = get_client()
    
    logger.info(f"Looking for slack channel {channel_name}...")
    
    try:
        cursor = None
        while True:
            # Use pagination to handle workspaces with many channels
            result = client.conversations_list(
                exclude_archived=True, 
                limit=999999,
                types="private_channel,public_channel"  # Specify channel types
            )
            
            logger.debug(f"Found {len(result['channels'])} channels in this batch")
            
            # Search through current batch
            for channel in result["channels"]:
                if channel["name"] == channel_name:
                    logger.info(f"Found Channel! {channel_name} -> {channel['id']}")
                    return channel["id"]
            
            # Check if there are more pages
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
                
        logger.warning(f"Channel {channel_name} not found")
        return None
        
    except SlackApiError as e:
        logger.error(f"Error fetching channels: {e}")
        return None


def is_channel_id(channel_id):
    # Use conversation_info to check if channel_id is valid
    client = get_client()
    try:
        result = client.conversations_info(channel=channel_id)
        return True
    except SlackApiError as e:
        logger.error(f"Error checking channel ID {channel_id}: {e}")
        return False    


def invite_user_to_channel(user_id, channel_name):
    logger.debug("invite_user_to_channel start")
    client = get_client()
       
       
    channel_id = None 

    if is_channel_id(channel_name):
        logger.info(f"Channel name {channel_name} is actually a channel ID, using it directly")
        channel_id = channel_name
    else:   
        channel_id = get_channel_id_from_channel_name(channel_name)
    
    logger.info(f"Channel ID: {channel_id}")

    # If user_id has a - in it, use split to get the last part
    if "-" in user_id:
        user_id = user_id.split("-")[1]    

    if channel_id is None:
        logger.error(f"Channel {channel_name} not found, cannot invite user {user_id}")
        return    

    # Have the bot join the channel first
    try:
        client.conversations_join(channel=channel_id)
    except Exception as e:
        logger.error(f"Error joining channel {channel_id}: {e} this might be okay if the bot is already in the channel.")
        # Log stack trace
        logger.error(e, exc_info=True)
        return

    # Now invite the user
    try:        
        client.conversations_invite(channel=channel_id, users=user_id)        
    except Exception as e:
        logger.error(
            "Caught exception - this might be okay if the user is already in the channel.")
        #log error
        logger.error(e)
        return False        

    logger.debug("invite_user_to_channel end")
    return True


def invite_user_to_channel_id(user_id, channel_id):
    logger.debug("invite_user_to_channel_id start")
    client = get_client()    
    logger.info(f"Channel ID: {channel_id}")

    # If user_id has a - in it, use split to get the last part
    if "-" in user_id:
        user_id = user_id.split("-")[1]        

    try:
        #client.conversations_join(channel=channel_id)
        result = client.conversations_invite(channel=channel_id, users=user_id)        
    except Exception as e:
        logger.error(
            "Caught exception - this might be okay if the user is already in the channel.")
        #log error stack trace
        logger.error(e, exc_info=True)
        

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
        kwargs = {
            "channel": channel_id,
            "blocks": [
                SectionBlock(
                    text={
                        "type": "mrkdwn",
                        "text": message
                    }
                )
            ],
            "username": username
        }
        
        if icon_emoji:
            kwargs["icon_emoji"] = icon_emoji
        else:
            kwargs["icon_url"] = "https://cdn.ohack.dev/ohack.dev/logos/OpportunityHack_2Letter_Light_Blue.png"
        
        response = client.chat_postMessage(**kwargs)
    except SlackApiError as e:
        logger.error(e.response["error"])
        assert e.response["error"]


def async_send_slack(message="", channel="", icon_emoji=None, username="Hackathon Bot"):
    """
    Send a Slack message asynchronously using threading.
    This allows the calling function to return immediately without waiting for the Slack API call.
    
    :param message: The message to send
    :param channel: The channel name or user ID to send to  
    :param icon_emoji: Optional emoji icon
    :param username: The username for the bot
    """
    def _send_slack_thread():
        try:
            send_slack(message=message, channel=channel, icon_emoji=icon_emoji, username=username)
            logger.info(f"Async Slack message sent successfully to {channel}")
        except Exception as e:
            logger.error(f"Error sending async Slack message to {channel}: {e}")
    
    # Start the thread and let it run in background
    thread = threading.Thread(target=_send_slack_thread, daemon=True)
    thread.start()
    logger.info(f"Started background thread to send Slack message to {channel}")




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
                "is_admin": user_info.get("is_admin", False),
                "is_owner": user_info.get("is_owner", False),
                "is_bot": user_info.get("is_bot", False),
                "updated": user_info.get("updated", 0)
            }

    return users_info
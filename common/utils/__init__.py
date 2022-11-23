from os import environ
import requests

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def safe_get_env_var(key):
    try:
        return environ[key]
    except KeyError:
        raise NameError(f"Missing {key} environment variable.")



SLACK_URL = safe_get_env_var("SLACK_WEBHOOK")
def send_slack_audit(action="", message="", payload=None):
    if not SLACK_URL or SLACK_URL=="":
        print("SLACK_URL not set, returning")
        return 

    json = {
        "text": f"[{action}] {message}"
    }

    if payload:
        json = {
            "text": f"[{action}] {message}\n{payload}" 
        }

    requests.post(json=json, url=SLACK_URL)


def get_slack_token():
    slack_token = safe_get_env_var("SLACK_BOT_TOKEN")
    if not slack_token:
        print("SLACK_BOT_TOKEN not set, returning")
        return
    return slack_token

def get_client():
    token = get_slack_token()
    print("Creating new client, token = ", token)
    client = WebClient(token=token)
    return client

def get_channel_id_from_channel_name(channel_name):
    # Get channel name
    client = get_client()
    result = client.conversations_list(exclude_archived=True, limit=1000)

    print(f"Looking for slack channel {channel_name}...")
    for c in result["channels"]:
        if c["name"] == channel_name:
            print(f"Found Channel! {channel_name}")
            return c["id"]
    return None


def invite_user_to_channel(user_id, channel_name):
    print("invite_user_to_channel start")
    client = get_client()
    channel_id = get_channel_id_from_channel_name(channel_name)

    print(channel_id)
    try:
        client.conversations_join(channel=channel_id)
        result = client.conversations_invite(channel=channel_id, users=user_id)
        print(result)
    except Exception as e:
        print("Caught exception - this might be okay if the user is already in the channel.")
    
    print("invite_user_to_channel end")

def send_slack(message="", channel=""):
    client = get_client()
    channel_id = get_channel_id_from_channel_name(channel)

    # Joining isn't necessary to be able to send messages via chat_postMessage
    #join_result = client.conversations_join(channel=channel_id)
    # print(join_result)
    
    # Post
    print("Sending message...")
    try:
        response = client.chat_postMessage(
            channel=channel_id,
            text=message)
        print(response)
    
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]

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
    if not SLACK_URL:
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


def send_slack(message="", channel=""):
    slack_token = safe_get_env_var("SLACK_BOT_TOKEN")
    if not slack_token:
        print("SLACK_BOT_TOKEN not set, returning")
        return
    
    # TODO: will need to join private channel if not already in it
    client = WebClient(token=slack_token)

    # Get channel name
    result = client.conversations_list(exclude_archived=True, limit=1000)

    channel_id = ""
    for c in result["channels"]:        
        if c["name"] == channel:
            print(f"Found Channel! {channel}")
            channel_id = c["id"]
    

    # Joining isn't necessary to be able to send messages via chat_postMessage
    #join_result = client.conversations_join(channel=channel_id)
    # print(join_result)
    
    # Post
    try:
        response = client.chat_postMessage(
            channel=channel_id,
            text=message)
        print(response)
    
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]

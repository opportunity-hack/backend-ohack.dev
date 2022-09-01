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

    try:
        response = client.chat_postMessage(
            channel="C040GVBCVEH",
            text="Hello from your app! :tada:")
        print(response)
    
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["error"]

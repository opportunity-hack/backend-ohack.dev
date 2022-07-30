from os import environ
import requests

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


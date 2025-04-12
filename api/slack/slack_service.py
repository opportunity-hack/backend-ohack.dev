import requests
import datetime, json
from ratelimiter import RateLimiter
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
from common.utils import safe_get_env_var
load_dotenv()

SLACK_URL = safe_get_env_var("SLACK_WEBHOOK")
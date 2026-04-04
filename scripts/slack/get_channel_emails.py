"""
Get email addresses for all members of a Slack channel.

Usage:
    python -m scripts.slack.get_channel_emails 2026-spring-wics-mentors
    python -m scripts.slack.get_channel_emails '#2026-spring-wics-mentors'
    python -m scripts.slack.get_channel_emails 2026-spring-wics-mentors --csv
"""

import argparse
import sys
import os

# Allow running from repo root as: python -m scripts.slack.get_channel_emails
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

from common.utils.slack import get_client, get_channel_id_from_channel_name
from slack_sdk.errors import SlackApiError
from ratelimit import limits, sleep_and_retry

import logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

CALLS = 50
RATE_LIMIT = 60


def get_channel_members(channel_id):
    """Get all member IDs from a channel using pagination."""
    client = get_client()
    member_ids = []
    cursor = None

    while True:
        kwargs = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor

        result = client.conversations_members(**kwargs)
        member_ids.extend(result["members"])

        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    return member_ids


@sleep_and_retry
@limits(calls=CALLS, period=RATE_LIMIT)
def get_user_email(client, user_id):
    """Fetch a single user's profile to get their email."""
    try:
        result = client.users_info(user=user_id)
        user = result["user"]

        if user.get("is_bot") or user.get("id") == "USLACKBOT":
            return None

        profile = user.get("profile", {})
        return {
            "user_id": user["id"],
            "real_name": user.get("real_name", ""),
            "display_name": profile.get("display_name", ""),
            "email": profile.get("email", ""),
        }
    except SlackApiError as e:
        logger.error(f"Error fetching user {user_id}: {e}")
        return None


def get_emails_for_channel(channel_name):
    """Get email addresses for all human members of a Slack channel."""
    # Strip leading # if present
    channel_name = channel_name.lstrip("#")

    channel_id = get_channel_id_from_channel_name(channel_name)
    if not channel_id:
        logger.error(f"Channel '{channel_name}' not found")
        sys.exit(1)

    logger.info(f"Found channel '{channel_name}' -> {channel_id}")

    member_ids = get_channel_members(channel_id)
    logger.info(f"Found {len(member_ids)} members in channel")

    client = get_client()
    members = []
    for user_id in member_ids:
        info = get_user_email(client, user_id)
        if info and info["email"]:
            members.append(info)

    members.sort(key=lambda m: m["real_name"].lower())
    return members


def main():
    parser = argparse.ArgumentParser(
        description="Get email addresses for members of a Slack channel"
    )
    parser.add_argument(
        "channel",
        help="Slack channel name (with or without #)"
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Output as CSV instead of plain text"
    )
    args = parser.parse_args()

    members = get_emails_for_channel(args.channel)

    if not members:
        print("No members with email addresses found.")
        return

    if args.csv:
        print("email,real_name,display_name,user_id")
        for m in members:
            print(f"{m['email']},{m['real_name']},{m['display_name']},{m['user_id']}")
    else:
        print(f"\n{len(members)} members with emails in #{args.channel.lstrip('#')}:\n")
        for m in members:
            print(f"  {m['email']:40s} {m['real_name']}")
        print(f"\nEmails only (copy-paste friendly):\n")
        print(", ".join(m["email"] for m in members))


if __name__ == "__main__":
    main()

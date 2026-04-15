#!/usr/bin/env python3
"""
Add GitHub collaborators as admins to team repos and notify them in Slack.

Usage:
    # Add one user to a team
    python scripts/github/add_team_admins.py --repo 09-barack-obama --users smoha150-eng

    # Add multiple users
    python scripts/github/add_team_admins.py --repo 13-bubble-guppies --users sanguy06 hliu325 nkfelic1

    # Dry run
    python scripts/github/add_team_admins.py --repo 09-barack-obama --users smoha150-eng --dry-run

    # Skip Slack message
    python scripts/github/add_team_admins.py --repo 09-barack-obama --users smoha150-eng --no-slack
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

from github import Github, GithubException

ORG_NAME = "2026-ASU-WiCS-Opportunity-Hack"
SLACK_CHANNEL_PREFIX = "team-"


def add_admins(org, repo_name, usernames, dry_run=False):
    """Add GitHub users as admin collaborators to a repo."""
    try:
        repo = org.get_repo(repo_name)
    except GithubException as e:
        print(f"  [GitHub] ERROR: repo '{repo_name}' not found: {e.data.get('message', e)}")
        return False

    print(f"  [GitHub] Found repo: {repo.html_url}")

    all_ok = True
    for username in usernames:
        if dry_run:
            print(f"  [GitHub] Would add '{username}' as admin")
            continue
        try:
            repo.add_to_collaborators(username, permission="admin")
            print(f"  [GitHub] Added '{username}' as admin")
        except GithubException as e:
            print(f"  [GitHub] ERROR adding '{username}': {e.data.get('message', e)}")
            all_ok = False

    return all_ok


def send_slack_notification(repo_name, usernames, dry_run=False):
    """Send a Slack message to the team channel about the new admin(s)."""
    from common.utils.slack import send_slack

    channel_name = f"{SLACK_CHANNEL_PREFIX}{repo_name}"
    user_list = ", ".join(f"`{u}`" for u in usernames)
    repo_url = f"https://github.com/{ORG_NAME}/{repo_name}"

    message = (
        f"👋 GitHub admin access has been granted to {user_list} for <{repo_url}|{repo_name}>.\n\n"
        f"You should receive a GitHub invitation email — please accept it to get access. "
        f"You can also check <https://github.com/{ORG_NAME}/{repo_name}/invitations|your pending invitations>.\n\n"
        f"Once accepted, you'll have full admin access to the repo. Happy hacking! 🚀"
    )

    if dry_run:
        print(f"  [Slack] Would post to #{channel_name}:")
        print(f"          {message[:120]}...")
        return True

    try:
        send_slack(message=message, channel=channel_name)
        print(f"  [Slack] Posted notification to #{channel_name}")
        return True
    except Exception as e:
        print(f"  [Slack] ERROR posting to #{channel_name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Add GitHub admins to a team repo and notify via Slack")
    parser.add_argument('--repo', required=True, help="Repo name (e.g. 09-barack-obama)")
    parser.add_argument('--users', nargs='+', required=True, help="GitHub username(s) to add as admin")
    parser.add_argument('--dry-run', action='store_true', help="Preview without making changes")
    parser.add_argument('--no-slack', action='store_true', help="Skip Slack notification")
    args = parser.parse_args()

    print(f"=== Adding admins to {ORG_NAME}/{args.repo} ===")
    if args.dry_run:
        print("  [DRY RUN MODE]\n")

    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token and not args.dry_run:
        print("ERROR: GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    g = Github(github_token)
    try:
        org = g.get_organization(ORG_NAME)
    except GithubException as e:
        print(f"ERROR: Could not access org '{ORG_NAME}': {e.data.get('message', e)}")
        sys.exit(1)

    # Add GitHub admins
    add_admins(org, args.repo, args.users, dry_run=args.dry_run)

    # Send Slack notification
    if not args.no_slack:
        time.sleep(1)  # small delay to avoid rate limits
        send_slack_notification(args.repo, args.users, dry_run=args.dry_run)

    print("\n=== Done! ===")


if __name__ == "__main__":
    main()

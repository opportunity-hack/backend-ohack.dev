#!/usr/bin/env python3
"""
Send personalized emails via Resend to all hackathon team members.

Each person gets an email with their team name, GitHub repo, and Slack channel,
urging them to join Slack and declare a Problem Statement by 3pm.

Usage:
    python scripts/email_hackathon_teams.py --dry-run
    python scripts/email_hackathon_teams.py
"""

import argparse
import csv
import os
import sys
import time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import resend
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ORG_NAME = "2026-ASU-WiCS-Opportunity-Hack"
EVENT_ID = "2026_spring_wics_asu"
CSV_PATH = "/Users/gregv/Downloads/hackathon_teams (1).csv"

# Teams 27-29 not in CSV
EXTRA_TEAMS_MEMBERS = [
    {"team_number": "27", "team_name": "Three Byte", "repo_name": "27-three-byte",
     "members": [
         {"name": "Person Lastname", "email": "email@gmail.com"},         
     ]},
   
]


def load_recipients(csv_path):
    """Load all team members from CSV + extra teams. Returns list of dicts with email, name, team info."""
    teams = OrderedDict()

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            repo = row['repo_name'].strip()
            if repo not in teams:
                teams[repo] = {
                    'team_number': row['team_number'].strip(),
                    'team_name': row['team_name'].strip(),
                    'repo_name': repo,
                    'members': [],
                }
            name = row.get('member_name', '').strip()
            email = row['member_email'].strip().lower()
            if email:
                # Clean up names that are actually timestamps (e.g. "3/27/2026 18:59:40")
                if name and ('/' in name and ':' in name):
                    name = email.split('@')[0]
                teams[repo]['members'].append({'name': name, 'email': email})

    # Add extra teams
    for extra in EXTRA_TEAMS_MEMBERS:
        repo = extra['repo_name']
        if repo not in teams:
            teams[repo] = {
                'team_number': extra['team_number'],
                'team_name': extra['team_name'],
                'repo_name': repo,
                'members': [],
            }
        for m in extra['members']:
            if m['email']:
                teams[repo]['members'].append({'name': m['name'], 'email': m['email'].lower()})

    # Flatten to per-person records, deduplicate by email
    seen = set()
    recipients = []
    for repo, team in teams.items():
        for m in team['members']:
            if m['email'] not in seen:
                seen.add(m['email'])
                recipients.append({
                    'email': m['email'],
                    'name': m['name'] or m['email'].split('@')[0],
                    'team_name': team['team_name'],
                    'team_number': team['team_number'],
                    'repo_name': team['repo_name'],
                    'slack_channel': f"team-{team['repo_name']}",
                })
    return recipients


def build_html(recipient):
    """Build personalized HTML email for one recipient."""
    name = recipient['name']
    team_name = recipient['team_name']
    repo_name = recipient['repo_name']
    slack_channel = recipient['slack_channel']
    repo_url = f"https://github.com/{ORG_NAME}/{repo_name}"
    hackathon_url = f"https://www.ohack.dev/hack/{EVENT_ID}"
    devpost_url = "https://wics-ohack-sp26-hackathon.devpost.com/"
    slack_join_url = "https://slack.ohack.dev"
    slack_channel_url = f"https://opportunity-hack.slack.com/app_redirect?channel={slack_channel}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Action Required - Opportunity Hack</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9;">
    <div style="background-color: #ffffff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <div style="text-align: center; margin-bottom: 20px;">
            <img src="https://cdn.ohack.dev/ohack.dev/2023_hackathon_1.webp" alt="Opportunity Hack" style="width: 100%; max-width: 560px; height: auto; border-radius: 8px;">
        </div>

        <h1 style="color: #0088FE; font-size: 22px;">Hey {name}! Action Needed for Your Hackathon Team</h1>

        <p>You're registered as part of <strong>Team {team_name}</strong> in the <strong>2026 Spring WiCS x Opportunity Hack</strong> happening <strong>right now!</strong></p>

        <p style="color: #d32f2f; font-weight: bold; font-size: 16px;">&#9888; We need to hear from your team by 3:00 PM today (March 28) or your team will be marked as deleted.</p>

        <h2 style="color: #0088FE; font-size: 18px;">&#9745; Your 3 Action Items</h2>

        <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
            <tr style="background-color: #e3f2fd;">
                <td style="padding: 12px; border-radius: 4px;">
                    <strong>1. Join Slack</strong><br>
                    Go to <a href="{slack_join_url}" style="color: #0088FE;">slack.ohack.dev</a> and join the workspace.<br>
                    Then find your team channel: <a href="{slack_channel_url}" style="color: #0088FE;">#{slack_channel}</a>
                </td>
            </tr>
            <tr><td style="padding: 4px;"></td></tr>
            <tr style="background-color: #e8f5e9;">
                <td style="padding: 12px; border-radius: 4px;">
                    <strong>2. Your GitHub Repo</strong><br>
                    Your team repo is ready: <a href="{repo_url}" style="color: #0088FE;">{repo_url}</a><br>
                    Reply in your Slack channel with your GitHub username so we can add you as a collaborator.
                </td>
            </tr>
            <tr><td style="padding: 4px;"></td></tr>
            <tr style="background-color: #fff3e0;">
                <td style="padding: 12px; border-radius: 4px;">
                    <strong>3. Declare a Problem Statement</strong><br>
                    Visit <a href="{hackathon_url}" style="color: #0088FE;">ohack.dev/hack/{EVENT_ID}</a> and choose a nonprofit problem statement for your team.<br>
                    <strong style="color: #d32f2f;">Deadline: 3:00 PM today.</strong>
                </td>
            </tr>
        </table>

        <h2 style="color: #0088FE; font-size: 18px;">&#128279; Key Links</h2>
        <ul>
            <li><a href="{hackathon_url}" style="color: #0088FE;">Hackathon Details</a></li>
            <li><a href="{devpost_url}" style="color: #0088FE;">DevPost Submission</a></li>
            <li><a href="https://www.ohack.dev/about/judges#judging-criteria" style="color: #0088FE;">Judging Criteria</a></li>
            <li><a href="https://github.com/{ORG_NAME}" style="color: #0088FE;">GitHub Organization</a></li>
        </ul>

        <p>If you have any questions, message us on Slack or reply to this email. We're here to help!</p>

        <p>Happy hacking! &#128640;<br>
        <strong>The Opportunity Hack Team</strong></p>

    </div>

    <div style="text-align: center; margin-top: 20px; font-size: 12px; color: #999;">
        <p>Opportunity Hack — Code for Good | <a href="https://ohack.dev" style="color: #999;">ohack.dev</a></p>
    </div>
</body>
</html>"""


def send_email(recipient, dry_run=False):
    """Send one personalized email via Resend."""
    name = recipient['name']
    email = recipient['email']
    team = recipient['team_name']

    subject = f"Action Required: Team {team} — Declare Your Problem Statement by 3pm Today"

    if dry_run:
        print(f"  [DRY RUN] Would send to: {name} <{email}> (Team {team})")
        return True

    html = build_html(recipient)

    params = {
        "from": "Opportunity Hack <welcome@notifs.ohack.org>",
        "to": f"{name} <{email}>",
        "reply_to": "questions@ohack.org",
        "subject": subject,
        "html": html,
    }

    try:
        resend.Emails.send(params)
        print(f"  SENT to: {name} <{email}> (Team {team})")
        return True
    except Exception as e:
        print(f"  ERROR sending to {email}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Email all hackathon team members via Resend")
    parser.add_argument('--dry-run', action='store_true', help="Preview without sending")
    parser.add_argument('--csv', default=CSV_PATH, help="Path to teams CSV")
    args = parser.parse_args()

    # Setup Resend
    api_key = os.getenv("RESEND_WELCOME_EMAIL_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: RESEND_WELCOME_EMAIL_KEY not set")
        sys.exit(1)
    resend.api_key = api_key

    recipients = load_recipients(args.csv)
    print(f"Loaded {len(recipients)} unique recipients across all teams")

    if args.dry_run:
        print("=== DRY RUN ===\n")

    sent = 0
    failed = 0

    for r in recipients:
        if send_email(r, dry_run=args.dry_run):
            sent += 1
        else:
            failed += 1

        if not args.dry_run:
            time.sleep(0.2)  # rate limit

    print(f"\nDone. Sent: {sent}, Failed: {failed}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

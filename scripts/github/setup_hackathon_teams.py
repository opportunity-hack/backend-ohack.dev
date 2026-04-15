#!/usr/bin/env python3
"""
Setup hackathon teams: create GitHub repos, Slack channels, and invite members.

Reads a CSV with columns: team_number, team_name, repo_name, member_name, member_email, source
For each team:
  1. Creates a GitHub repo in the org with README + LICENSE
  2. Creates a Slack channel and invites members found in Slack
  3. Posts a welcome message in the Slack channel with the repo link

Usage:
    python scripts/github/setup_hackathon_teams.py --csv /path/to/teams.csv
    python scripts/github/setup_hackathon_teams.py --csv /path/to/teams.csv --dry-run
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import OrderedDict

# Add project root to path so we can import common utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

from github import Github, GithubException

# --- Configuration ---
ORG_NAME = "2026-ASU-WiCS-Opportunity-Hack"
HACKATHON_EVENT_ID = "2026_spring_wics_asu"
DEVPOST_URL = "https://wics-ohack-sp26-hackathon.devpost.com/"
SLACK_CHANNEL_PREFIX = "team-"

GITHUB_ADMINS = [
    "bmysoreshankar", "jotpowers", "nemathew", "pkakathkar",
    "vertex", "gregv", "mosesj1914", "ananay", "axeljonson",
    "MrPanda1", "sathyvs"
]

SLACK_ADMINS = [
    "UCQKX6LPR", "U035023T81Z", "UC31XTRT5",
    "UC2JW3T3K", "UPD90QV17", "UEP2U69AA"
]


def parse_csv(csv_path):
    """Parse CSV and group members by team."""
    teams = OrderedDict()

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            repo_name = row['repo_name'].strip()
            if repo_name not in teams:
                teams[repo_name] = {
                    'team_number': row['team_number'].strip(),
                    'team_name': row['team_name'].strip(),
                    'repo_name': repo_name,
                    'members': []
                }
            teams[repo_name]['members'].append({
                'name': row.get('member_name', '').strip(),
                'email': row['member_email'].strip(),
                'source': row.get('source', '').strip()
            })

    return teams


def build_readme(team_name, hackathon_event_id, members, devpost_url, org_name, repo_name):
    """Build README content for a team repo."""
    member_lines = ""
    for m in members:
        name = m['name'] if m['name'] else m['email']
        member_lines += f"- {name}\n"

    return f"""# {hackathon_event_id} Hackathon Project

## Quick Links
- [Hackathon Details](https://www.ohack.dev/hack/{hackathon_event_id})
- [DevPost Submission]({devpost_url})
- [Team Slack Channel](https://opportunity-hack.slack.com/app_redirect?channel={SLACK_CHANNEL_PREFIX}{repo_name})

## Team "{team_name}"
{member_lines}
## Project Overview
Brief description of your project and its goals.

## Tech Stack
- Frontend:
- Backend:
- Database:
- APIs:
<!-- Add/modify as needed -->


## Getting Started
Instructions on how to set up and run your project locally.

```bash
# Example commands
git clone https://github.com/{org_name}/{repo_name}.git
cd {repo_name}
# Add your setup commands here
```


## Checklist for the final submission
### 0/Judging Criteria
- [ ] Review the [judging criteria](https://www.ohack.dev/about/judges#judging-criteria) to understand how your project will be evaluated

### 1/DevPost
- [ ] Submit a [DevPost project to this DevPost page for our hackathon]({devpost_url}) - see our [YouTube Walkthrough](https://youtu.be/rsAAd7LXMDE) or a more general one from DevPost [here](https://www.youtube.com/watch?v=vCa7QFFthfU)
- [ ] Your DevPost final submission demo video should be 4 minutes or less
- [ ] Link your team to your DevPost project on ohack.dev in [your team dashboard](https://www.ohack.dev/hack/{hackathon_event_id}/manageteam)
- [ ] Link your GitHub repo to your DevPost project on the DevPost submission form under "Try it out" links

### 2/GitHub
- [ ] Add everyone on your team to your GitHub repo [YouTube Walkthrough](https://youtu.be/kHs0jOewVKI)
- [ ] Make sure your repo is public
- [ ] Make sure your repo has a MIT License
- [ ] Make sure your repo has a detailed README.md (see below for details)


# What should your final README look like?
Your readme should be a one-stop-shop for the judges to understand your project. It should include:
- Team name
- Team members
- Slack channel
- Problem statement
- Tech stack
- Link to your working project on the web so judges can try it out
- Link to your DevPost project
- Link to your final demo video
- Instructions on how to run your project
- Any other relevant links (e.g. Figma, GitHub repos for any open source libraries you used, etc.)


You'll use this repo as your resume in the future, so make it shine! 🌟

# Examples
Examples of stellar readmes:
- ✨ [2019 Team 3](https://github.com/2019-Arizona-Opportunity-Hack/Team-3)
- ✨ [2019 Team 6](https://github.com/2019-Arizona-Opportunity-Hack/Team-6)
- ✨ [2020 Team 2](https://github.com/2020-opportunity-hack/Team-02)
- ✨ [2020 Team 4](https://github.com/2020-opportunity-hack/Team-04)
- ✨ [2020 Team 8](https://github.com/2020-opportunity-hack/Team-08)
- ✨ [2020 Team 12](https://github.com/2020-opportunity-hack/Team-12)

Examples of winning DevPost submissions:
- [1st place 2024](https://devpost.com/software/nature-s-edge-wildlife-and-reptile-rescue)
- [2nd place 2024](https://devpost.com/software/team13-kidcoda-steam)
- [1st place 2023](https://devpost.com/software/preservation-partners-search-engine)
- [1st place 2019](https://devpost.com/software/zuri-s-dashboard)
- [1st place 2018](https://devpost.com/software/matthews-crossing-data-manager-oj4ica)
"""


def create_repo(g, org, team, dry_run=False):
    """Create GitHub repo for a team. Returns the repo object or None."""
    repo_name = team['repo_name']
    team_name = team['team_name']

    # Check if repo exists
    try:
        repo = org.get_repo(repo_name)
        print(f"  [GitHub] Repo '{repo_name}' already exists, using it")
        return repo
    except GithubException:
        pass

    if dry_run:
        print(f"  [GitHub] Would create repo '{repo_name}'")
        return None

    try:
        repo = org.create_repo(repo_name, private=False)
        print(f"  [GitHub] Created repo '{repo_name}'")
    except GithubException as e:
        print(f"  [GitHub] ERROR creating repo '{repo_name}': {e.data.get('message', e)}")
        return None

    # Add admins
    for admin in GITHUB_ADMINS:
        try:
            repo.add_to_collaborators(admin, permission="admin")
        except GithubException as e:
            print(f"  [GitHub] Warning: could not add admin '{admin}': {e.data.get('message', e)}")

    # Add LICENSE
    try:
        repo.create_file("LICENSE", "Add MIT License", "MIT License")
    except GithubException:
        print(f"  [GitHub] Warning: LICENSE already exists or could not be created")

    # Add README
    readme_content = build_readme(
        team_name=team_name,
        hackathon_event_id=HACKATHON_EVENT_ID,
        members=team['members'],
        devpost_url=DEVPOST_URL,
        org_name=ORG_NAME,
        repo_name=repo_name
    )
    try:
        repo.create_file("README.md", "Add README.md", readme_content)
    except GithubException:
        print(f"  [GitHub] Warning: README.md already exists or could not be created")

    # Set description
    repo.edit(description=f"{HACKATHON_EVENT_ID} Hackathon - Team {team_name}")

    return repo


def invite_github_collaborators(repo, members, dry_run=False):
    """Invite team members as GitHub collaborators by username.

    Note: PyGithub add_to_collaborators does not support email-based invites.
    Teams will share their GitHub usernames and be added later.
    This function is kept as a placeholder for future username-based invites.
    """
    pass


def setup_slack_channel(team, dry_run=False):
    """Create Slack channel and invite members. Returns channel_id or None."""
    # Import here so dry-run works without Slack credentials
    from common.utils.slack import (
        create_slack_channel,
        get_slack_user_by_email,
        invite_user_to_channel_id,
        add_bot_to_channel,
        send_slack,
    )

    channel_name = f"{SLACK_CHANNEL_PREFIX}{team['repo_name']}"

    if dry_run:
        print(f"  [Slack] Would create channel '#{channel_name}'")
        for m in team['members']:
            print(f"  [Slack] Would look up and invite '{m['email']}'")
        print(f"  [Slack] Would post welcome message")
        return None

    # Create channel
    channel_id = create_slack_channel(channel_name)
    if channel_id is None:
        print(f"  [Slack] ERROR: could not create channel '{channel_name}'")
        return None
    print(f"  [Slack] Channel '#{channel_name}' ready (ID: {channel_id})")

    # Bot joins the channel
    add_bot_to_channel(channel_id)

    # Invite Slack admins
    for admin_id in SLACK_ADMINS:
        invite_user_to_channel_id(user_id=admin_id, channel_id=channel_id)
        print(f"  [Slack] Invited admin {admin_id} to #{channel_name}")

    # Invite members
    slack_members_found = []
    for member in team['members']:
        email = member['email']
        slack_user = get_slack_user_by_email(email)
        if slack_user:
            slack_user_id = slack_user['id']
            slack_display = slack_user.get('real_name', slack_user.get('name', email))
            invite_user_to_channel_id(user_id=slack_user_id, channel_id=channel_id)
            slack_members_found.append(slack_display)
            print(f"  [Slack] Invited '{email}' ({slack_display}) to #{channel_name}")
        else:
            print(f"  [Slack] User not found in Slack for '{email}' — they may need to join the workspace first")

    # Post welcome message
    repo_url = f"https://github.com/{ORG_NAME}/{team['repo_name']}"
    member_list = "\n".join(
        f"• {m['name'] or m['email']}" for m in team['members']
    )
    message = (
        f"🎉 *Welcome to Team {team['team_name']}!*\n\n"
        f"Your GitHub repo has been created: <{repo_url}>\n\n"
        f"*Team Members:*\n{member_list}\n\n"
        f"📋 *Next steps:*\n"
        f"• *Reply in this channel with your GitHub username* so we can add you as a collaborator to the repo\n"
        f"• Clone the repo and start coding!\n"
        f"• Submit to DevPost: <{DEVPOST_URL}>\n"
        f"• Hackathon details: <https://www.ohack.dev/hack/{HACKATHON_EVENT_ID}>\n\n"
        f"Good luck! 🚀"
    )
    try:
        send_slack(message=message, channel=channel_id)
        print(f"  [Slack] Posted welcome message to #{channel_name}")
    except Exception as e:
        print(f"  [Slack] Warning: could not post message: {e}")

    return channel_id


# --- Processed tracking ---
PROCESSED_FILE = os.path.join(os.path.dirname(__file__), "processed_teams.json")


def load_processed():
    """Load set of already-processed repo names."""
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, 'r') as f:
            return set(json.load(f))
    return set()


def mark_processed(repo_name):
    """Add a repo_name to the processed set and save."""
    processed = load_processed()
    processed.add(repo_name)
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(sorted(processed), f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Setup hackathon teams: GitHub repos + Slack channels")
    parser.add_argument('--csv', required=True, help="Path to the hackathon teams CSV file")
    parser.add_argument('--dry-run', action='store_true', help="Preview actions without making changes")
    parser.add_argument('--team', type=int, help="Process only this team number")
    parser.add_argument('--skip-processed', action='store_true', default=True,
                        help="Skip teams already marked as processed (default: True)")
    parser.add_argument('--no-skip-processed', action='store_false', dest='skip_processed',
                        help="Re-process all teams even if already marked as processed")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"ERROR: CSV file not found: {args.csv}")
        sys.exit(1)

    teams = parse_csv(args.csv)
    processed = load_processed()
    print(f"Loaded {len(teams)} teams from CSV ({len(processed)} already processed)\n")

    if args.dry_run:
        print("=== DRY RUN MODE — no changes will be made ===\n")

    # Filter to specific team if requested
    if args.team is not None:
        teams = {k: v for k, v in teams.items() if int(v['team_number']) == args.team}
        if not teams:
            print(f"ERROR: Team number {args.team} not found in CSV")
            sys.exit(1)

    # Initialize GitHub
    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token and not args.dry_run:
        print("ERROR: GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    g = None
    org = None
    if github_token:
        g = Github(github_token)
        try:
            org = g.get_organization(ORG_NAME)
        except GithubException as e:
            print(f"ERROR: Could not access org '{ORG_NAME}': {e.data.get('message', e)}")
            sys.exit(1)

    for repo_name, team in teams.items():
        if args.skip_processed and repo_name in processed and not args.dry_run:
            print(f"--- Team {team['team_number']}: {team['team_name']} ({repo_name}) --- SKIPPED (already processed)")
            continue

        print(f"--- Team {team['team_number']}: {team['team_name']} ({repo_name}) ---")

        success = True

        # 1. Create GitHub repo
        repo = create_repo(g, org, team, dry_run=args.dry_run)
        if repo is None and not args.dry_run:
            success = False

        # 2. Invite GitHub collaborators
        invite_github_collaborators(repo, team['members'], dry_run=args.dry_run)

        # 3. Setup Slack channel + invites + welcome message
        channel_id = setup_slack_channel(team, dry_run=args.dry_run)
        if channel_id is None and not args.dry_run:
            success = False

        # Mark as processed if everything succeeded
        if success and not args.dry_run:
            mark_processed(repo_name)
            print(f"  [OK] Team {team['team_number']} marked as processed")

        print()

        # Small delay to avoid rate limits
        if not args.dry_run:
            time.sleep(1)

    print("=== Done! ===")


if __name__ == "__main__":
    main()

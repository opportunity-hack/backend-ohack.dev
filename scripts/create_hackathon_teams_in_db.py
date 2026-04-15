#!/usr/bin/env python3
"""
Create team documents in Firestore for the 2026 Spring WiCS x Opportunity Hack.

Reads team data from the CSV (teams 1-26) plus hardcoded teams 27-29,
creates Firestore team documents, and links them to the hackathon.

Usage:
    python scripts/create_hackathon_teams_in_db.py --dry-run
    python scripts/create_hackathon_teams_in_db.py
"""

import argparse
import csv
import os
import sys
from collections import OrderedDict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db, get_hackathon_by_event_id, get_team_by_name, add_team_to_hackathon
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ORG_NAME = "2026-ASU-WiCS-Opportunity-Hack"
EVENT_ID = "2026_spring_wics_asu"
CSV_PATH = "/Users/gregv/Downloads/hackathon_teams (1).csv"

# Teams 27-29 not in the CSV
EXTRA_TEAMS = [
    {"team_number": "27", "team_name": "Three Byte", "repo_name": "27-three-byte"},
    {"team_number": "28", "team_name": "shawarmaalgo", "repo_name": "28-shawarmaalgo"},
    {"team_number": "29", "team_name": "GitGood", "repo_name": "29-gitgood"},
]


def load_teams_from_csv(csv_path):
    """Parse CSV and return one entry per unique team."""
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
                }
    return list(teams.values())


def main():
    parser = argparse.ArgumentParser(description="Create hackathon teams in Firestore")
    parser.add_argument('--dry-run', action='store_true', help="Preview without writing")
    parser.add_argument('--csv', default=CSV_PATH, help="Path to teams CSV")
    args = parser.parse_args()

    # Load all teams
    teams = load_teams_from_csv(args.csv)
    for extra in EXTRA_TEAMS:
        teams.append(extra)

    print(f"Loaded {len(teams)} teams")
    if args.dry_run:
        print("=== DRY RUN ===\n")

    # Get hackathon doc ID
    db = get_db()
    hackathon = get_hackathon_by_event_id(EVENT_ID)
    if not hackathon:
        print(f"ERROR: Hackathon {EVENT_ID} not found")
        sys.exit(1)

    hackathon_doc_id = hackathon.get("id") or hackathon.get("doc_id")
    print(f"Hackathon doc ID: {hackathon_doc_id}\n")

    created = 0
    skipped = 0

    for team in teams:
        team_name = team['team_name']
        repo_name = team['repo_name']
        team_number = int(team['team_number'])
        slack_channel = f"team-{repo_name}"
        repo_url = f"https://github.com/{ORG_NAME}/{repo_name}"

        # Check if team already exists
        existing = get_team_by_name(team_name)
        if existing:
            print(f"  SKIP Team {team_number}: {team_name} (already exists)")
            skipped += 1
            continue

        team_doc = {
            "name": team_name,
            "active": True,
            "slack_channel": slack_channel,
            "team_number": team_number,
            "problem_statements": [],
            "users": [],
            "hackathon_event_id": EVENT_ID,
            "status": "IN_REVIEW",
            "created": datetime.now().isoformat(),
            "github_links": [{"link": repo_url, "name": repo_name}],
        }

        if args.dry_run:
            print(f"  WOULD CREATE Team {team_number}: {team_name} | slack: #{slack_channel} | github: {repo_url}")
            created += 1
            continue

        # Create team document
        _, doc_ref = db.collection("teams").add(team_doc)
        team_doc_id = doc_ref.id
        print(f"  CREATED Team {team_number}: {team_name} (doc: {team_doc_id})")

        # Link to hackathon
        try:
            add_team_to_hackathon(team_doc_id, hackathon_doc_id)
            print(f"    Linked to hackathon {EVENT_ID}")
        except Exception as e:
            print(f"    WARNING: Could not link to hackathon: {e}")

        created += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

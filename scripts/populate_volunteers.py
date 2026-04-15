#!/usr/bin/env python3
"""
Populate the volunteers collection in Firestore with hackathon participants.

Reads team member data from the consolidated CSV, checks for duplicates by email,
and creates volunteer records for anyone not already in the collection.

Usage:
    python scripts/populate_volunteers.py --dry-run
    python scripts/populate_volunteers.py
    python scripts/populate_volunteers.py --csv /path/to/other.csv --event-id some_event
"""

import argparse
import csv
import os
import sys
import uuid
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_CSV = "/Users/gregv/Downloads/hackathon_teams_consolidated.csv"
DEFAULT_EVENT_ID = "2026_spring_wics_asu"
VOLUNTEER_TYPE = "hacker"
BATCH_LIMIT = 500


def get_timestamp():
    """ISO timestamp in Arizona timezone, matching volunteers_service.py."""
    return datetime.now(pytz.timezone('US/Arizona')).isoformat()


def load_members(csv_path):
    """Load unique members from CSV. Deduplicates by lowercase email."""
    seen = set()
    members = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row['Member Email'].strip().lower()
            if not email or email == '(no email)':
                continue
            if email in seen:
                continue
            seen.add(email)

            name = row.get('Member Name', '').strip()
            if name == '(unknown)':
                name = ''

            # Split name into first/last
            parts = name.split(None, 1) if name else []
            first_name = parts[0] if parts else email.split('@')[0]
            last_name = parts[1] if len(parts) > 1 else ''

            members.append({
                'email': email,
                'name': name,
                'firstName': first_name,
                'lastName': last_name,
                'team_name': row.get('Team Name', '').strip(),
                'team_number': row.get('Team #', '').strip(),
            })

    return members


def get_existing_emails(db, event_id):
    """Fetch all emails already in volunteers collection for this event + type."""
    existing = set()
    docs = db.collection('volunteers') \
        .where('event_id', '==', event_id) \
        .where('volunteer_type', '==', VOLUNTEER_TYPE) \
        .stream()

    for doc in docs:
        data = doc.to_dict()
        email = data.get('email', '').lower()
        if email:
            existing.add(email)

    return existing


def build_volunteer_doc(member, event_id):
    """Build a volunteer document matching the schema in volunteers_service.py."""
    now = get_timestamp()
    return {
        'id': str(uuid.uuid4()),
        'user_id': '',  # No PropelAuth user yet
        'event_id': event_id,
        'email': member['email'],
        'name': member['name'],
        'firstName': member['firstName'],
        'lastName': member['lastName'],
        'volunteer_type': VOLUNTEER_TYPE,
        'type': 'hackers',
        'status': 'active',
        'isSelected': False,
        'timestamp': now,
        'created_by': 'script:populate_volunteers',
        'created_timestamp': now,
        'updated_by': 'script:populate_volunteers',
        'updated_timestamp': now,
    }


def main():
    parser = argparse.ArgumentParser(description="Populate volunteers collection from hackathon team CSV")
    parser.add_argument('--dry-run', action='store_true', help="Preview without writing")
    parser.add_argument('--csv', default=DEFAULT_CSV, help="Path to consolidated teams CSV")
    parser.add_argument('--event-id', default=DEFAULT_EVENT_ID, help="Hackathon event ID")
    args = parser.parse_args()

    members = load_members(args.csv)
    print(f"Loaded {len(members)} unique members from CSV")

    if args.dry_run:
        print("=== DRY RUN ===\n")

    db = get_db()

    # Fetch existing volunteers to check for duplicates
    print(f"Checking existing volunteers for event={args.event_id}, type={VOLUNTEER_TYPE}...")
    existing_emails = get_existing_emails(db, args.event_id)
    print(f"Found {len(existing_emails)} existing volunteer records\n")

    to_create = []
    skipped = 0

    for m in members:
        if m['email'] in existing_emails:
            print(f"  SKIP (exists): {m['name'] or m['email']} <{m['email']}>")
            skipped += 1
        else:
            to_create.append(m)
            if args.dry_run:
                print(f"  WOULD CREATE: {m['name'] or m['email']} <{m['email']}> (Team {m['team_number']}: {m['team_name']})")

    print(f"\nSummary: {len(to_create)} to create, {skipped} already exist")

    if args.dry_run or not to_create:
        return

    # Batch write
    created = 0
    for batch_start in range(0, len(to_create), BATCH_LIMIT):
        batch = db.batch()
        batch_slice = to_create[batch_start:batch_start + BATCH_LIMIT]

        for m in batch_slice:
            doc = build_volunteer_doc(m, args.event_id)
            ref = db.collection('volunteers').document(doc['id'])
            batch.set(ref, doc)

        batch.commit()
        created += len(batch_slice)
        print(f"  Batch committed: {created}/{len(to_create)}")

    print(f"\nDone. Created {created} volunteer records.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

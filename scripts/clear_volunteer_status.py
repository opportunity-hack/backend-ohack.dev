#!/usr/bin/env python3
"""
Update the status field for volunteers in Firestore by event and type.

Usage examples:
    # Preview mentors that would be affected (dry run)
    python clear_volunteer_status.py 2026_spring_wics_asu mentor --status inactive --dry-run

    # Set mentors to inactive with confirmation prompt
    python clear_volunteer_status.py 2026_spring_wics_asu mentor --status inactive

    # Skip confirmation
    python clear_volunteer_status.py 2026_spring_wics_asu mentor --status inactive --yes
"""

import sys
import os
import argparse
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import from project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.utils.firebase import get_db
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BATCH_LIMIT = 500


def query_volunteers(db, event_id, volunteer_type):
    """Query volunteers by event_id and volunteer_type. Returns list of (ref, doc_dict)."""
    query = (
        db.collection('volunteers')
        .where('event_id', '==', event_id)
        .where('volunteer_type', '==', volunteer_type)
    )
    results = []
    for doc in query.stream():
        results.append((doc.reference, doc.to_dict()))
    return results


def display_volunteers(volunteers):
    """Print a table of volunteers with their current status."""
    print(f"\n{'#':<4} {'Name':<30} {'Email':<35} {'Status':<15}")
    print('-' * 84)
    for i, (_, data) in enumerate(volunteers, 1):
        name = data.get('name') or f"{data.get('firstName', '')} {data.get('lastName', '')}".strip() or '(unknown)'
        email = data.get('email', '(unknown)')
        status = data.get('status', '(not set)')
        print(f"{i:<4} {name:<30} {email:<35} {status:<15}")
    print(f"\nTotal: {len(volunteers)} volunteer(s)")


def update_statuses(db, volunteers, new_status):
    """Batch update the status field for all volunteers."""
    updated = 0
    timestamp = datetime.now().isoformat()

    for batch_start in range(0, len(volunteers), BATCH_LIMIT):
        batch = db.batch()
        batch_slice = volunteers[batch_start:batch_start + BATCH_LIMIT]
        for ref, _ in batch_slice:
            batch.update(ref, {
                'status': new_status,
                'updated_timestamp': timestamp,
            })
        batch.commit()
        updated += len(batch_slice)
        logger.info(f"Updated batch: {updated}/{len(volunteers)}")

    return updated


def main():
    parser = argparse.ArgumentParser(
        description='Update volunteer status in Firestore by event and type.',
        epilog='Example: python clear_volunteer_status.py 2026_spring_wics_asu mentor --status inactive'
    )
    parser.add_argument('event_id', help='Event ID (e.g. 2026_spring_wics_asu)')
    parser.add_argument('volunteer_type', help='Volunteer type (e.g. mentor, judge, sponsor, volunteer, hacker)')
    parser.add_argument('--status', required=True, help='New status value (e.g. inactive, completed, "")')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')

    args = parser.parse_args()

    db = get_db()
    logger.info(f"Querying volunteers: event_id={args.event_id}, volunteer_type={args.volunteer_type}")

    volunteers = query_volunteers(db, args.event_id, args.volunteer_type)

    if not volunteers:
        print(f"\nNo volunteers found for event_id={args.event_id}, volunteer_type={args.volunteer_type}")
        return

    display_volunteers(volunteers)

    if args.dry_run:
        print(f"\n[DRY RUN] Would set status to '{args.status}' for {len(volunteers)} volunteer(s). No changes made.")
        return

    print(f"\nWill set status to '{args.status}' for {len(volunteers)} volunteer(s).")

    if not args.yes:
        response = input("Proceed? [y/N]: ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return

    count = update_statuses(db, volunteers, args.status)
    print(f"\nDone. Updated {count} volunteer(s) to status='{args.status}'.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

#!/usr/bin/env python3
"""
Backfill script to populate praise_receiver_ohack_id and praise_sender_ohack_id
in the praises collection.

This script queries the praises collection, finds records missing these IDs,
and looks up the corresponding user records using their Slack IDs.
"""

import sys
import os

from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import from project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.utils.firebase import get_db
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SLACK_PREFIX = "oauth2|slack|T1Q7936BH-"


def fetch_user_by_slack_id(db, slack_user_id, cache=None):
    """
    Fetch a user from the users collection by their Slack user ID.
    Uses an optional cache to avoid repeated database queries.

    Args:
        db: Firestore database client
        slack_user_id (str): Slack user ID (without prefix)
        cache (dict, optional): Dictionary to cache user lookups

    Returns:
        dict: User document with 'id' field, or None if not found
    """
    # Add prefix if not present
    if not slack_user_id.startswith(SLACK_PREFIX):
        full_slack_id = f"{SLACK_PREFIX}{slack_user_id}"
    else:
        full_slack_id = slack_user_id

    # Check cache first
    if cache is not None and full_slack_id in cache:
        logger.debug(f"Cache hit for slack_id: {full_slack_id}")
        return cache[full_slack_id]

    logger.debug(f"Cache miss - looking up user with slack_id: {full_slack_id}")

    # Query users collection
    docs = db.collection('users').where("user_id", "==", full_slack_id).stream()

    for doc in docs:
        user_dict = doc.to_dict()
        user_dict['id'] = doc.id
        logger.debug(f"Found user: {user_dict.get('name', 'Unknown')} (ID: {doc.id})")

        # Store in cache if provided
        if cache is not None:
            cache[full_slack_id] = user_dict

        return user_dict

    logger.warning(f"User not found for slack_id: {full_slack_id}")

    # Cache the negative result to avoid repeated lookups
    if cache is not None:
        cache[full_slack_id] = None

    return None


def backfill_praise_columns():
    """
    Main function to backfill praise_receiver_ohack_id and praise_sender_ohack_id
    for all praises missing these fields.

    Optimizations:
    - Uses in-memory cache to avoid duplicate user lookups
    - Batches database writes (up to 500 operations per batch)
    - Two-pass approach: collect data, then batch update
    """
    logger.info("Starting praise column backfill script")

    # Get database connection
    db = get_db()

    # Initialize user lookup cache
    user_cache = {}

    # Query all praises
    logger.info("Querying praises collection...")
    praises_ref = db.collection('praises')
    all_praises = list(praises_ref.stream())

    logger.info(f"Found {len(all_praises)} total praises")

    # Counters for statistics
    total_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0
    cache_hit_count = 0
    cache_miss_count = 0

    # Collect updates to batch
    updates_to_apply = []

    # First pass: Analyze all praises and prepare updates
    logger.info("Pass 1: Analyzing praises and looking up users...")
    for praise_doc in all_praises:
        total_count += 1
        praise_data = praise_doc.to_dict()
        praise_id = praise_doc.id

        if total_count % 50 == 0:
            logger.info(f"Processed {total_count}/{len(all_praises)} praises...")

        # Check if both fields are already populated
        has_receiver_id = 'praise_receiver_ohack_id' in praise_data and praise_data['praise_receiver_ohack_id']
        has_sender_id = 'praise_sender_ohack_id' in praise_data and praise_data['praise_sender_ohack_id']

        if has_receiver_id and has_sender_id:
            skipped_count += 1
            continue

        # Prepare update data
        update_data = {}

        # Look up receiver if needed
        if not has_receiver_id:
            praise_receiver = praise_data.get('praise_receiver')
            if praise_receiver:
                receiver_user = fetch_user_by_slack_id(db, praise_receiver, cache=user_cache)
                if receiver_user:
                    update_data['praise_receiver_ohack_id'] = receiver_user['id']
                else:
                    logger.warning(f"  Praise {praise_id}: Could not find receiver user for slack_id: {praise_receiver}")
                    error_count += 1

        # Look up sender if needed
        if not has_sender_id:
            praise_sender = praise_data.get('praise_sender')
            if praise_sender:
                sender_user = fetch_user_by_slack_id(db, praise_sender, cache=user_cache)
                if sender_user:
                    update_data['praise_sender_ohack_id'] = sender_user['id']
                else:
                    logger.warning(f"  Praise {praise_id}: Could not find sender user for slack_id: {praise_sender}")
                    error_count += 1

        # Queue update if we have data to update
        if update_data:
            updates_to_apply.append({
                'reference': praise_doc.reference,
                'data': update_data,
                'praise_id': praise_id
            })

    logger.info(f"Pass 1 complete: {len(updates_to_apply)} praises need updating")
    logger.info(f"User cache contains {len(user_cache)} unique users")

    # Second pass: Apply updates in batches
    if updates_to_apply:
        logger.info("Pass 2: Applying updates in batches...")
        batch_size = 500  # Firestore limit
        num_batches = (len(updates_to_apply) + batch_size - 1) // batch_size

        for batch_num in range(num_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(updates_to_apply))
            batch_updates = updates_to_apply[start_idx:end_idx]

            logger.info(f"Processing batch {batch_num + 1}/{num_batches} ({len(batch_updates)} updates)...")

            # Create Firestore batch
            batch = db.batch()

            for update in batch_updates:
                batch.update(update['reference'], update['data'])

            # Commit the batch
            try:
                batch.commit()
                updated_count += len(batch_updates)
                logger.info(f"  Successfully committed batch {batch_num + 1}")
            except Exception as e:
                logger.error(f"  Error committing batch {batch_num + 1}: {str(e)}")
                error_count += len(batch_updates)

    # Print summary
    logger.info("=" * 80)
    logger.info("Backfill complete!")
    logger.info(f"Total praises processed: {total_count}")
    logger.info(f"Successfully updated: {updated_count}")
    logger.info(f"Skipped (already complete): {skipped_count}")
    logger.info(f"Errors encountered: {error_count}")
    logger.info(f"Unique users cached: {len(user_cache)}")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        backfill_praise_columns()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

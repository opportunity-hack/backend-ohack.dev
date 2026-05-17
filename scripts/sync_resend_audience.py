#!/usr/bin/env python3
"""
Sync emails from Firestore to a Resend audience.

Pulls emails from one or more sources and upserts them as contacts into a
Resend audience. Re-runnable: skips contacts that already exist in the audience.

Sources:
  profiles    -> users collection (registered ohack.dev profiles)
  volunteers  -> volunteers collection, all volunteer_type values
  mentors     -> volunteers where volunteer_type=mentor
  judges      -> volunteers where volunteer_type=judge
  sponsors    -> volunteers where volunteer_type=sponsor
  helpers     -> volunteers where volunteer_type=volunteer
  leads       -> leads collection (ohack.dev signup form)
  all         -> profiles + volunteers + leads (deduped)

Usage:
  # Dry-run: see what would be synced
  python scripts/sync_resend_audience.py --source all --audience "Everyone"

  # Apply: actually create the audience and add contacts
  python scripts/sync_resend_audience.py --source all --audience "Everyone" --apply

  # Mentors only, restricted to one event, only those marked isSelected
  python scripts/sync_resend_audience.py --source mentors \
      --audience "Mentors - 2026 Spring WiCS" \
      --event-id 2026_spring_wics_asu --selected-only --apply

  # Leads
  python scripts/sync_resend_audience.py --source leads --audience "Leads" --apply

Env vars:
  RESEND_API_KEY            preferred
  RESEND_WELCOME_EMAIL_KEY  fallback (used elsewhere in this repo)
"""

import argparse
import logging
import os
import re
import sys
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import resend

from common.utils.firebase import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

VOLUNTEER_TYPE_BY_SOURCE = {
    'mentors': 'mentor',
    'judges': 'judge',
    'sponsors': 'sponsor',
    'helpers': 'volunteer',
}


def _norm_email(raw) -> Optional[str]:
    if not raw:
        return None
    e = str(raw).strip().lower()
    return e if EMAIL_RE.match(e) else None


def _split_name(full: str) -> Tuple[str, str]:
    if not full:
        return '', ''
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], ' '.join(parts[1:])


def _add(contacts: Dict[str, dict], email: Optional[str], first: str, last: str, src: str):
    """Insert or upgrade a contact entry. Prefers entries with a real name."""
    if not email:
        return
    first = (first or '').strip()
    last = (last or '').strip()
    existing = contacts.get(email)
    if existing is None:
        contacts[email] = {'email': email, 'first_name': first, 'last_name': last, 'source': src}
        return
    # Keep richer name if we previously had blanks
    if not existing['first_name'] and first:
        existing['first_name'] = first
    if not existing['last_name'] and last:
        existing['last_name'] = last


def load_profiles() -> Dict[str, dict]:
    db = get_db()
    out: Dict[str, dict] = {}
    for doc in db.collection('users').stream():
        d = doc.to_dict() or {}
        email = _norm_email(d.get('email_address'))
        if not email:
            continue
        first, last = _split_name(d.get('name', '') or d.get('nickname', ''))
        _add(out, email, first, last, 'profiles')
    logger.info("profiles: %d unique emails", len(out))
    return out


def load_volunteers(volunteer_type: Optional[str], event_id: Optional[str],
                    selected_only: bool) -> Dict[str, dict]:
    db = get_db()
    query = db.collection('volunteers')
    if volunteer_type:
        query = query.where('volunteer_type', '==', volunteer_type)
    if event_id:
        query = query.where('event_id', '==', event_id)
    # Don't add isSelected to the firestore query (would require a composite
    # index). Filter in Python.
    out: Dict[str, dict] = {}
    for doc in query.stream():
        d = doc.to_dict() or {}
        if selected_only and not d.get('isSelected'):
            continue
        email = _norm_email(d.get('email'))
        if not email:
            continue
        first = d.get('first_name', '') or ''
        last = d.get('last_name', '') or ''
        if not first and not last:
            first, last = _split_name(d.get('name', ''))
        _add(out, email, first, last, f"volunteers:{volunteer_type or 'all'}")
    label = volunteer_type or 'all'
    logger.info("volunteers[%s] event=%s selected_only=%s: %d unique emails",
                label, event_id, selected_only, len(out))
    return out


def load_leads() -> Dict[str, dict]:
    db = get_db()
    out: Dict[str, dict] = {}
    for doc in db.collection('leads').stream():
        d = doc.to_dict() or {}
        email = _norm_email(d.get('email'))
        if not email:
            continue
        first, last = _split_name(d.get('name', ''))
        _add(out, email, first, last, 'leads')
    logger.info("leads: %d unique emails", len(out))
    return out


def collect(source: str, event_id: Optional[str], selected_only: bool) -> Dict[str, dict]:
    if source == 'profiles':
        return load_profiles()
    if source == 'leads':
        return load_leads()
    if source == 'volunteers':
        return load_volunteers(None, event_id, selected_only)
    if source in VOLUNTEER_TYPE_BY_SOURCE:
        return load_volunteers(VOLUNTEER_TYPE_BY_SOURCE[source], event_id, selected_only)
    if source == 'all':
        combined: Dict[str, dict] = {}
        for chunk in (load_profiles(), load_volunteers(None, event_id, selected_only), load_leads()):
            for email, rec in chunk.items():
                _add(combined, email, rec['first_name'], rec['last_name'], rec['source'])
        logger.info("all (deduped union): %d unique emails", len(combined))
        return combined
    raise ValueError(f"unknown source: {source}")


def get_or_create_audience(name: str, apply: bool) -> Optional[str]:
    """Returns the audience id. In dry-run mode, returns None if it doesn't exist."""
    try:
        listed = resend.Audiences.list()
    except Exception as e:
        logger.error("failed to list audiences: %s", e)
        raise
    audiences = listed.get('data', []) if isinstance(listed, dict) else getattr(listed, 'data', [])
    for a in audiences:
        a_name = a.get('name') if isinstance(a, dict) else getattr(a, 'name', None)
        a_id = a.get('id') if isinstance(a, dict) else getattr(a, 'id', None)
        if a_name == name:
            logger.info("found existing audience '%s' id=%s", name, a_id)
            return a_id
    if not apply:
        logger.info("[DRY RUN] audience '%s' does not exist; would create it", name)
        return None
    created = resend.Audiences.create({'name': name})
    a_id = created.get('id') if isinstance(created, dict) else getattr(created, 'id', None)
    logger.info("created audience '%s' id=%s", name, a_id)
    return a_id


def existing_audience_emails(audience_id: str) -> Set[str]:
    """Fetch the full set of emails already in the audience (paginated)."""
    out: Set[str] = set()
    after = None
    while True:
        params: dict = {'limit': 100}
        if after:
            params['after'] = after
        resp = resend.Contacts.list(audience_id=audience_id, params=params)
        data = resp.get('data', []) if isinstance(resp, dict) else getattr(resp, 'data', [])
        if not data:
            break
        last_id = None
        for c in data:
            email = c.get('email') if isinstance(c, dict) else getattr(c, 'email', None)
            cid = c.get('id') if isinstance(c, dict) else getattr(c, 'id', None)
            if email:
                out.add(email.strip().lower())
            last_id = cid
        if len(data) < 100 or not last_id:
            break
        after = last_id
    logger.info("audience %s already has %d contacts", audience_id, len(out))
    return out


def push_contacts(audience_id: str, to_add: Iterable[dict], sleep_seconds: float) -> Tuple[int, int]:
    added, failed = 0, 0
    for rec in to_add:
        try:
            resend.Contacts.create({
                'audience_id': audience_id,
                'email': rec['email'],
                'first_name': rec['first_name'],
                'last_name': rec['last_name'],
                'unsubscribed': False,
            })
            added += 1
            if added % 25 == 0:
                logger.info("  ... added %d so far", added)
        except Exception as e:
            failed += 1
            logger.warning("failed to add %s: %s", rec['email'], e)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return added, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', required=True,
                        choices=['all', 'profiles', 'volunteers', 'mentors', 'judges',
                                 'sponsors', 'helpers', 'leads'])
    parser.add_argument('--audience', required=True, help="Resend audience name (created if missing)")
    parser.add_argument('--event-id', help="Restrict volunteers/mentors/judges to this event_id")
    parser.add_argument('--selected-only', action='store_true',
                        help="For volunteer sources, include only isSelected=True records")
    parser.add_argument('--apply', action='store_true',
                        help="Actually write to Resend. Without this, runs in dry-run mode.")
    parser.add_argument('--sleep', type=float, default=0.05,
                        help="Seconds to sleep between contact writes (default 0.05)")
    parser.add_argument('--limit', type=int, default=0,
                        help="Stop after N contact writes (0 = unlimited)")
    args = parser.parse_args()

    api_key = os.getenv('RESEND_API_KEY') or os.getenv('RESEND_WELCOME_EMAIL_KEY')
    if not api_key:
        logger.error("Missing RESEND_API_KEY (or RESEND_WELCOME_EMAIL_KEY)")
        sys.exit(1)
    resend.api_key = api_key

    contacts = collect(args.source, args.event_id, args.selected_only)
    if not contacts:
        logger.warning("no contacts collected; nothing to do")
        return

    audience_id = get_or_create_audience(args.audience, args.apply)

    already = existing_audience_emails(audience_id) if audience_id else set()
    to_add = [rec for email, rec in sorted(contacts.items()) if email not in already]

    logger.info("collected=%d already_in_audience=%d to_add=%d",
                len(contacts), len(already), len(to_add))

    if args.limit and len(to_add) > args.limit:
        logger.info("limiting writes to first %d (of %d)", args.limit, len(to_add))
        to_add = to_add[:args.limit]

    preview = to_add[:5]
    for rec in preview:
        logger.info("  preview: %s <%s>", (rec['first_name'] + ' ' + rec['last_name']).strip(), rec['email'])
    if len(to_add) > len(preview):
        logger.info("  ... +%d more", len(to_add) - len(preview))

    if not args.apply:
        logger.info("[DRY RUN] no writes performed. Re-run with --apply to push to Resend.")
        return

    if not audience_id:
        logger.error("audience id missing after apply path; aborting")
        sys.exit(2)

    added, failed = push_contacts(audience_id, to_add, args.sleep)
    logger.info("done: added=%d failed=%d audience='%s' id=%s",
                added, failed, args.audience, audience_id)


if __name__ == '__main__':
    main()

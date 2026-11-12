#!/usr/bin/env python3
"""
Size the judge caller-identity problem (reliability plan Nov 2026, Phase 2
step 1): how many judge volunteer docs for an event are NOT keyed by a
PropelAuth UUID, i.e. judges whose login id differs from the id admins assign
them by.

READ-ONLY and COUNTS-ONLY. It never writes, and it never prints names,
emails or ids — only how many docs fall in each bucket.

Buckets for `user_id`:
  empty       — Sheets/admin imports; admins assign these by the doc id
  propel_uuid — self-submitted applications (the common case, always worked)
  oauth       — legacy oauth2|... ids
  other       — anything else

A propel_uuid doc can still belong to a DIFFERENT login than the judge uses
on judging day (applied with one Google account, logs in with another); this
script can't see that without PropelAuth lookups, so treat its number as a
floor.

Usage
-----
  cd backend-ohack.dev
  python scripts/judge_identity_sizing.py --event fall-2026
  python scripts/judge_identity_sizing.py --event fall-2026 --selected-only

The Firestore project comes from the usual FIREBASE_CERT_CONFIG / .env, so
point it at prod deliberately when you want prod numbers.
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def bucket(user_id):
    if not user_id:
        return "empty"
    if UUID_RE.match(str(user_id)):
        return "propel_uuid"
    if str(user_id).startswith("oauth2|"):
        return "oauth"
    return "other"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", required=True, help="event_id, e.g. fall-2026")
    parser.add_argument("--selected-only", action="store_true",
                        help="only count judges with isSelected == True")
    args = parser.parse_args()

    from db.db import get_db
    db = get_db()
    query = db.collection("volunteers") \
              .where("event_id", "==", args.event) \
              .where("volunteer_type", "==", "judge")

    shapes = Counter()
    with_email = Counter()
    total = 0
    for snap in query.stream():
        data = snap.to_dict() or {}
        if args.selected_only and not data.get("isSelected"):
            continue
        total += 1
        b = bucket(data.get("user_id"))
        shapes[b] += 1
        if data.get("email"):
            with_email[b] += 1

    print(f"event={args.event} judges={total}" + (" (selected only)" if args.selected_only else ""))
    for b in ("propel_uuid", "empty", "oauth", "other"):
        print(f"  {b:12s} {shapes[b]:4d}   with email: {with_email[b]}")
    at_risk = total - shapes["propel_uuid"]
    print(f"not keyed by a PropelAuth UUID (fixed by fix/judge-caller-identity): {at_risk}")


if __name__ == "__main__":
    main()

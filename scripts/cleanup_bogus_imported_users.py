#!/usr/bin/env python3
"""
Delete user docs that were created by the off-by-one bug in
import_hackathon_users_from_csv.py's `parse_projects`.

DRY-RUN BY DEFAULT. Pass --apply to mutate Firestore.

Background
----------
Devpost projects CSVs come in two layouts: one with a "Team Number" column
(24 cols, team members start at col 20) and one without (23 cols, team
members start at col 19). The old parser hard-coded the team-member tail to
start at col 21, so each team-member triplet read shifted by one or two
columns. That produced user docs like:
  email_address = "kankipati"
  name          = "sathvikmalla17@gmail.com Abhishek"
  imported      = True
  propel_id     = ""

These docs are linked into the team's `users[]` array as DocumentReferences.
This script:
  1. Finds every user doc matching the bogus fingerprint
        imported == True AND
        propel_id == ""  AND
        email_address present but not a valid email AND
        import_source starts with "projects-"
  2. For each bogus user, walks their import_event_id's hackathon ->
     teams[] -> users[] and finds every team that still references the
     bogus user.
  3. Plans removal of the bogus user_ref from each team's users[] and
     deletion of the user doc.

Re-running the (now-fixed) `import_hackathon_users_from_csv.py --csv-type
projects` against the same event will import the real members that were
never created the first time.

Usage
-----
  cd backend-ohack.dev
  python scripts/cleanup_bogus_imported_users.py            # dry-run
  python scripts/cleanup_bogus_imported_users.py --apply    # write
  python scripts/cleanup_bogus_imported_users.py --event-id cal_poly_humboldt_2025
"""

import argparse
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def looks_bogus(data):
    """A user doc matches the bug fingerprint iff ALL of these hold."""
    if not data.get("imported"):
        return False
    if (data.get("propel_id") or "").strip():
        return False
    ea = (data.get("email_address") or "").strip()
    if not ea or EMAIL_RE.match(ea):
        return False
    src = (data.get("import_source") or "").strip()
    if not src.startswith("projects-"):
        return False
    return True


def find_bogus_users(db, event_filter=None):
    """Return list of dicts {id, data} for every bogus user.

    event_filter: optional event_id; restricts to users with that import_event_id.
    """
    out = []
    for d in db.collection("users").stream():
        data = d.to_dict() or {}
        if event_filter and (data.get("import_event_id") or "") != event_filter:
            continue
        if looks_bogus(data):
            out.append({"id": d.id, "data": data})
    return out


def build_team_index_for_events(db, event_ids):
    """Return {team_doc_id: {ref, data, hackathon_event_id}} for every team
    linked to any of the given hackathon event_ids."""
    out = {}
    for eid in event_ids:
        snaps = list(db.collection("hackathons").where("event_id", "==", eid).stream())
        if not snaps:
            print(f"WARN: hackathon with event_id={eid!r} not found; skipping")
            continue
        snap = snaps[0]
        team_refs = (snap.to_dict() or {}).get("teams") or []
        if not team_refs:
            continue
        team_docs = db.get_all(team_refs)
        for ref, doc in zip(team_refs, team_docs):
            if not doc.exists:
                continue
            out[doc.id] = {
                "ref": ref,
                "data": doc.to_dict() or {},
                "hackathon_event_id": eid,
            }
    return out


def plan_team_user_removals(bogus, teams_by_id):
    """Build per-team plan: {team_id: {team_data, user_ids_to_remove: [str], user_names: [(id, name)]}}."""
    bogus_id_set = {u["id"] for u in bogus}
    name_by_id = {u["id"]: (u["data"].get("name") or "") for u in bogus}
    per_team = {}
    for tid, t in teams_by_id.items():
        users_list = t["data"].get("users") or []
        hits = []
        for u_ref in users_list:
            if hasattr(u_ref, "id") and u_ref.id in bogus_id_set:
                hits.append(u_ref.id)
        if hits:
            per_team[tid] = {
                "team_data": t["data"],
                "hackathon_event_id": t["hackathon_event_id"],
                "user_ids_to_remove": hits,
                "user_names": [(uid, name_by_id.get(uid, "")) for uid in hits],
            }
    return per_team


def main():
    ap = argparse.ArgumentParser(
        description="Delete bogus user docs left behind by the projects-CSV import off-by-one bug.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--event-id",
                    help="Only clean up users whose import_event_id matches. "
                         "Default: clean every event.")
    ap.add_argument("--apply", action="store_true",
                    help="Write to Firestore. Default is dry-run.")
    args = ap.parse_args()

    db = get_db()

    print("Scanning users/ for bogus imported docs ...")
    bogus = find_bogus_users(db, event_filter=args.event_id)
    print(f"Found {len(bogus)} bogus user doc(s)")

    if not bogus:
        print("Nothing to do.")
        return

    by_event = defaultdict(list)
    for u in bogus:
        by_event[u["data"].get("import_event_id") or "(missing)"].append(u)

    print("\nBogus users grouped by import_event_id:")
    for eid, users in sorted(by_event.items()):
        print(f"  {eid}: {len(users)} user(s)")
        for u in users:
            d = u["data"]
            print(f"    - users/{u['id']}  email={d.get('email_address')!r}  name={d.get('name')!r}")

    event_ids = [eid for eid in by_event.keys() if eid != "(missing)"]
    teams_by_id = build_team_index_for_events(db, event_ids)
    print(f"\nLoaded {len(teams_by_id)} team(s) across {len(event_ids)} event(s) to scan for links")

    per_team = plan_team_user_removals(bogus, teams_by_id)
    print(f"{len(per_team)} team(s) reference a bogus user doc")

    sep = "=" * 78
    print("\n" + sep)
    print(f"PLAN ({'APPLY' if args.apply else 'DRY-RUN'})")
    print(sep)

    for tid, plan in per_team.items():
        td = plan["team_data"]
        print(f"\n  teams/{tid}  name={td.get('name')!r}  event={plan['hackathon_event_id']}")
        for uid, name in plan["user_names"]:
            print(f"    - remove user_ref users/{uid}  (name={name!r})")

    print(f"\nDelete {len(bogus)} bogus user doc(s):")
    for u in bogus:
        print(f"  - users/{u['id']}")

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to execute.")
        return

    print("\nApplying writes ...")
    # Step 1: remove bogus refs from each team's users[]
    for tid, plan in per_team.items():
        to_remove = set(plan["user_ids_to_remove"])
        td = plan["team_data"]
        existing = td.get("users") or []
        new_users = [u for u in existing if not (hasattr(u, "id") and u.id in to_remove)]
        if len(new_users) == len(existing):
            continue
        db.collection("teams").document(tid).set({"users": new_users}, merge=True)
        print(f"  teams/{tid}: pruned {len(existing) - len(new_users)} user ref(s)")

    # Step 2: delete the bogus user docs
    for u in bogus:
        db.collection("users").document(u["id"]).delete()
        print(f"  deleted users/{u['id']}")

    print("\nDone.")


if __name__ == "__main__":
    main()

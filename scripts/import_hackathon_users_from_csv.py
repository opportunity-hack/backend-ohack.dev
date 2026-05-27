#!/usr/bin/env python3
"""
Import / backfill hackathon users and team memberships from a CSV.

DRY-RUN BY DEFAULT. Pass --apply to actually write to Firestore.

Why this exists
---------------
Many past hackathons registered participants via Devpost / JotForm / Eventbrite,
and we want their user records and team memberships to exist on ohack.dev so the
public team page (/hack/{event_id}) shows the full roster. Users imported this
way typically have:
  - no propel_id (never logged in via PropelAuth)
  - no profile_image
  - sometimes no name (just an email)
That's OK - they can claim/upgrade later by signing in with the same email.

What it does
------------
For each user in the CSV (looked up by email_address, case-insensitive):
  - If a user doc already exists for that email -> reuse it.
  - Otherwise create a NEW user doc with:
        email_address, name, first_name, last_name,
        nickname (= first name if no name),
        profile_image = "",
        user_id      = ""  (no Slack/OAuth identity yet),
        propel_id    = ""  (never logged in),
        imported     = True,
        import_source = csv basename
        import_event_id = the event we're importing for

For each team (projects / team-roster CSV types):
  - Find existing team in this hackathon by name (case-insensitive, trimmed).
  - If missing, create a new team doc and link it to the hackathon.
  - Append every member's doc-reference to team.users[] (dedup'd by ref).
  - NEVER remove existing members. This is purely additive.

Three CSV formats supported
---------------------------
  --csv-type registrants
      Devpost registrants export. One user per row. Columns include:
          First Name, Last Name, Email, ...
      No team linkage performed. (Use this to seed user docs for later linking.)

  --csv-type projects
      Devpost projects export. One TEAM per row. Variable-length tail:
          Project Title, ..., Submitter First/Last/Email (cols 14-16, 1-indexed),
          ..., then repeating triplets of (First, Last, Email)
          starting at col 22 (1-indexed) for additional team members.
      Team name = "Project Title". Submitter + all triplets are added as members.

  --csv-type roster
      Generic roster CSV. Required columns (header row, case-insensitive):
          team, email
      Optional: first_name, last_name, name
      One MEMBER per row. Use this for the 2026 WiCS backfill.

Idempotency
-----------
Safe to re-run. Existing users (by email) are reused. Existing team members
(by user-doc reference identity) are skipped. The script writes only when a
change is needed, so re-runs against unchanged data produce zero writes.

Usage
-----
  cd backend-ohack.dev

  # 1) See what the script would do (always dry-run first)
  python scripts/import_hackathon_users_from_csv.py \
      --csv ~/Downloads/registrants-with-pii-...csv \
      --event-id 2021_fall \
      --csv-type registrants

  python scripts/import_hackathon_users_from_csv.py \
      --csv ~/Downloads/projects-opportunity-hack-2021-...csv \
      --event-id 2021_fall \
      --csv-type projects

  python scripts/import_hackathon_users_from_csv.py \
      --csv ~/path/to/2026_wics_roster.csv \
      --event-id 2026_spring_wics_asu \
      --csv-type roster

  # 2) When the diff looks right, re-run with --apply
  python scripts/import_hackathon_users_from_csv.py ... --apply
"""

import argparse
import csv
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db


# --------------------------- helpers ---------------------------

def norm_email(e):
    if not e:
        return ""
    return e.strip().lower()


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def looks_like_email(s):
    return bool(s) and bool(_EMAIL_RE.match(s))


def norm_team_name(n):
    if not n:
        return ""
    return re.sub(r"\s+", " ", n).strip().lower()


def display_name(first, last, fallback=""):
    parts = [p.strip() for p in (first, last) if p and p.strip()]
    if parts:
        return " ".join(parts)
    return fallback or ""


# --------------------------- CSV parsers ---------------------------

def parse_registrants(csv_path):
    """Devpost registrants CSV -> list of user dicts."""
    users = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = norm_email(row.get("Email"))
            if not email:
                continue
            users.append({
                "email": email,
                "first_name": (row.get("First Name") or "").strip(),
                "last_name": (row.get("Last Name") or "").strip(),
            })
    return users


def parse_projects(csv_path):
    """Devpost projects CSV -> list of teams with members.

    Devpost's projects CSV starts with a fixed set of project columns, then
    "Submitter First/Last/Email" (cols 13/14/15 in every format we've seen),
    then a variable run of summary columns ("Notes", optional "Team Number",
    "Team Colleges/Universities", "Additional Team Member Count"), and finally
    repeating triplets of (Team Member N First, Last, Email).

    The number of summary columns DIFFERS between exports (older 23-col CSVs
    have no "Team Number"; newer 24-col CSVs do), so the team-member tail
    starts at col 19 or 20 depending on the file. Anchor on the header column
    "Team Member 1 First Name" to find the right offset for this file.

    Each parsed "email" is validated with looks_like_email; rows where the
    triplet shifted off-axis produce a name-as-email and are skipped with a
    warning rather than written as bogus user docs.
    """
    teams = []
    skipped_bogus = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        try:
            i_tm1f = header.index("Team Member 1 First Name")
        except ValueError:
            raise SystemExit(
                "projects CSV is missing 'Team Member 1 First Name' header; "
                f"cannot locate team-member triplets. Header: {header}"
            )
        for row in reader:
            if not row:
                continue
            title = (row[0] if len(row) > 0 else "").strip()
            if not title:
                continue
            submitter_first = (row[13] if len(row) > 13 else "").strip()
            submitter_last  = (row[14] if len(row) > 14 else "").strip()
            submitter_email = norm_email(row[15] if len(row) > 15 else "")
            members = []
            if submitter_email:
                members.append({
                    "email": submitter_email,
                    "first_name": submitter_first,
                    "last_name": submitter_last,
                    "is_submitter": True,
                })
            tail = row[i_tm1f:]
            for i in range(0, len(tail), 3):
                if i + 2 >= len(tail):
                    break
                first = (tail[i] or "").strip()
                last  = (tail[i + 1] or "").strip()
                email = norm_email(tail[i + 2])
                if not email:
                    continue
                if not looks_like_email(email):
                    skipped_bogus.append((title, first, last, email))
                    continue
                members.append({
                    "email": email,
                    "first_name": first,
                    "last_name": last,
                    "is_submitter": False,
                })
            teams.append({"team_name": title, "members": members})
    if skipped_bogus:
        print(f"WARNING: skipped {len(skipped_bogus)} team-member triplet(s) where "
              f"the 'email' value didn't look like an email "
              f"(usually means the CSV triplet shifted off-axis):")
        for team, first, last, email in skipped_bogus:
            print(f"  - team={team!r}  first={first!r}  last={last!r}  email={email!r}")
    return teams


def parse_roster(csv_path):
    """Generic roster CSV: one MEMBER per row.

    Required columns (case-insensitive, aliases accepted):
      team  := team | team name | team_name
      email := email | member email | member_email | email_address
    Optional:
      name        := name | member name | member_name | full_name
      first_name  := first_name | first name
      last_name   := last_name  | last name
    """
    aliases = {
        "team":       ("team", "team name", "team_name"),
        "email":      ("email", "member email", "member_email", "email_address"),
        "name":       ("name", "member name", "member_name", "full_name", "full name"),
        "first_name": ("first_name", "first name"),
        "last_name":  ("last_name",  "last name"),
    }

    teams = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # build a case-insensitive header lookup
        headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}

        def pick(field):
            for alias in aliases[field]:
                if alias in headers:
                    return headers[alias]
            return None

        col_team  = pick("team")
        col_email = pick("email")
        if not col_team or not col_email:
            raise SystemExit(
                f"roster CSV missing required column(s). "
                f"team -> tried {aliases['team']}, email -> tried {aliases['email']}. "
                f"Found columns: {list(headers.values())}"
            )
        col_name  = pick("name")
        col_first = pick("first_name")
        col_last  = pick("last_name")

        skipped = []
        for row in reader:
            team = (row.get(col_team) or "").strip()
            raw_email = (row.get(col_email) or "").strip()
            email = norm_email(raw_email)
            if not team or not email:
                continue
            if not looks_like_email(email):
                name_for_log = (row.get(col_name) or "").strip() if col_name else ""
                skipped.append((team, name_for_log, raw_email))
                continue
            first = (row.get(col_first) or "").strip() if col_first else ""
            last  = (row.get(col_last)  or "").strip() if col_last  else ""
            name  = (row.get(col_name)  or "").strip() if col_name  else ""
            teams[team].append({
                "email": email,
                "first_name": first,
                "last_name": last,
                "name": name,
            })
        if skipped:
            print(f"WARNING: skipped {len(skipped)} row(s) with non-email values in '{col_email}':")
            for team, nm, raw in skipped:
                print(f"  - team={team!r}  name={nm!r}  email={raw!r}")
            print("  These members will NOT be imported. Fix the CSV (real email) and re-run if you want them included.")
    return [{"team_name": k, "members": v} for k, v in teams.items()]


# --------------------------- Firestore ops ---------------------------

class Plan:
    """Accumulator for actions. In dry-run mode we collect; in apply we execute."""

    def __init__(self, db, apply: bool, source: str, event_id: str):
        self.db = db
        self.apply = apply
        self.source = source
        self.event_id = event_id
        self.users_create = []      # list of user dicts
        self.users_reuse  = []      # list of (email, doc_id, doc_name)
        self.teams_create = []      # list of team_name
        self.teams_reuse  = []      # list of (team_name, doc_id)
        self.team_link_to_hackathon = []  # list of (team_name, doc_id)
        self.memberships_add = []   # list of (team_name, user_email, user_doc_id)
        self.memberships_skip = []  # list of (team_name, user_email) already a member

    def summary(self):
        return {
            "users_create": len(self.users_create),
            "users_reuse":  len(self.users_reuse),
            "teams_create": len(self.teams_create),
            "teams_reuse":  len(self.teams_reuse),
            "team_link_to_hackathon": len(self.team_link_to_hackathon),
            "memberships_add":  len(self.memberships_add),
            "memberships_skip": len(self.memberships_skip),
        }


def load_existing_users_by_email(db, emails):
    """Batch-load existing user docs keyed by lowercase email."""
    out = {}
    if not emails:
        return out
    # we stored emails as written; do a streaming exact-match by lowercase first,
    # then a fallback streaming pass for case variants we didn't hit.
    from google.cloud.firestore import FieldFilter
    unique = list({e for e in emails if e})

    # First pass: try the lowercase email exactly.
    CHUNK = 30
    for i in range(0, len(unique), CHUNK):
        chunk = unique[i:i + CHUNK]
        docs = db.collection("users").where(
            filter=FieldFilter("email_address", "in", chunk)
        ).stream()
        for d in docs:
            data = d.to_dict() or {}
            ea = norm_email(data.get("email_address"))
            if ea:
                out[ea] = {"id": d.id, **data}

    # Second pass for emails we didn't find: stream all users and bucket by lower(email).
    # Only do this if we have a non-trivial number of misses, to avoid full scans for
    # tiny imports.
    misses = [e for e in unique if e not in out]
    if misses and len(misses) > 0:
        # Heuristic: only do the full scan if more than 0 misses AND
        # importing more than a handful of users (otherwise per-miss queries are fine).
        if len(misses) >= 5:
            # one full scan, build a lookup, fill remaining misses
            all_users = db.collection("users").stream()
            lookup = {}
            for d in all_users:
                data = d.to_dict() or {}
                ea = norm_email(data.get("email_address"))
                if ea:
                    lookup.setdefault(ea, {"id": d.id, **data})
            for e in misses:
                if e in lookup:
                    out[e] = lookup[e]
        else:
            for e in misses:
                docs = list(
                    db.collection("users")
                    .where(filter=FieldFilter("email_address", "==", e))
                    .stream()
                )
                if docs:
                    data = docs[0].to_dict() or {}
                    out[e] = {"id": docs[0].id, **data}
    return out


def load_existing_teams_in_event(db, event_id):
    """Load all team docs linked to a hackathon, returning {normalized_name: {id, doc, ref}}."""
    out = {}
    # event_id is a FIELD on hackathon docs, not the doc ID.
    docs = list(db.collection("hackathons").where("event_id", "==", event_id).stream())
    if not docs:
        raise SystemExit(f"hackathon with event_id={event_id!r} does not exist")
    snap = docs[0]
    hackathon_ref = snap.reference
    team_refs = (snap.to_dict() or {}).get("teams") or []
    if not team_refs:
        return out, hackathon_ref, []
    team_docs = db.get_all(team_refs)
    for ref, doc in zip(team_refs, team_docs):
        if not doc.exists:
            continue
        data = doc.to_dict() or {}
        key = norm_team_name(data.get("name"))
        if not key:
            continue
        out[key] = {"id": doc.id, "name": data.get("name"), "data": data, "ref": ref}
    return out, hackathon_ref, list(team_refs)


def create_user_doc(db, member, source, event_id):
    doc_id = uuid.uuid1().hex
    name = member.get("name") or display_name(
        member.get("first_name", ""), member.get("last_name", "")
    )
    payload = {
        "email_address": member["email"],
        "first_name": member.get("first_name", ""),
        "last_name":  member.get("last_name", ""),
        "name":       name,
        "nickname":   name or (member.get("first_name") or ""),
        "profile_image": "",
        "user_id":   "",
        "propel_id": "",
        "imported": True,
        "import_source": source,
        "import_event_id": event_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("users").document(doc_id).set(payload)
    return doc_id


def create_team_doc(db, team_name, event_id):
    doc_id = uuid.uuid1().hex
    db.collection("teams").document(doc_id).set({
        "name": team_name,
        "users": [],
        "hackathon_event_id": event_id,
        "active": True,
        "status": "IMPORTED",
        "created": datetime.now(timezone.utc).isoformat(),
        "imported": True,
    })
    return doc_id


def link_team_to_hackathon(db, hackathon_ref, team_ref, existing_team_refs):
    if any(r.id == team_ref.id for r in existing_team_refs):
        return False
    new_refs = list(existing_team_refs) + [team_ref]
    hackathon_ref.set({"teams": new_refs}, merge=True)
    return True


def add_user_refs_to_team(db, team_id, team_data, user_refs_to_add):
    """Add user refs to team.users, deduped by ref.id. Returns # added."""
    existing = team_data.get("users") or []
    existing_ids = {r.id for r in existing if hasattr(r, "id")}
    to_append = [r for r in user_refs_to_add if r.id not in existing_ids]
    if not to_append:
        return 0
    new_users = list(existing) + to_append
    db.collection("teams").document(team_id).set({"users": new_users}, merge=True)
    return len(to_append)


# --------------------------- main flow ---------------------------

def plan_and_execute(db, csv_type, parsed, event_id, source, apply):
    plan = Plan(db=db, apply=apply, source=source, event_id=event_id)

    # Collect every email we touch, batch-fetch existing user docs once.
    all_emails = set()
    if csv_type == "registrants":
        for u in parsed:
            all_emails.add(u["email"])
    else:
        for t in parsed:
            for m in t["members"]:
                all_emails.add(m["email"])

    print(f"Looking up {len(all_emails)} unique emails in users/ ...")
    existing_users = load_existing_users_by_email(db, all_emails) if all_emails else {}
    print(f"  -> {len(existing_users)} already exist, {len(all_emails) - len(existing_users)} new")

    # Resolve/create user docs. In dry-run we just plan; in apply we write.
    email_to_doc_id = {}  # email -> user_doc_id
    for email in sorted(all_emails):
        if not email:
            continue
        existing = existing_users.get(email)
        if existing:
            email_to_doc_id[email] = existing["id"]
            plan.users_reuse.append((email, existing["id"], existing.get("name") or ""))
        else:
            # find the first member dict for this email so we have name fields
            member_template = None
            if csv_type == "registrants":
                for u in parsed:
                    if u["email"] == email:
                        member_template = u
                        break
            else:
                for t in parsed:
                    for m in t["members"]:
                        if m["email"] == email:
                            member_template = m
                            break
                    if member_template:
                        break
            member_template = member_template or {"email": email}
            plan.users_create.append({
                "email": email,
                "first_name": member_template.get("first_name", ""),
                "last_name":  member_template.get("last_name", ""),
                "name":       member_template.get("name") or display_name(
                    member_template.get("first_name", ""),
                    member_template.get("last_name", ""),
                ),
            })
            if apply:
                doc_id = create_user_doc(db, member_template, source, event_id)
                email_to_doc_id[email] = doc_id
            else:
                email_to_doc_id[email] = f"(would-create:{email})"

    # If we're just importing registrants, we're done.
    if csv_type == "registrants":
        return plan

    # Otherwise, resolve teams.
    print(f"Resolving teams under hackathons/{event_id} ...")
    existing_teams, hackathon_ref, hackathon_team_refs = load_existing_teams_in_event(db, event_id)
    print(f"  -> {len(existing_teams)} existing teams already linked to this hackathon")

    team_ref_cache = {}  # team_doc_id -> DocumentReference

    for team_entry in parsed:
        team_name = team_entry["team_name"].strip()
        key = norm_team_name(team_name)
        if not key:
            continue

        if key in existing_teams:
            team_info = existing_teams[key]
            team_doc_id = team_info["id"]
            team_data = team_info["data"]
            team_ref_cache[team_doc_id] = team_info["ref"]
            plan.teams_reuse.append((team_name, team_doc_id))
        else:
            plan.teams_create.append(team_name)
            if apply:
                team_doc_id = create_team_doc(db, team_name, event_id)
                team_ref = db.collection("teams").document(team_doc_id)
                team_data = team_ref.get().to_dict() or {}
                team_ref_cache[team_doc_id] = team_ref
                # link to hackathon
                if link_team_to_hackathon(db, hackathon_ref, team_ref, hackathon_team_refs):
                    hackathon_team_refs.append(team_ref)
                    plan.team_link_to_hackathon.append((team_name, team_doc_id))
            else:
                team_doc_id = f"(would-create:{team_name})"
                team_data = {"users": []}
                plan.team_link_to_hackathon.append((team_name, team_doc_id))

        # plan memberships
        existing_member_ref_ids = (
            {r.id for r in (team_data.get("users") or []) if hasattr(r, "id")}
            if apply or key in existing_teams
            else set()
        )
        member_refs_to_add = []
        for m in team_entry["members"]:
            user_doc_id = email_to_doc_id.get(m["email"])
            if not user_doc_id:
                continue
            if apply:
                # already-on-team check is by doc id
                if user_doc_id in existing_member_ref_ids:
                    plan.memberships_skip.append((team_name, m["email"]))
                    continue
                ref = db.collection("users").document(user_doc_id)
                member_refs_to_add.append(ref)
                plan.memberships_add.append((team_name, m["email"], user_doc_id))
            else:
                # dry-run: don't know real doc id for would-create users
                if user_doc_id.startswith("(would-create:"):
                    plan.memberships_add.append((team_name, m["email"], user_doc_id))
                elif user_doc_id in existing_member_ref_ids:
                    plan.memberships_skip.append((team_name, m["email"]))
                else:
                    plan.memberships_add.append((team_name, m["email"], user_doc_id))

        if apply and member_refs_to_add:
            # re-read latest team data so we don't clobber concurrent writes
            latest = db.collection("teams").document(team_doc_id).get().to_dict() or {}
            add_user_refs_to_team(db, team_doc_id, latest, member_refs_to_add)

    return plan


def print_plan(plan, apply):
    s = plan.summary()
    print()
    print("=" * 60)
    print(f"PLAN ({'APPLY' if apply else 'DRY-RUN'})")
    print("=" * 60)
    print(f"  users to CREATE: {s['users_create']}")
    print(f"  users to REUSE : {s['users_reuse']}")
    print(f"  teams to CREATE: {s['teams_create']}")
    print(f"  teams to REUSE : {s['teams_reuse']}")
    print(f"  teams to LINK to hackathon: {s['team_link_to_hackathon']}")
    print(f"  memberships to ADD : {s['memberships_add']}")
    print(f"  memberships to SKIP (already on team): {s['memberships_skip']}")
    print()

    def show(label, items, limit=20):
        if not items:
            return
        print(f"  {label} (showing {min(limit, len(items))} of {len(items)}):")
        for x in items[:limit]:
            print(f"    - {x}")

    show("users to CREATE", [u["email"] for u in plan.users_create])
    show("teams to CREATE", plan.teams_create)
    show("memberships to ADD",
         [f"{t}  <-  {e}" for (t, e, _id) in plan.memberships_add])

    if not apply:
        print()
        print("DRY-RUN complete. No data was written. Re-run with --apply to execute.")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--csv", required=True, help="Path to the CSV file")
    p.add_argument("--event-id", required=True, help="Hackathon event_id, e.g. 2026_spring_wics_asu")
    p.add_argument("--csv-type", required=True, choices=["registrants", "projects", "roster"],
                   help="CSV layout. See module docstring for each format.")
    p.add_argument("--apply", action="store_true",
                   help="ACTUALLY WRITE to Firestore. Default is dry-run (no writes).")
    args = p.parse_args()

    csv_path = os.path.expanduser(args.csv)
    if not os.path.exists(csv_path):
        raise SystemExit(f"CSV not found: {csv_path}")

    if args.csv_type == "registrants":
        parsed = parse_registrants(csv_path)
        print(f"Parsed {len(parsed)} registrants from {csv_path}")
    elif args.csv_type == "projects":
        parsed = parse_projects(csv_path)
        total_members = sum(len(t["members"]) for t in parsed)
        print(f"Parsed {len(parsed)} teams ({total_members} member rows) from {csv_path}")
    elif args.csv_type == "roster":
        parsed = parse_roster(csv_path)
        total_members = sum(len(t["members"]) for t in parsed)
        print(f"Parsed {len(parsed)} teams ({total_members} member rows) from {csv_path}")
    else:
        raise SystemExit(f"unknown csv-type: {args.csv_type}")

    db = get_db()
    source = os.path.basename(csv_path)
    plan = plan_and_execute(db, args.csv_type, parsed, args.event_id, source, args.apply)
    print_plan(plan, args.apply)


if __name__ == "__main__":
    main()

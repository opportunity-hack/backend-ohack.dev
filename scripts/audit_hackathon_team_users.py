#!/usr/bin/env python3
"""
Read-only audit of team membership for a hackathon event.

For a given event_id, walks:
  hackathons/{event_id}
    -> teams[] (DocumentReferences)
        -> teams/{team_id}
            -> users[] (DocumentReferences -> users/{user_id})

Reports per team:
  - team name + doc id
  - # of user refs on the team
  - for each ref:
      - whether the user doc exists
      - quality flags: missing name / no profile_image / no email / no propel_id

Also prints a summary highlighting:
  - teams with 0 or 1 members (likely missing roster on /hack/<event_id>)
  - "ghost" user docs (no name AND no propel_id - imported but never linked)
  - dangling refs (team.users[] points to a deleted user doc)

This script ONLY READS. It never writes. Safe to run anywhere with prod creds.

Usage:
  cd backend-ohack.dev
  python scripts/audit_hackathon_team_users.py --event-id 2026_spring_wics_asu
  python scripts/audit_hackathon_team_users.py --event-id 2021_fall --json > audit.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db


def audit_event(event_id: str):
    db = get_db()
    # event_id is a FIELD on hackathon docs, not the doc ID. Look it up by field.
    docs = list(db.collection("hackathons").where("event_id", "==", event_id).stream())
    if not docs:
        return {"error": f"hackathon with event_id={event_id!r} does not exist"}
    hackathon_doc = docs[0]
    hackathon_ref = hackathon_doc.reference
    hackathon_data = hackathon_doc.to_dict() or {}
    team_refs = hackathon_data.get("teams") or []

    # Batch-fetch all team docs at once.
    team_docs = db.get_all(team_refs) if team_refs else []

    # Collect every user ref across all teams, then batch-fetch once.
    all_user_refs = []
    seen_user_ref_ids = set()
    teams_summary = []
    for team_doc, team_ref in zip(team_docs, team_refs):
        if not team_doc.exists:
            teams_summary.append({
                "team_id": team_ref.id,
                "exists": False,
                "name": None,
                "users": [],
            })
            continue
        td = team_doc.to_dict() or {}
        user_refs = td.get("users") or []
        for ur in user_refs:
            if hasattr(ur, "id") and ur.id not in seen_user_ref_ids:
                seen_user_ref_ids.add(ur.id)
                all_user_refs.append(ur)
        teams_summary.append({
            "team_id": team_doc.id,
            "exists": True,
            "name": td.get("name"),
            "slack_channel": td.get("slack_channel"),
            "active": td.get("active"),
            "status": td.get("status"),
            "hackathon_event_id": td.get("hackathon_event_id"),
            "_user_refs": user_refs,
        })

    user_docs = db.get_all(all_user_refs) if all_user_refs else []
    user_map = {}
    for ud in user_docs:
        if ud.exists:
            d = ud.to_dict() or {}
            user_map[ud.id] = {
                "id": ud.id,
                "name": d.get("name") or "",
                "nickname": d.get("nickname") or "",
                "email_address": d.get("email_address") or "",
                "profile_image": d.get("profile_image") or "",
                "user_id": d.get("user_id") or "",
                "propel_id": d.get("propel_id") or "",
                "github": d.get("github") or "",
            }
        else:
            user_map[ud.id] = None  # dangling ref

    # Build per-team detail.
    teams_out = []
    ghost_user_ids = set()
    dangling_ref_ids = set()
    for t in teams_summary:
        if not t["exists"]:
            teams_out.append(t)
            continue
        members = []
        for ur in t.pop("_user_refs"):
            u = user_map.get(ur.id)
            if u is None:
                members.append({
                    "ref_id": ur.id,
                    "exists": False,
                    "flags": ["DANGLING_REF"],
                })
                dangling_ref_ids.add(ur.id)
                continue
            flags = []
            if not u["name"] and not u["nickname"]:
                flags.append("NO_NAME")
            if not u["profile_image"]:
                flags.append("NO_PROFILE_IMAGE")
            if not u["email_address"]:
                flags.append("NO_EMAIL")
            if not u["propel_id"]:
                flags.append("NO_PROPEL_ID")  # never logged in via PropelAuth
            if "NO_NAME" in flags and "NO_PROPEL_ID" in flags:
                ghost_user_ids.add(u["id"])
            members.append({
                "ref_id": ur.id,
                "exists": True,
                "name": u["name"] or u["nickname"],
                "email": u["email_address"],
                "user_id": u["user_id"],
                "has_propel": bool(u["propel_id"]),
                "has_image": bool(u["profile_image"]),
                "flags": flags,
            })
        t["member_count"] = len(members)
        t["members"] = members
        teams_out.append(t)

    return {
        "event_id": event_id,
        "team_count": len(teams_out),
        "teams": teams_out,
        "summary": {
            "teams_with_zero_members": [
                t["team_id"] for t in teams_out
                if t.get("exists") and t.get("member_count", 0) == 0
            ],
            "teams_with_one_member": [
                t["team_id"] for t in teams_out
                if t.get("exists") and t.get("member_count", 0) == 1
            ],
            "dangling_ref_count": len(dangling_ref_ids),
            "ghost_user_count": len(ghost_user_ids),
            "total_unique_user_refs": len(seen_user_ref_ids),
        },
    }


def print_human(report):
    if "error" in report:
        print(f"ERROR: {report['error']}")
        return
    print(f"\n=== Audit: {report['event_id']} ===")
    print(f"Teams: {report['team_count']}\n")
    for t in report["teams"]:
        if not t.get("exists"):
            print(f"  [MISSING TEAM DOC] {t['team_id']}")
            continue
        marker = ""
        mc = t.get("member_count", 0)
        if mc == 0:
            marker = "  <-- EMPTY"
        elif mc == 1:
            marker = "  <-- only 1 member (likely missing roster)"
        print(f"  Team: {t['name']!r}  ({mc} members) [{t['team_id']}]{marker}")
        for m in t.get("members", []):
            if not m.get("exists"):
                print(f"      - DANGLING ref -> users/{m['ref_id']} (doc missing)")
                continue
            flag_str = (" [" + ",".join(m["flags"]) + "]") if m["flags"] else ""
            display = m["name"] or "(no name)"
            email = m["email"] or "(no email)"
            print(f"      - {display}  <{email}>  user_id={m['user_id'] or '(none)'}{flag_str}")
        print()

    s = report["summary"]
    print("--- Summary ---")
    print(f"  Total unique user refs across teams: {s['total_unique_user_refs']}")
    print(f"  Teams with 0 members: {len(s['teams_with_zero_members'])}")
    print(f"  Teams with only 1 member: {len(s['teams_with_one_member'])}")
    print(f"  Dangling user refs (team points to missing user doc): {s['dangling_ref_count']}")
    print(f"  Ghost users (no name AND no propel_id - imported but never logged in): {s['ghost_user_count']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--event-id", required=True, help="Hackathon event_id (e.g. 2026_spring_wics_asu)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of the human-readable report")
    args = p.parse_args()

    report = audit_event(args.event_id)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_human(report)


if __name__ == "__main__":
    main()

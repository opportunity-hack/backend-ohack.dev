#!/usr/bin/env python3
"""
Backfill the "hacker funnel" summary for a hackathon from Devpost CSV exports.

DRY-RUN BY DEFAULT. Pass --apply to actually write to Firestore.

Why this exists
---------------
We want a public, PII-free summary of how many people moved through each stage
of the hackathon: registered -> started a project -> submitted -> winning teams.
The first three stages live in Devpost; the last two live in our own teams
collection (status in WINNING_STATUSES). This script writes the Devpost-derived
counts so the frontend funnel page can render without re-parsing CSVs on each
request.

Storage
-------
  hackathons/{hackathon_doc_id}/funnel/summary

That's a SUBCOLLECTION called `funnel` with a single document `summary`. The
hackathon doc itself stays small; the funnel doc is public-safe (counts only,
no emails or names).

Shape (counts only - no PII)
----------------------------
  {
    registered: int,
    started_project: int,         # any Devpost project, any status
    submitted_project: int,       # Project Status starts with "Submitted"
    submitted_gallery_visible: int,
    status_breakdown: {...},      # Project Status counts
    step_breakdown: {...},        # Highest Step Completed counts
    referral_breakdown: {...},    # Who told you about this hackathon?
    teammate_intent_breakdown: {...},
    country_breakdown: {...},
    source: "devpost_csv",
    source_files: [<basename(s)>],
    last_updated: ISO,
    last_updated_by: "scripts/backfill_devpost_funnel.py",
  }

Inputs
------
Both flags are optional; pass whichever CSV(s) you have. Counts derived from a
missing CSV are simply absent from the written doc.

  --registrants-csv <path>   Devpost registrants export
  --projects-csv <path>      Devpost projects export
  --event-id <event_id>      The hackathons.event_id (e.g. 2026_spring_wics_asu)

Idempotency
-----------
Safe to re-run. The summary doc is `set` (full overwrite). Re-runs against the
same CSV inputs produce the same doc.

Usage
-----
  cd backend-ohack.dev

  python scripts/backfill_devpost_funnel.py \
      --event-id 2026_spring_wics_asu \
      --registrants-csv ~/Downloads/registrants-with-pii-...csv \
      --projects-csv    ~/Downloads/projects-women-in-computer-science-...csv

  python scripts/backfill_devpost_funnel.py ... --apply
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _norm_email(e):
    if not e:
        return ""
    return e.strip().lower()


def _looks_like_email(s):
    return bool(s) and bool(_EMAIL_RE.match(s))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db


# --------------------------- CSV parsers ---------------------------

def parse_registrants_csv(path):
    """Return list of dicts; one per registrant row (no email here - we only need counts)."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # tolerate empty trailing rows
            if not any((v or "").strip() for v in row.values()):
                continue
            rows.append({
                "country": (row.get("Country") or "").strip(),
                "referral": (row.get("Who told you about this hackathon?") or "").strip(),
                "teammate_intent": (row.get("Do you have teammates?") or "").strip(),
            })
    return rows


def parse_projects_csv(path):
    """Return list of dicts; one per project, with `member_emails` extracted.

    Devpost projects CSV layout (verified against the 2026 WiCS export):
      col  0  Project Title
      col  2  Project Status
      col  4  Highest Step Completed
      col 13  Submitter First Name
      col 14  Submitter Last Name
      col 15  Submitter Email
      col 21  Additional Team Member Count    <- NOT a name; skip
      col 22+ Repeating triplets: (Team Member N First, Last, Email)

    We use csv.reader (positional) for the tail since DictReader can't
    represent the variable-length repeating columns.
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if not row:
                continue
            title = (row[0] if len(row) > 0 else "").strip()
            if not title:
                continue
            status = (row[2] if len(row) > 2 else "").strip()
            step   = (row[4] if len(row) > 4 else "").strip()

            emails = []
            sub_email = _norm_email(row[15] if len(row) > 15 else "")
            if _looks_like_email(sub_email):
                emails.append(sub_email)

            tail = row[22:]  # triplets start AFTER the Additional Team Member Count column
            for i in range(0, len(tail), 3):
                if i + 2 >= len(tail):
                    break
                em = _norm_email(tail[i + 2])
                if _looks_like_email(em):
                    emails.append(em)

            rows.append({
                "title": title,
                "status": status,
                "step":   step,
                "member_emails": emails,  # ordered, may contain dups w/in a project
            })
    return rows


# --------------------------- summary builder ---------------------------

def build_summary(registrants, projects, source_files):
    out = {
        "source": "devpost_csv",
        "source_files": source_files,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_updated_by": "scripts/backfill_devpost_funnel.py",
    }

    if registrants is not None:
        out["registered"] = len(registrants)
        out["referral_breakdown"] = dict(
            Counter((r["referral"] or "Unknown") for r in registrants)
        )
        out["teammate_intent_breakdown"] = dict(
            Counter((r["teammate_intent"] or "Unknown") for r in registrants)
        )
        out["country_breakdown"] = dict(
            Counter((r["country"] or "Unknown") for r in registrants)
        )

    if projects is not None:
        status_counts = Counter(p["status"] or "Unknown" for p in projects)
        out["status_breakdown"] = dict(status_counts)
        out["step_breakdown"] = dict(Counter(p["step"] or "Unknown" for p in projects))

        # Project (team) counts.
        out["started_project_teams"] = len(projects)
        submitted_team_count = sum(
            c for s, c in status_counts.items() if s.startswith("Submitted")
        )
        out["submitted_project_teams"] = submitted_team_count
        out["submitted_gallery_visible_teams"] = status_counts.get(
            "Submitted (Gallery/Visible)", 0
        )

        # People counts (deduped by email so a person on two projects only
        # counts once at each stage). This is the unit the funnel displays.
        started_emails = set()
        submitted_emails = set()
        gallery_emails = set()
        for p in projects:
            ems = set(p["member_emails"])
            started_emails |= ems
            if (p["status"] or "").startswith("Submitted"):
                submitted_emails |= ems
            if p["status"] == "Submitted (Gallery/Visible)":
                gallery_emails |= ems
        out["started_project"] = len(started_emails)
        out["submitted_project"] = len(submitted_emails)
        out["submitted_gallery_visible"] = len(gallery_emails)

    return out


# --------------------------- firestore ---------------------------

def find_hackathon_doc(db, event_id):
    docs = list(db.collection("hackathons").where("event_id", "==", event_id).stream())
    if not docs:
        raise SystemExit(f"hackathon with event_id={event_id!r} does not exist")
    return docs[0]


def write_summary(db, hackathon_doc_id, summary):
    db.collection("hackathons").document(hackathon_doc_id) \
        .collection("funnel").document("summary").set(summary)


def read_existing_summary(db, hackathon_doc_id):
    snap = db.collection("hackathons").document(hackathon_doc_id) \
        .collection("funnel").document("summary").get()
    if snap.exists:
        return snap.to_dict()
    return None


# --------------------------- main ---------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--event-id", required=True,
                   help="Hackathon event_id (e.g. 2026_spring_wics_asu)")
    p.add_argument("--registrants-csv", help="Devpost registrants CSV path")
    p.add_argument("--projects-csv",    help="Devpost projects CSV path")
    p.add_argument("--apply", action="store_true",
                   help="ACTUALLY WRITE to Firestore. Default is dry-run.")
    args = p.parse_args()

    if not args.registrants_csv and not args.projects_csv:
        raise SystemExit("provide at least one of --registrants-csv or --projects-csv")

    registrants = None
    projects = None
    source_files = []

    if args.registrants_csv:
        path = os.path.expanduser(args.registrants_csv)
        if not os.path.exists(path):
            raise SystemExit(f"registrants CSV not found: {path}")
        registrants = parse_registrants_csv(path)
        source_files.append(os.path.basename(path))
        print(f"Parsed {len(registrants)} registrants from {path}")

    if args.projects_csv:
        path = os.path.expanduser(args.projects_csv)
        if not os.path.exists(path):
            raise SystemExit(f"projects CSV not found: {path}")
        projects = parse_projects_csv(path)
        source_files.append(os.path.basename(path))
        print(f"Parsed {len(projects)} projects from {path}")

    summary = build_summary(registrants, projects, source_files)

    db = get_db()
    hackathon_doc = find_hackathon_doc(db, args.event_id)
    hackathon_doc_id = hackathon_doc.id
    existing = read_existing_summary(db, hackathon_doc_id)

    print()
    print("=" * 60)
    print(f"PLAN ({'APPLY' if args.apply else 'DRY-RUN'})")
    print("=" * 60)
    print(f"Target: hackathons/{hackathon_doc_id}/funnel/summary")
    print(f"  (event_id={args.event_id})")
    print()
    if existing:
        print("Existing summary doc:")
        print(json.dumps(existing, indent=2, default=str))
        print()
    else:
        print("No existing summary doc - this would create a new one.\n")

    print("Summary that would be written:")
    print(json.dumps(summary, indent=2, default=str))
    print()

    if args.apply:
        write_summary(db, hackathon_doc_id, summary)
        print(f"WROTE hackathons/{hackathon_doc_id}/funnel/summary")
    else:
        print("DRY-RUN complete. No data was written. Re-run with --apply to execute.")


if __name__ == "__main__":
    main()

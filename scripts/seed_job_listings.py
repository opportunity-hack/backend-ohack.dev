#!/usr/bin/env python3
"""
Seed the volunteer job board (job_listings collection) with the three Fall 2026
organizer roles: Social Media Manager, Hackathon Operations Lead (Phoenix), and
Mentor Program Lead.

DRY-RUN BY DEFAULT. Pass --apply to actually write to Firestore.

Idempotency
-----------
Safe to re-run: a listing whose slug already exists is SKIPPED (never
overwritten), so admin edits made via /admin/jobs are preserved. Listings seed
as status="draft" — publish them from the admin UI.

Usage
-----
  cd backend-ohack.dev
  python scripts/seed_job_listings.py            # dry run
  python scripts/seed_job_listings.py --apply    # write drafts
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.db import get_db
from common.utils.validators import validate_job_listing

SHARED_WHAT_YOU_GET = """## What you get

This is a volunteer role, and it is not paid — we are a nonprofit with very limited funds. What we can offer is real:

- **Real, portfolio-worthy experience** with actual users, constraints, and deadlines — not a side project that never ships
- **Hearts** toward certificates, plus **LinkedIn recommendations and references** from people who watched you deliver
- A leadership title you can put on your resume, backed by work anyone can verify
- The satisfaction of helping nonprofits get software they could never afford

Everyone who runs Opportunity Hack has a full-time job. We volunteer because we believe tech can do good. Join us.

## The fine print

- We are unable to sponsor visas. This is an unpaid volunteer role.
- We run on Slack and email — fast, clear communication is the core skill for every role here.
- We'd love someone who sticks around past Fall 2026, but we'll take all the help we can get."""

LISTINGS = [
    {
        "slug": "social-media-manager",
        "title": "Social Media Manager",
        "status": "draft",
        "location_type": "remote",
        "location_label": "Remote (US time zones preferred)",
        "hours_per_week_label": "3–5",
        "min_hours_per_week": 3,
        "duration_ask": "6+ months preferred — through Fall 2026 and beyond",
        "valid_through": "2026-10-31",
        "summary": "Own Opportunity Hack's voice. Turn real nonprofit projects and hackathon stories into LinkedIn, Instagram, and Threads posts that bring in volunteers, sponsors, and nonprofits.",
        "description_markdown": """## About Opportunity Hack

We're a nonprofit that connects tech volunteers with nonprofits who need software. Our hackathons bring together developers, designers, and product folks to build real solutions in a weekend — and our projects keep shipping year-round.

## The role

Every weekend we generate stories most organizations would kill for: a team of strangers shipping a food-bank inventory system in 48 hours, a student getting their first job offer off hackathon portfolio work, a nonprofit director seeing their spreadsheet nightmare become an app. Almost none of it gets told. That's the job.

## What you'll do

- Own our posting cadence on LinkedIn, Instagram, and Threads (2–4 posts/week)
- Mine our blog, project pages, and Slack for stories worth telling — and tell them
- Run the social push before, during, and after the Fall 2026 hackathon (the during part is the fun part)
- Watch what works and double down — you'll have our analytics and full creative latitude
- Recruit: every post is ultimately about bringing in volunteers, nonprofits, and sponsors

## Who this is for

- Aspiring social media / content / comms folks who want a real brand to run, not a mock portfolio piece
- Experienced marketers who want to give back with skills they already have
- You write clearly, you ship consistently, and you don't need someone to hand you a content calendar

""" + SHARED_WHAT_YOU_GET,
        "work_sample_prompt": "Pick any project from [ohack.dev/projects](https://www.ohack.dev/projects) and write the LinkedIn post you'd publish about it. Real project, real details — we want to see how you find the story.",
        "video_prompts": [
            "In under 2 minutes: who are you, why this role, and why Opportunity Hack?",
            "Walk us through the post you wrote in the previous step — why that project, and what were you optimizing for?",
        ],
    },
    {
        "slug": "hackathon-operations-lead",
        "title": "Hackathon Operations Lead — Phoenix",
        "status": "draft",
        "location_type": "phoenix_in_person",
        "location_label": "Phoenix / Tempe, AZ (on-site at ASU)",
        "hours_per_week_label": "2–6",
        "min_hours_per_week": 2,
        "duration_ask": "Through the Fall 2026 event — ~2 hrs/week now, 4–6 hrs/week in the final six weeks, plus the full event weekend on-site",
        "valid_through": "2026-10-31",
        "summary": "Be the person who makes the Fall 2026 hackathon actually run: venue, food, check-in, schedule, and the hundred small saves nobody notices when they go right. On-site in Phoenix/Tempe.",
        "description_markdown": """## About Opportunity Hack

We're a nonprofit that connects tech volunteers with nonprofits who need software. Our flagship hackathon happens each fall at ASU in Tempe — 100+ hackers, a dozen nonprofits, one weekend.

## The role

A hackathon is a live event wearing a tech costume. Catering shows up late, the check-in line backs up, a room double-books, the awards ceremony needs to start in ten minutes and the demo laptop won't connect. The Operations Lead is the person who handles all of that so hackers and nonprofits only experience the good parts.

## What you'll do

- **Before** (~2 hrs/week, ramping to 4–6 in the final six weeks): help plan the venue layout, food schedule, volunteer shifts, signage, and run-of-show with the core team
- **During** (the full event weekend, on-site): run check-in, keep the schedule honest, direct day-of volunteers, and solve problems in real time
- **After**: a short retro so next year's event starts smarter
- Coordinate over Slack with the core organizing team — we're responsive and we'll have your back

## Who this is for

- Event, program, or project coordinators (aspiring or experienced) who want a serious line on their resume
- People who stay calm when the plan meets reality, and communicate clearly while it's happening
- **You must be in the Phoenix/Tempe area and available on-site the full event weekend** — this one can't be done remotely

""" + SHARED_WHAT_YOU_GET,
        "work_sample_prompt": "It's 9am on hackathon Saturday. Catering is 45 minutes late, 60 hungry hackers are asking questions, and the venue contact isn't answering. Walk us through your next 30 minutes — be specific.",
        "video_prompts": [
            "In under 2 minutes: who are you, why this role, and what's the best event (any kind) you've helped run?",
            "Explain the plan you wrote in the previous step — what's the first thing that could go wrong with it, and what would you do then?",
        ],
    },
    {
        "slug": "mentor-program-lead",
        "title": "Mentor Program Lead",
        "status": "draft",
        "location_type": "hybrid",
        "location_label": "Remote-friendly (event weekend hybrid)",
        "hours_per_week_label": "3–4",
        "min_hours_per_week": 3,
        "duration_ask": "Through the Fall 2026 event plus ~3 months — 6+ months welcome",
        "valid_through": "2026-10-31",
        "summary": "Recruit, prep, and lead the 20+ industry mentors who keep hackathon teams unblocked. You're the multiplier: great mentoring is the difference between demos and shipped software.",
        "description_markdown": """## About Opportunity Hack

We're a nonprofit that connects tech volunteers with nonprofits who need software. At our hackathons, mentors — engineers, designers, PMs from across the industry — are the difference between teams that flounder and teams that ship.

## The role

We usually have more teams needing help than mentors proactively giving it. The Mentor Program Lead owns that gap: recruiting good mentors, setting expectations before the event, and running the mentor bench during the weekend so no team sits blocked for hours.

## What you'll do

- Recruit mentors from your network, ours, and past events (we have the application system; you drive the pipeline)
- Review mentor applications and set expectations: at OHack, mentoring means proactive help — checking on teams, reviewing code, unblocking — not sitting in a room being available
- Run mentor onboarding before the event (a call + a Slack channel + our existing checklists and tools)
- During the weekend: run the mentor schedule, watch our team-coverage dashboard, and route mentors to teams that are stuck (remote-friendly; being on-site in Tempe is a plus, not a requirement)
- Afterwards: make sure great mentors get recognized (certificates, LinkedIn recommendations) so they come back

## Who this is for

- Engineering managers, senior ICs, PMs, or community builders who like making other people effective
- Aspiring leads who want real people-coordination experience with visible outcomes
- You're organized, you follow up without being chased, and you're comfortable nudging busy professionals over Slack

""" + SHARED_WHAT_YOU_GET,
        "work_sample_prompt": "Draft the Slack message you'd send to 20 confirmed mentors on the Monday before the hackathon. Assume half have never mentored a hackathon before.",
        "video_prompts": [
            "In under 2 minutes: who are you, why this role, and tell us about a time you helped someone else succeed at something technical.",
            "Read us the Slack message you drafted in the previous step — then tell us what you'd change about it for a mentor who went quiet mid-event.",
        ],
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed job_listings with the Fall 2026 organizer roles")
    parser.add_argument("--apply", action="store_true", help="Actually write to Firestore (default: dry run)")
    args = parser.parse_args()

    now = datetime.now().isoformat()
    actor = {"propel_user_id": None, "email": "scripts/seed_job_listings.py"}

    db = get_db()
    for listing in LISTINGS:
        validate_job_listing(listing)  # raises on drift between seed and validators
        slug = listing["slug"]
        doc_ref = db.collection("job_listings").document(slug)
        if doc_ref.get().exists:
            print(f"SKIP   {slug} (already exists — not overwriting)")
            continue
        doc = dict(listing)
        doc.pop("slug")
        doc.update({
            "posted_at": "",
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "last_updated_by": actor,
        })
        if args.apply:
            doc_ref.set(doc)
            print(f"WROTE  {slug} (status=draft)")
        else:
            print(f"DRYRUN {slug} — would write {len(doc['description_markdown'])} chars of description")

    if not args.apply:
        print("\nDry run complete. Re-run with --apply to write drafts, then publish via /admin/jobs.")


if __name__ == "__main__":
    main()

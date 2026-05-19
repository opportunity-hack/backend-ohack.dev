#!/usr/bin/env python3
"""
Backfill team.devpost_link and winning-team status on a hackathon from Devpost.

DRY-RUN BY DEFAULT. Pass --apply to write to Firestore.

What it does
------------
Given a Devpost event URL (e.g. https://opportunity-hack-2025-arizona.devpost.com/)
and a matching hackathon event_id:

  1. Scrape <devpost-url>/project-gallery, collect EVERY tile and flag the ones
     carrying the orange "WINNER" ribbon (img.winner inside aside.entry-badge).
  2. Match each project to a Firestore team in this event (see strategies
     below). For matched teams whose devpost_link is empty, plan to set it
     (this is the bulk backfill for old hackathons that never linked
     submissions).
  3. For matched WINNERS, additionally fetch /software/<slug> to extract:
       - the prize strings under "Submitted to" (span.winner + free text)
       - the team member names under <section id="app-team">
     Map prize text -> team status enum:
       "... 1st place"           -> FOUNDING_ENGINEERS
       "... Completion / 2nd ..." -> COMPLETION_SUPPORT
       anything else marked Winner -> CATEGORY_WINNER
     Multiple prizes are kept verbatim in `awards: []`. `status` is set to the
     best (lowest-rank) status across all prizes the team won.

Match strategies (layered)
--------------------------
  (a) teams.devpost_link == project_url   (strongest signal)
  (b) teams.name ~= project title         (case-insensitive, normalized)
  (c) email overlap via Devpost projects CSV:
        project title -> CSV row -> member emails -> user docs ->
        team in this event whose users[] contains those user docs.
  Tie-breaker for (b): if multiple teams share a name, fall back to (c).

Storage
-------
On a matched team doc:
  devpost_link      = project URL (only set when previously empty;
                      conflicts logged, never overwritten)
  status            = best status across all prizes won (winners only)
  awards            = list[str] of prize text from Devpost (winners only)
  winners_backfilled_at     = ISO timestamp (winners only)
  winners_backfilled_source = "scripts/backfill_devpost_winners.py" (winners only)

No other fields are touched. team.users[] is never modified by this script.

Unmatched winners
-----------------
Logged and skipped. The script exits with code 2 when there are unmatched
winning projects so a human (or CI) notices.

Unmatched non-winners are listed for visibility (these typically represent
teams that registered only on Devpost and never on ohack.dev) but do NOT
trigger a non-zero exit.

Usage
-----
  cd backend-ohack.dev

  # Dry run (read-only); prints the plan
  python scripts/backfill_devpost_winners.py \
      --event-id 2025_fall_az \
      --devpost-url https://opportunity-hack-2025-arizona.devpost.com/

  # Optional: point at a specific projects CSV (otherwise auto-detected
  # from /tmp/devpost_files/<event-id>/projects-*.csv)
  python scripts/backfill_devpost_winners.py \
      --event-id 2024_fall \
      --devpost-url https://opportunity-hack-2024-arizona.devpost.com/ \
      --projects-csv /tmp/devpost_files/2024_fall/projects-....csv

  # Write to Firestore
  python scripts/backfill_devpost_winners.py ... --apply

Re-running is safe: writes are idempotent (set with merge=True) and only touch
status/awards/devpost_link/two metadata fields.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.utils.firebase import get_db


HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 ohack-backfill-script"}
HTTP_DELAY_SEC = 0.5  # polite delay between fetches

# Frontend source of truth: frontend-ohack.dev/src/constants/teamStatus.js
STATUS_FOUNDING_ENGINEERS = "FOUNDING_ENGINEERS"
STATUS_COMPLETION_SUPPORT = "COMPLETION_SUPPORT"
STATUS_CATEGORY_WINNER    = "CATEGORY_WINNER"

# Lower rank wins when a team has multiple prizes.
STATUS_RANK = {
    STATUS_FOUNDING_ENGINEERS: 1,
    STATUS_COMPLETION_SUPPORT: 2,
    STATUS_CATEGORY_WINNER:    3,
}


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def norm_email(e):
    return (e or "").strip().lower()


def prize_text_to_status(prize_text):
    s = (prize_text or "").lower()
    if re.search(r"\b1st\s+place\b|\bfirst\s+place\b", s):
        return STATUS_FOUNDING_ENGINEERS
    if "completion" in s or re.search(r"\b2nd\s+place\b|\bsecond\s+place\b", s):
        return STATUS_COMPLETION_SUPPORT
    return STATUS_CATEGORY_WINNER


def best_status(prizes):
    statuses = [prize_text_to_status(p) for p in prizes if p]
    if not statuses:
        return None
    return min(statuses, key=lambda s: STATUS_RANK[s])


# --------------------------- Scraping ---------------------------

def fetch_html(url):
    time.sleep(HTTP_DELAY_SEC)
    r = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _gallery_url(devpost_url):
    base = devpost_url.rstrip("/")
    if base.endswith("/project-gallery"):
        return base
    return base + "/project-gallery"


def scrape_gallery_projects(devpost_url):
    """Walk the project gallery and return EVERY project tile.

    Returns: list of {"title": str, "project_url": str, "is_winner": bool}
    """
    gallery_url = _gallery_url(devpost_url)
    projects = []
    seen = set()
    page = 1
    while True:
        url = gallery_url if page == 1 else f"{gallery_url}?page={page}"
        print(f"  GET {url}")
        soup = BeautifulSoup(fetch_html(url), "html.parser")
        tiles = soup.select("div.gallery-item")
        if not tiles:
            break
        for tile in tiles:
            link = tile.select_one("a.link-to-software")
            if not link or not link.get("href"):
                continue
            project_url = link["href"].strip()
            if project_url in seen:
                continue
            seen.add(project_url)
            h5 = tile.select_one("figcaption h5")
            title = h5.get_text(strip=True) if h5 else ""
            is_winner = tile.select_one("aside.entry-badge img.winner") is not None
            projects.append({
                "title": title,
                "project_url": project_url,
                "is_winner": is_winner,
            })
        # Pagination: only continue if a rel=next or .next_page link exists.
        next_link = soup.select_one("a[rel='next'], a.next_page")
        if not next_link:
            break
        page += 1
    return projects


def scrape_project(project_url):
    """Return {prizes: [str], members: [{name, profile_url}]} for a Devpost project page."""
    soup = BeautifulSoup(fetch_html(project_url), "html.parser")

    prizes = []
    submissions_div = soup.find("div", id="submissions")
    if submissions_div:
        # Each prize is an <li> containing <span class="winner ...">Winner</span>
        # followed by free text like "Founding Engineer Prize (Website Redesign 1st place)".
        for li in submissions_div.select("ul.no-bullet > li"):
            winner_span = li.find("span", class_="winner")
            if not winner_span:
                continue
            full = li.get_text(" ", strip=True)
            # Strip the leading "Winner" label that came from the span.
            prize_text = re.sub(r"^\s*Winner\s+", "", full, flags=re.I).strip()
            if prize_text:
                prizes.append(prize_text)

    members = []
    team_section = soup.find("section", id="app-team")
    if team_section:
        for li in team_section.select("li.software-team-member"):
            # Two anchors per member (avatar + name); pick the one with text.
            name_link = None
            for a in li.select("a.user-profile-link"):
                text = a.get_text(strip=True)
                if text:
                    name_link = a
                    break
            if not name_link:
                continue
            members.append({
                "name": name_link.get_text(strip=True),
                "profile_url": (name_link.get("href") or "").strip(),
            })

    return {"prizes": prizes, "members": members}


# --------------------------- CSV ingestion ---------------------------

def parse_projects_csv(csv_path):
    """Parse a Devpost projects CSV using header-based column resolution.

    Returns list of {title, submission_url, emails: [str], members: [{first, last, email}]}
    """
    out = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r)

        def col(name):
            try:
                return header.index(name)
            except ValueError:
                return -1

        i_title = col("Project Title")
        i_url   = col("Submission Url")
        i_sf    = col("Submitter First Name")
        i_sl    = col("Submitter Last Name")
        i_se    = col("Submitter Email")
        i_tm1f  = col("Team Member 1 First Name")
        if i_title < 0 or i_se < 0 or i_tm1f < 0:
            raise SystemExit(
                f"projects CSV is missing required columns "
                f"(Project Title / Submitter Email / Team Member 1 First Name). "
                f"Header: {header}"
            )

        for row in r:
            if not row:
                continue
            title = (row[i_title] if i_title < len(row) else "").strip()
            if not title:
                continue
            url   = (row[i_url] if 0 <= i_url < len(row) else "").strip()
            sf = (row[i_sf] if 0 <= i_sf < len(row) else "").strip()
            sl = (row[i_sl] if 0 <= i_sl < len(row) else "").strip()
            se = norm_email(row[i_se] if i_se < len(row) else "")
            members = []
            if se:
                members.append({"first": sf, "last": sl, "email": se})
            tail = row[i_tm1f:]
            for i in range(0, len(tail), 3):
                if i + 2 >= len(tail):
                    break
                f_ = (tail[i] or "").strip()
                l_ = (tail[i + 1] or "").strip()
                e_ = norm_email(tail[i + 2])
                if e_:
                    members.append({"first": f_, "last": l_, "email": e_})
            emails = sorted({m["email"] for m in members if m["email"]})
            out.append({
                "title": title,
                "submission_url": url,
                "members": members,
                "emails": emails,
            })
    return out


def auto_find_projects_csv(event_id):
    folder = os.path.join("/tmp/devpost_files", event_id)
    if not os.path.isdir(folder):
        return None
    candidates = [n for n in os.listdir(folder)
                  if n.startswith("projects-") and n.endswith(".csv")]
    if not candidates:
        return None
    return os.path.join(folder, sorted(candidates)[0])


# --------------------------- Firestore ---------------------------

def load_hackathon_and_teams(db, event_id):
    docs = list(db.collection("hackathons").where("event_id", "==", event_id).stream())
    if not docs:
        raise SystemExit(f"hackathon with event_id={event_id!r} not found")
    snap = docs[0]
    team_refs = (snap.to_dict() or {}).get("teams") or []
    teams = []
    if team_refs:
        team_docs = db.get_all(team_refs)
        for ref, doc in zip(team_refs, team_docs):
            if not doc.exists:
                continue
            teams.append({"id": doc.id, "ref": ref, "data": doc.to_dict() or {}})
    return snap, teams


def load_users_by_emails(db, emails):
    """Return {email_lower: user_doc_id} for any users found in the users/ collection."""
    from google.cloud.firestore import FieldFilter
    out = {}
    unique = sorted({e for e in emails if e})
    if not unique:
        return out
    CHUNK = 30
    for i in range(0, len(unique), CHUNK):
        chunk = unique[i:i + CHUNK]
        try:
            docs = db.collection("users").where(
                filter=FieldFilter("email_address", "in", chunk)
            ).stream()
            for d in docs:
                ea = norm_email((d.to_dict() or {}).get("email_address"))
                if ea:
                    out[ea] = d.id
        except Exception:
            for e in chunk:
                docs = list(db.collection("users").where(
                    filter=FieldFilter("email_address", "==", e)
                ).stream())
                if docs:
                    out[e] = docs[0].id
    return out


# --------------------------- Matching ---------------------------

def match_team(winner, teams, teams_by_link, teams_by_name, csv_index, email_to_user):
    url = winner["project_url"]
    title_norm = norm(winner["title"])

    if url in teams_by_link:
        return teams_by_link[url], f"devpost_link exact match ({url})"

    if title_norm and title_norm in teams_by_name:
        candidates = teams_by_name[title_norm]
        if len(candidates) == 1:
            return candidates[0], f"team name match {winner['title']!r}"
        # multiple teams with the same name in the same event: fall through to email overlap

    if csv_index:
        csv_row = csv_index.get(title_norm)
        if csv_row:
            user_doc_ids = {
                email_to_user[e] for e in csv_row["emails"] if e in email_to_user
            }
            if user_doc_ids:
                best_team = None
                best_overlap = 0
                for t in teams:
                    team_user_ids = {
                        u.id for u in (t["data"].get("users") or [])
                        if hasattr(u, "id")
                    }
                    overlap = len(team_user_ids & user_doc_ids)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_team = t
                if best_team and best_overlap > 0:
                    return best_team, (
                        f"email overlap via CSV "
                        f"({best_overlap}/{len(user_doc_ids)} member doc IDs matched "
                        f"team {best_team['data'].get('name')!r})"
                    )

    return None, "no devpost_link / team name / email-overlap match"


# --------------------------- Main ---------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Backfill winning-team status on a hackathon from Devpost.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--event-id", required=True,
                    help="hackathons.event_id (e.g. 2025_fall_az)")
    ap.add_argument("--devpost-url", required=True,
                    help="Devpost event base URL (e.g. https://opportunity-hack-2025-arizona.devpost.com/)")
    ap.add_argument("--projects-csv",
                    help="Optional explicit path. Auto-detected from /tmp/devpost_files/<event-id>/projects-*.csv if omitted.")
    ap.add_argument("--apply", action="store_true",
                    help="Write to Firestore. Default is dry-run.")
    args = ap.parse_args()

    csv_path = args.projects_csv or auto_find_projects_csv(args.event_id)
    if csv_path and not os.path.isfile(csv_path):
        raise SystemExit(f"--projects-csv not found: {csv_path}")
    if csv_path:
        print(f"Using projects CSV: {csv_path}")
    else:
        print(f"No projects CSV under /tmp/devpost_files/{args.event_id}/; "
              f"email-overlap fallback disabled.")

    db = get_db()
    hackathon_snap, teams = load_hackathon_and_teams(db, args.event_id)
    print(f"Hackathon: doc_id={hackathon_snap.id}  teams={len(teams)}")

    teams_by_link = {}
    teams_by_name = defaultdict(list)
    for t in teams:
        link = (t["data"].get("devpost_link") or "").strip()
        if link:
            teams_by_link[link] = t
        nm = norm(t["data"].get("name") or "")
        if nm:
            teams_by_name[nm].append(t)

    csv_index = None
    email_to_user = {}
    if csv_path:
        csv_rows = parse_projects_csv(csv_path)
        csv_index = {norm(r["title"]): r for r in csv_rows}
        all_emails = sorted({e for r in csv_rows for e in r["emails"]})
        print(f"CSV: {len(csv_rows)} project row(s), {len(all_emails)} unique member email(s)")
        email_to_user = load_users_by_emails(db, all_emails)
        print(f"  -> {len(email_to_user)} email(s) already linked to a user doc")

    print(f"\nScraping gallery {args.devpost_url}")
    projects = scrape_gallery_projects(args.devpost_url)
    winner_count = sum(1 for p in projects if p["is_winner"])
    print(f"Found {len(projects)} project(s) in gallery  ({winner_count} winner(s))")

    # Three planning buckets:
    #   winner_plans       - matched winners: set status + awards + devpost_link
    #   link_only_plans    - matched non-winners with empty devpost_link: set link only
    #   unmatched_winners  - winners we can't match (exit non-zero)
    #   unmatched_others   - non-winners we can't match (informational only)
    #   link_conflicts     - team already has a devpost_link that points elsewhere
    winner_plans = []
    link_only_plans = []
    link_already_set = []
    link_conflicts = []
    unmatched_winners = []
    unmatched_others = []

    for proj in projects:
        team, reason = match_team(
            proj, teams, teams_by_link, teams_by_name, csv_index, email_to_user
        )
        if not team:
            (unmatched_winners if proj["is_winner"] else unmatched_others).append(
                {"project": proj, "reason": reason}
            )
            continue

        cur_link = (team["data"].get("devpost_link") or "").strip()
        link_status = None
        if not cur_link:
            link_status = "set"
        elif cur_link == proj["project_url"]:
            link_status = "already_matches"
        else:
            link_status = "conflict"

        if proj["is_winner"]:
            print(f"\n  Fetching {proj['project_url']}  ({proj['title']!r})")
            details = scrape_project(proj["project_url"])
            if not details["prizes"]:
                print(f"    WARN: gallery marks WINNER but no prize text on project page; "
                      f"defaulting status to CATEGORY_WINNER")
                details["prizes"] = ["(unknown — gallery WINNER, no prize text found)"]
            status = best_status(details["prizes"]) or STATUS_CATEGORY_WINNER
            winner_plans.append({
                "project": proj,
                "team": team,
                "new_status": status,
                "awards": details["prizes"],
                "members": details["members"],
                "reason": reason,
                "link_status": link_status,
            })
        else:
            if link_status == "set":
                link_only_plans.append({"project": proj, "team": team, "reason": reason})
            elif link_status == "already_matches":
                link_already_set.append({"project": proj, "team": team})
            else:
                link_conflicts.append({
                    "project": proj, "team": team,
                    "existing_link": cur_link, "reason": reason,
                })

    sep = "=" * 78
    print("\n" + sep)
    print(f"PLAN ({'APPLY' if args.apply else 'DRY-RUN'})  event_id={args.event_id}")
    print(sep)

    print(f"\n[WINNERS] {len(winner_plans)} status update(s) planned")
    for p in winner_plans:
        t = p["team"]
        cur_status = t["data"].get("status") or "(none)"
        cur_link = (t["data"].get("devpost_link") or "").strip()
        print(f"\n  {p['project']['title']!r}")
        print(f"    team:    {t['id']}  name={t['data'].get('name')!r}")
        print(f"    match:   {p['reason']}")
        print(f"    devpost members: {[m['name'] for m in p['members']]}")
        print(f"    prizes:")
        for pr in p["awards"]:
            print(f"      - {pr}")
        print(f"    status:  {cur_status!r} -> {p['new_status']!r}")
        if not cur_link:
            print(f"    devpost_link: (empty) -> {p['project']['project_url']!r}")
        elif cur_link != p["project"]["project_url"]:
            print(f"    devpost_link: {cur_link!r} (keeping; differs from {p['project']['project_url']!r})")

    print(f"\n[DEVPOST_LINK BACKFILL] {len(link_only_plans)} team(s) will get devpost_link set")
    for p in link_only_plans:
        t = p["team"]
        print(f"  - teams/{t['id']}  {t['data'].get('name')!r}")
        print(f"      -> {p['project']['project_url']}  ({p['reason']})")

    if link_already_set:
        print(f"\n[LINK ALREADY CORRECT] {len(link_already_set)} team(s) - no change needed")

    if link_conflicts:
        print(f"\n[LINK CONFLICTS] {len(link_conflicts)} team(s) have a different devpost_link already set (NOT overwriting)")
        for c in link_conflicts:
            t = c["team"]
            print(f"  - teams/{t['id']}  {t['data'].get('name')!r}")
            print(f"      existing: {c['existing_link']}")
            print(f"      gallery:  {c['project']['project_url']}  ({c['reason']})")

    print(f"\n[UNMATCHED WINNERS] {len(unmatched_winners)}")
    for u in unmatched_winners:
        p = u["project"]
        print(f"  - {p['title']!r}  {p['project_url']}")
        print(f"      reason: {u['reason']}")

    print(f"\n[UNMATCHED NON-WINNERS] {len(unmatched_others)} (informational — these teams likely never registered on ohack.dev)")
    for u in unmatched_others:
        p = u["project"]
        print(f"  - {p['title']!r}  {p['project_url']}")

    print(
        f"\nSummary: gallery={len(projects)}  winners={winner_count}  "
        f"winner_status_updates={len(winner_plans)}  "
        f"link_backfills={len(link_only_plans)}  "
        f"link_conflicts={len(link_conflicts)}  "
        f"unmatched_winners={len(unmatched_winners)}  "
        f"unmatched_non_winners={len(unmatched_others)}"
    )

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to write to Firestore.")
        sys.exit(2 if unmatched_winners else 0)

    print("\nApplying writes ...")
    now_iso = datetime.now(timezone.utc).isoformat()
    for p in winner_plans:
        t = p["team"]
        update = {
            "status": p["new_status"],
            "awards": p["awards"],
            "winners_backfilled_at": now_iso,
            "winners_backfilled_source": "scripts/backfill_devpost_winners.py",
        }
        if not (t["data"].get("devpost_link") or "").strip():
            update["devpost_link"] = p["project"]["project_url"]
        db.collection("teams").document(t["id"]).set(update, merge=True)
        print(f"  wrote teams/{t['id']}  status={p['new_status']!r}  awards={len(p['awards'])}")

    for p in link_only_plans:
        t = p["team"]
        db.collection("teams").document(t["id"]).set(
            {"devpost_link": p["project"]["project_url"]},
            merge=True,
        )
        print(f"  wrote teams/{t['id']}  devpost_link={p['project']['project_url']}")

    if unmatched_winners:
        print(f"\nWARNING: {len(unmatched_winners)} unmatched winner(s) - see list above.")
        sys.exit(2)
    print("\nDone.")


if __name__ == "__main__":
    main()

"""Render one sample participation letter per role from fixture facts.

No Firestore, no network: the facts below are what `build_facts` would
snapshot. Use it to eyeball the layout after touching api/letters/letter_pdf.py.

    python scripts/render_sample_letters.py              # -> /tmp/ohack-letters
    python scripts/render_sample_letters.py --out DIR --preview
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.letters.letter_pdf import page_count, render_pdf  # noqa: E402

BASE = {
    "template_version": 1,
    "event_id": "2026_fall",
    "event_title": "Opportunity Hack Fall 2026",
    "season_label": "FALL 2026",
    "city_state": "Tempe, Arizona",
    "venue": "ASU Tempe Campus",
    "attendance": "in_person",
    "served_dates": ["2026-11-14", "2026-11-15"],
    "served_date": "November 14–15, 2026",
    "n_teams": None, "nonprofits": [], "mentor_teams": [],
    "team_name": None, "project_title": None, "nonprofit_name": None, "awards": [],
}

SAMPLES = {
    "judge": dict(BASE, role="judge", name="Dr. Amélie Núñez-Okafor",
                  title_line="Principal Engineer, Example Corp",
                  served_dates=["2026-11-15"], served_date="November 15, 2026",
                  n_teams=6, nonprofits=["Matthew's Crossing Food Bank", "Zuri's Circle",
                                         "NMTSA", "Will2Walk"]),
    "mentor": dict(BASE, role="mentor", name="Kenji Watanabe 渡辺健二",
                   mentor_teams=["Byte Bandits", "Null Pointers"]),
    "volunteer": dict(BASE, role="volunteer", name="Jordan Lee", attendance=None, venue=None),
    "hacker": dict(BASE, role="hacker", name="Priya Raman",
                   title_line="Student, Arizona State University",
                   team_name="Byte Bandits", project_title="Volunteer Shift Scheduler",
                   nonprofit_name="Matthew's Crossing Food Bank", awards=["1st Place"]),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/ohack-letters")
    parser.add_argument("--preview", action="store_true", help="also render watermarked previews")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for role, facts in SAMPLES.items():
        variants = [("", False)] + ([("-preview", True)] if args.preview else [])
        for suffix, preview in variants:
            pdf = render_pdf(facts, letter_id=None if preview else "7KQ2M4XBNP3R",
                             issued_on=date(2026, 11, 27), preview=preview)
            path = os.path.join(args.out, f"letter-{role}{suffix}.pdf")
            with open(path, "wb") as fh:
                fh.write(pdf)
            print(f"{path}  {len(pdf)} bytes  {page_count(pdf)} page(s)")


if __name__ == "__main__":
    main()

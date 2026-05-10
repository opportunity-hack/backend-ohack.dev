"""Seed the OHack default planning board template."""
import logging
from datetime import datetime, timezone

from common.utils.firebase import get_db

logger = logging.getLogger("planning_template_service")

# ---------------------------------------------------------------------------
# OHack default template — 8 lists with seed cards
# ---------------------------------------------------------------------------

OHACK_PLANNING_TEMPLATE = [
    {
        "title": "Nonprofits & Problems",
        "is_run_of_show": False,
        "cards": [
            {"title": "Confirm 6–8 nonprofit problem statements", "kind": "nonprofits", "target_count": 8},
            {"title": "Schedule nonprofit kickoff calls"},
            {"title": "Publish problem briefs on ohack.dev"},
        ],
    },
    {
        "title": "Sponsors & Budget",
        "is_run_of_show": False,
        "cards": [
            {"title": "Set fundraising goal"},
            {"title": "Refresh sponsor deck"},
            {"title": "Send sponsor outreach"},
        ],
    },
    {
        "title": "Venue & Logistics",
        "is_run_of_show": False,
        "cards": [
            {"title": "Lock venue and dates"},
            {"title": "Order food (per meal slot)"},
            {"title": "AV / wifi / power"},
        ],
    },
    {
        "title": "Volunteers & Mentors",
        "is_run_of_show": False,
        "cards": [
            {"title": "Open mentor applications", "kind": "mentors", "target_count": 20},
            {"title": "Mentor schedule"},
            {"title": "Volunteer task list"},
        ],
    },
    {
        "title": "Judges & Prizes",
        "is_run_of_show": False,
        "cards": [
            {"title": "Recruit judges", "kind": "judges", "target_count": 15},
            {"title": "Define judging rubric"},
            {"title": "Order prizes"},
        ],
    },
    {
        "title": "Marketing & Comms",
        "is_run_of_show": False,
        "cards": [
            {"title": "Landing page copy"},
            {"title": "Slack channel and invites"},
            {"title": "Social media schedule"},
        ],
    },
    {
        "title": "Run of Show",
        "is_run_of_show": True,
        "cards": [
            {"title": "Doors Open", "start_time": "08:00", "sync_to_countdowns": True},
            {"title": "Kickoff", "start_time": "09:00", "sync_to_countdowns": True},
            {"title": "Nonprofit Pitches", "start_time": "09:30", "sync_to_countdowns": True},
            {"title": "Team Formation", "start_time": "10:00", "sync_to_countdowns": True},
            {"title": "Hacking Begins", "start_time": "10:30", "sync_to_countdowns": True, "kind": "hackers", "target_count": 90},
            {"title": "Lunch (Day 1)", "start_time": "12:00", "sync_to_countdowns": True},
            {"title": "Breakfast (Day 2)", "start_time": "07:00", "sync_to_countdowns": True},
            {"title": "Hacking Ends", "start_time": "15:00", "sync_to_countdowns": True},
            {"title": "Judging Begins", "start_time": "15:05", "sync_to_countdowns": True},
            {"title": "Winners Announced", "start_time": "17:30", "sync_to_countdowns": True},
        ],
    },
    {
        "title": "Post-Event",
        "is_run_of_show": False,
        "cards": [
            {"title": "Winners announcement"},
            {"title": "Thank-you emails to sponsors and nonprofits"},
            {"title": "Retrospective and lessons learned"},
        ],
    },
]


def apply_ohack_template(hackathon_doc: dict) -> None:
    """Write the default template lists/cards into the given hackathon's subcollections."""
    db = get_db()
    href = db.collection("hackathons").document(hackathon_doc["id"])

    now = datetime.now(timezone.utc).isoformat()

    for list_position, list_template in enumerate(OHACK_PLANNING_TEMPLATE):
        position = f"p{list_position:04d}"
        list_ref = href.collection("planning_lists").document()
        list_ref.set({
            "title": list_template["title"],
            "position": position,
            "archived": False,
            "is_run_of_show": list_template.get("is_run_of_show", False),
            "created_at": now,
            "updated_at": now,
        })

        for card_position, card_template in enumerate(list_template.get("cards", [])):
            card_position_str = f"p{card_position:04d}"
            card_ref = href.collection("planning_cards").document()
            card_ref.set({
                "list_id": list_ref.id,
                "title": card_template["title"],
                "description": "",
                "kind": card_template.get("kind", "freetext"),
                "assignees": [],
                "labels": [],
                "due_date": None,
                "position": card_position_str,
                "archived": False,
                "checklists": [],
                "attachments": [],
                "comment_count": 0,
                "created_by": "system",
                "created_at": now,
                "updated_at": now,
                "last_activity_at": now,
                "start_time": card_template.get("start_time"),
                "end_time": None,
                "sync_to_countdowns": card_template.get("sync_to_countdowns", False),
                "budget": None,
                "target_count": card_template.get("target_count"),
                "sponsor": None,
            })

    logger.info("Applied OHack default template to hackathon %s", hackathon_doc.get("event_id"))

"""Run of Show sync: planning board cards → hackathon.countdowns array.

One-way sync only: board is the source of truth for is_run_of_show lists.
Existing countdowns without source="planning" are preserved as-is.
"""
import logging
from datetime import datetime, timezone

from common.utils.firebase import get_db

logger = logging.getLogger("planning_ros_service")

SOURCE_PLANNING = "planning"
SOURCE_MANUAL = "manual"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ros_cards(hackathon_doc: dict) -> list:
    """Return cards from is_run_of_show lists with sync_to_countdowns=True, sorted by start_time."""
    db = get_db()
    href = db.collection("hackathons").document(hackathon_doc["id"])

    ros_lists = [
        d.id
        for d in href.collection("planning_lists")
        .where("is_run_of_show", "==", True)
        .where("archived", "==", False)
        .stream()
    ]

    cards = []
    for list_id in ros_lists:
        for card_doc in (
            href.collection("planning_cards")
            .where("list_id", "==", list_id)
            .where("archived", "==", False)
            .stream()
        ):
            data = {**card_doc.to_dict(), "id": card_doc.id}
            if data.get("sync_to_countdowns") and data.get("start_time"):
                cards.append(data)

    cards.sort(key=lambda c: c.get("start_time", ""))
    return cards


def _cards_to_countdowns(ros_cards: list) -> list:
    return [
        {
            "name": card["title"],
            "description": card.get("description") or "",
            "time": card["start_time"],
            "source": SOURCE_PLANNING,
            "card_id": card["id"],
        }
        for card in ros_cards
    ]


def compute_ros_diff(hackathon_doc: dict) -> dict:
    """Return a preview diff of what the sync would do."""
    ros_cards = _get_ros_cards(hackathon_doc)
    incoming = _cards_to_countdowns(ros_cards)

    existing_countdowns = hackathon_doc.get("countdowns") or []
    preserved = [c for c in existing_countdowns if c.get("source") != SOURCE_PLANNING]
    current_planning = [c for c in existing_countdowns if c.get("source") == SOURCE_PLANNING]

    return {
        "incoming_count": len(incoming),
        "preserved_count": len(preserved),
        "current_planning_count": len(current_planning),
        "incoming": incoming,
        "preserved": preserved,
        "merged": sorted(incoming + preserved, key=lambda c: c.get("time", "")),
    }


def sync_ros_to_countdowns(hackathon_doc: dict, actor_id: str) -> dict:
    """Apply the sync: merge incoming planning entries with preserved manual entries."""
    diff = compute_ros_diff(hackathon_doc)
    merged = diff["merged"]

    db = get_db()
    href = db.collection("hackathons").document(hackathon_doc["id"])
    href.update({"countdowns": merged})

    logger.info(
        "RoS sync: %d planning + %d preserved = %d total for %s (by %s)",
        diff["incoming_count"],
        diff["preserved_count"],
        len(merged),
        hackathon_doc.get("event_id"),
        actor_id,
    )
    return {
        "synced": diff["incoming_count"],
        "preserved": diff["preserved_count"],
        "total": len(merged),
    }

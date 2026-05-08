"""Slack digest for the planning board.

Digest delivery is done without a dedicated cron job via three layered mechanisms:
1. Lazy flush: the next mutation after the 60s window reads and sends the prior window.
2. Read-driven flush: every GET /api/planning/{event_id} checks and flushes if past deadline.
3. Optional external cron: POST /api/planning/_flush_digests (authenticated by X-Internal-Token).

Without Redis or external cron, digests fire on the next page load after the window closes.
"""
import logging
from datetime import datetime, timezone, timedelta

from common.utils.firebase import get_db

logger = logging.getLogger("planning_slack_notifier")

DIGEST_WINDOW_SECONDS = 60
DIGEST_DEADLINE_FIELD = "planning_digest_deadline"
MAX_DIGEST_LINES = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _get_deadline_key(hackathon_id: str) -> str:
    return f"planning:digest_deadline:{hackathon_id}"


def _get_or_set_deadline(hackathon_doc: dict) -> datetime | None:
    """Return the current digest deadline, or None if no pending events exist."""
    hid = hackathon_doc["id"]

    # Try Redis first (fast path)
    try:
        from common.utils.redis_cache import get_redis_client
        rc = get_redis_client()
        if rc:
            key = _get_deadline_key(hid)
            raw = rc.get(key)
            if raw:
                return datetime.fromisoformat(raw.decode())
            # No deadline in Redis — set it
            deadline = _now() + timedelta(seconds=DIGEST_WINDOW_SECONDS)
            rc.set(key, deadline.isoformat(), ex=DIGEST_WINDOW_SECONDS + 30)
            return deadline
    except Exception:
        pass

    # Fallback: read/write deadline on the hackathon doc
    db = get_db()
    href = db.collection("hackathons").document(hid)
    snap = href.get()
    data = snap.to_dict() or {}
    existing_deadline = data.get(DIGEST_DEADLINE_FIELD)
    if existing_deadline:
        try:
            return datetime.fromisoformat(existing_deadline)
        except ValueError:
            pass

    deadline = _now() + timedelta(seconds=DIGEST_WINDOW_SECONDS)
    href.update({DIGEST_DEADLINE_FIELD: deadline.isoformat()})
    return deadline


def _clear_deadline(hackathon_doc: dict) -> None:
    hid = hackathon_doc["id"]
    try:
        from common.utils.redis_cache import get_redis_client
        rc = get_redis_client()
        if rc:
            rc.delete(_get_deadline_key(hid))
    except Exception:
        pass
    db = get_db()
    db.collection("hackathons").document(hid).update({DIGEST_DEADLINE_FIELD: None})


def _collect_and_clear_events(hackathon_doc: dict) -> list:
    """Read and delete all pending digest events, returning them sorted by created_at."""
    db = get_db()
    href = db.collection("hackathons").document(hackathon_doc["id"])
    pending_ref = href.collection("planning_pending_digests")
    docs = list(pending_ref.order_by("created_at").stream())
    events = [{**d.to_dict(), "_doc_id": d.id} for d in docs]

    batch = db.batch()
    for d in docs:
        batch.delete(pending_ref.document(d.id))
    batch.commit()

    return events


def _coalesce_events(events: list) -> list:
    """Deduplicate (card_id, kind) groups — keep last event per group."""
    seen = {}
    for ev in events:
        key = (ev.get("card_id", ""), ev.get("kind", ""))
        seen[key] = ev
    # Sort back by created_at
    return sorted(seen.values(), key=lambda e: e.get("created_at", ""))


KIND_TEMPLATES = {
    "card_created": "• {actor} added card \"{card_title}\"",
    "card_updated": "• {actor} updated \"{card_title}\"",
    "card_archived": "• {actor} archived \"{card_title}\"",
    "comment_added": "• New comment on \"{card_title}\"",
    "list_created": "• Created list \"{list_title}\"",
    "ros_synced": "• Run of Show synced to public timeline ({count} entries)",
}


def format_planning_digest(events: list, hackathon_doc: dict) -> str:
    title = hackathon_doc.get("title", "Planning board")
    lines = [f"📋 *{title}* — last 60s"]

    coalesced = _coalesce_events(events)[:MAX_DIGEST_LINES]
    overflow = len(_coalesce_events(events)) - MAX_DIGEST_LINES

    for ev in coalesced:
        kind = ev.get("kind", "")
        template = KIND_TEMPLATES.get(kind, "• Board updated")
        actor = ev.get("actor", "Someone")
        line = template.format(
            actor=actor,
            card_title=ev.get("card_title", "a card"),
            list_title=ev.get("list_title", "a list"),
            count=ev.get("count", ""),
        )
        lines.append(line)

    if overflow > 0:
        event_id = hackathon_doc.get("event_id", "")
        lines.append(f"…and {overflow} more changes <https://ohack.dev/hack/{event_id}/plan|Open board>")
    else:
        event_id = hackathon_doc.get("event_id", "")
        lines.append(f"<https://ohack.dev/hack/{event_id}/plan|Open board>")

    return "\n".join(lines)


def _do_flush(hackathon_doc: dict) -> int:
    """Flush pending events into a Slack message. Returns count of events sent."""
    events = _collect_and_clear_events(hackathon_doc)
    if not events:
        _clear_deadline(hackathon_doc)
        return 0

    planning = hackathon_doc.get("planning") or {}
    slack = planning.get("slack") or {}
    channel = slack.get("channel", "")
    if not channel:
        _clear_deadline(hackathon_doc)
        return 0

    message = format_planning_digest(events, hackathon_doc)
    try:
        from common.utils.slack import send_slack
        send_slack(message, channel=channel)
        logger.info("Planning digest sent to #%s (%d events)", channel, len(events))
    except Exception:
        logger.exception("Failed to send planning Slack digest to #%s", channel)

    _clear_deadline(hackathon_doc)
    return len(events)


def flush_digests_if_due(hackathon_doc: dict) -> None:
    """Read-driven flush — call from GET /api/planning/{event_id}."""
    planning = hackathon_doc.get("planning") or {}
    if not planning.get("notify_on_card_change", False):
        slack = planning.get("slack") or {}
        if not slack.get("notify_on_card_change"):
            return

    db = get_db()
    href = db.collection("hackathons").document(hackathon_doc["id"])
    data = href.get().to_dict() or {}
    deadline_str = data.get(DIGEST_DEADLINE_FIELD)
    if not deadline_str:
        return

    try:
        deadline = datetime.fromisoformat(deadline_str)
    except ValueError:
        return

    if _now() >= deadline:
        _do_flush(hackathon_doc)


def lazy_flush_if_due(hackathon_doc: dict) -> None:
    """Lazy flush — call at the start of any mutation route."""
    flush_digests_if_due(hackathon_doc)


def send_manual_digest(hackathon_doc: dict) -> None:
    """Admin 'Send digest now' — flushes the current board state immediately."""
    events = _collect_and_clear_events(hackathon_doc)
    _do_flush(hackathon_doc)


def flush_all_pending_digests() -> int:
    """Flush all hackathons with pending digests (called by external cron)."""
    db = get_db()
    # Find hackathons with a non-null planning_digest_deadline
    docs = (
        db.collection("hackathons")
        .where("planning_digest_deadline", "!=", None)
        .stream()
    )
    total = 0
    for doc in docs:
        data = doc.to_dict() or {}
        deadline_str = data.get(DIGEST_DEADLINE_FIELD)
        if not deadline_str:
            continue
        try:
            deadline = datetime.fromisoformat(deadline_str)
        except ValueError:
            continue
        if _now() >= deadline:
            hackathon_doc = {**data, "id": doc.id}
            total += _do_flush(hackathon_doc)
    return total

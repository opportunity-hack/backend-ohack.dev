"""Admin-side read / aggregation for the feedback-review dashboard.

The WRITE paths for these collections live elsewhere on purpose and are NOT
touched here:
  - peer-to-peer `feedback`   -> services/feedback_service.py (save_feedback)
  - `onboarding_feedbacks`    -> services/onboarding_service.py (save_onboarding_feedback)
  - event `surveys`           -> api/surveys/surveys_service.py (has its own admin reads)

This module is only the admin REVIEW side: list + light aggregate for
`/admin/feedback`. Every caller is `volunteer.admin`-gated at the view layer.
Reads are PII-light: peer feedback hides the giver when the entry is anonymous;
onboarding keeps the user-agent but drops the IP address.
"""
from typing import Any, Dict, Optional

from db.db import get_db, fetch_users
from common.log import get_logger, warning

logger = get_logger(__name__)

PEER_FEEDBACK_COLLECTION = "feedback"
ONBOARDING_COLLECTION = "onboarding_feedbacks"
DEFAULT_LIMIT = 500
# Sentinel some docs carry when seeded from a Firestore CSV export
# (see scripts/sync_hackathons_from_csv.py); strip it so timestamps parse.
_TIMESTAMP_PREFIX = "__Timestamp__"


def _user_directory() -> Dict[str, Dict[str, Any]]:
    """db_id -> light public identity, best-effort.

    One users-collection scan. The admin dashboard isn't a hot path, and
    peer-feedback giver/receiver ids are arbitrary user doc ids, so a single
    map is cheaper than N point reads.
    """
    directory: Dict[str, Dict[str, Any]] = {}
    try:
        for u in fetch_users() or []:
            uid = getattr(u, "id", None)
            if not uid:
                continue
            directory[uid] = {
                "name": getattr(u, "name", "") or getattr(u, "nickname", "") or "",
                "profile_image": getattr(u, "profile_image", None),
            }
    except Exception as e:  # pragma: no cover - defensive
        warning(logger, "feedback-admin: user directory build failed", exc_info=e)
    return directory


def _resolve(directory: Dict[str, Dict[str, Any]], db_id: Optional[str]):
    if not db_id:
        return None
    info = directory.get(db_id) or {}
    return {
        "id": db_id,
        "name": info.get("name") or "Unknown",
        "profile_image": info.get("profile_image"),
    }


def list_peer_feedback(limit: Optional[int] = None) -> Dict[str, Any]:
    """All peer-to-peer feedback, newest first, names resolved (giver hidden
    when anonymous), plus a light breakdown by relationship and role."""
    db = get_db()
    limit = limit or DEFAULT_LIMIT
    docs = list(db.collection(PEER_FEEDBACK_COLLECTION).stream())
    directory = _user_directory()

    items = []
    by_relationship: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    for doc in docs:
        d = doc.to_dict() or {}
        is_anon = bool(d.get("is_anonymous", False))
        fb = d.get("feedback") if isinstance(d.get("feedback"), dict) else {}
        role = fb.get("role")
        rel = d.get("relationship")
        if rel:
            by_relationship[rel] = by_relationship.get(rel, 0) + 1
        if role:
            by_role[role] = by_role.get(role, 0) + 1
        items.append({
            "id": doc.id,
            "receiver": _resolve(directory, d.get("feedback_receiver_id")),
            "giver": None if is_anon else _resolve(directory, d.get("feedback_giver_id")),
            "is_anonymous": is_anon,
            "relationship": rel,
            "duration": d.get("duration"),
            "confidence_level": d.get("confidence_level"),
            "role": role,
            "feedback": fb,  # nested: role + skill scores (0-100) + text fields
            "timestamp": d.get("timestamp"),
        })

    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    items = items[:limit]
    return {
        "success": True,
        "count": len(items),
        "summary": {"by_relationship": by_relationship, "by_role": by_role},
        "feedback": items,
    }


def list_onboarding_feedback(limit: Optional[int] = None) -> Dict[str, Any]:
    """All onboarding feedback, newest first, with a rating distribution.

    Drops `clientInfo.ipAddress`; keeps the user-agent for debugging dupes.
    """
    db = get_db()
    limit = limit or DEFAULT_LIMIT
    docs = list(db.collection(ONBOARDING_COLLECTION).stream())

    items = []
    rating_dist: Dict[str, int] = {}
    ease_dist: Dict[str, int] = {}
    rating_sum = 0.0
    rating_count = 0
    for doc in docs:
        d = doc.to_dict() or {}
        rating = d.get("overallRating")
        if isinstance(rating, (int, float)) and rating:  # 0 == unrated, don't count
            rating_dist[str(int(rating))] = rating_dist.get(str(int(rating)), 0) + 1
            rating_sum += rating
            rating_count += 1
        ease = d.get("easeOfUnderstanding") or ""
        if ease:
            ease_dist[ease] = ease_dist.get(ease, 0) + 1

        ts = d.get("timestamp")
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        elif isinstance(ts, str) and ts.startswith(_TIMESTAMP_PREFIX):
            # CSV-export sentinel (see scripts/sync_hackathons_from_csv.py) -> clean ISO
            ts = ts[len(_TIMESTAMP_PREFIX):]

        # contactForFollowup is {"willing": False} or {"willing": True, "firstName", "email"}
        contact = d.get("contactForFollowup") or {}
        client = d.get("clientInfo") or {}
        items.append({
            "id": doc.id,
            "overallRating": rating,
            "usefulTopics": d.get("usefulTopics") or [],
            "missingTopics": d.get("missingTopics") or "",
            "easeOfUnderstanding": ease,
            "improvements": d.get("improvements") or "",
            "additionalFeedback": d.get("additionalFeedback") or "",
            "contact": {
                "willing": bool(contact.get("willing")),
                "firstName": contact.get("firstName") or contact.get("name") or "",
                "email": contact.get("email") or "",
            },
            "userAgent": client.get("userAgent") or "",  # IP intentionally dropped
            "timestamp": ts,
        })

    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    items = items[:limit]
    avg = round(rating_sum / rating_count, 2) if rating_count else None
    return {
        "success": True,
        "count": len(items),
        "summary": {
            "rating_distribution": rating_dist,
            "ease_distribution": ease_dist,
            "overall_rating": {"count": rating_count, "average": avg},
        },
        "onboarding_feedback": items,
    }

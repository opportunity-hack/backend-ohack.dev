"""Re-match an event's applicants to the Slack workspace.

Lives outside api/slack so it can be imported (and tested in CI) without
api/slack/__init__ pulling in common.auth, which needs a real PropelAuth URL.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from common.log import get_logger, info, warning, error
from common.utils.slack import userlist
from common.utils.redis_cache import clear_pattern
from common.utils.oauth_providers import is_slack_user_id, extract_slack_user_id
from common.utils.firebase import get_db, get_users_by_emails

logger = get_logger(__name__)

VOLUNTEER_TYPES = ("mentor", "judge", "volunteer", "hacker", "sponsor")


class SlackUnavailableError(Exception):
    """The Slack member list could not be read, so nothing can be concluded."""


def _clear_volunteer_list_cache() -> None:
    """Drop the admin volunteer-list cache so refreshed rows show at once."""
    # Lazy import: hackathons_service pulls in most of the app.
    from services.hackathons_service import get_volunteer_by_event
    get_volunteer_by_event.cache_clear()


def _users_by_propel_ids(db, propel_ids) -> Dict[str, Dict[str, Any]]:
    """Batch `users` lookup by propel_id ('in' queries, 30 per chunk).

    Batched on purpose: one query per unmatched applicant made the refresh
    take seconds per hundred people, every time the invite dialog opened.
    """
    found: Dict[str, Dict[str, Any]] = {}
    unique = sorted({p for p in propel_ids if p})
    for i in range(0, len(unique), 30):
        chunk = unique[i:i + 30]
        try:
            # Positional form: works on Firestore and on MockFirestore (tests).
            docs = db.collection("users").where("propel_id", "in", chunk).stream()
            for doc in docs:
                user = doc.to_dict() or {}
                if user.get("propel_id"):
                    found[user["propel_id"]] = user
        except Exception as e:
            warning(logger, "users lookup by propel ids failed", error=str(e))
    return found


def _is_real_member(member: Dict[str, Any]) -> bool:
    """Same filter as get_active_users: no bots, deleted or guest accounts."""
    return not (
        member.get("is_bot", False)
        or member.get("deleted", False)
        or member.get("is_restricted", False)
        or member.get("is_ultra_restricted", False)
        or member.get("name", "").startswith("slackbot")
    )


def _norm_email(value) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _slack_id_from_user_doc(user, by_email: Dict[str, str], member_ids: set) -> Optional[str]:
    """Slack id for a `users` doc (dict), or None."""
    if not user:
        return None
    user_id, email = user.get("user_id"), user.get("email_address")
    if is_slack_user_id(user_id):
        slack_id = extract_slack_user_id(user_id)
        if slack_id in member_ids:
            return slack_id
    return by_email.get(_norm_email(email))


def refresh_volunteer_slack_ids(event_id: str, volunteer_type: Optional[str] = None,
                                dry_run: bool = False) -> Dict[str, Any]:
    """
    Find applicants for an event who joined Slack AFTER they applied and save
    their Slack id on the volunteer doc.

    `slack_user_id` is otherwise only looked up when the application is first
    created, so anyone who joins Slack later stays "not on Slack" in the admin
    view and can't be invited to a channel.

    Matching, in order, against ONE fresh users.list read:
      1. `email`   - the application email is a workspace member's email
      2. `profile` - the person's `users` doc (found by email, then by the
                     PropelAuth id stored as the volunteer's `user_id`) has a
                     Slack login id or a different email that is a member's

    Docs that already have a `slack_user_id` are never touched. `dry_run`
    reports what would match without writing anything.

    Raises:
        SlackUnavailableError: the member list could not be read. Nothing is
        written - a failed read must never look like "nobody is on Slack".
    """
    # The member list is cached for 10 minutes; someone who joined a minute ago
    # must be visible, so drop the cache before this explicit refresh.
    clear_pattern("slack:userlist:*")
    try:
        response = userlist()
    except Exception as e:  # Slack API / network failure
        error(logger, "Slack member list failed during volunteer refresh", error=str(e))
        raise SlackUnavailableError(str(e)) from e
    if not response or "members" not in response:
        raise SlackUnavailableError("Slack returned no member list")

    by_email: Dict[str, str] = {}
    member_ids = set()
    for member in response["members"]:
        if not _is_real_member(member):
            continue
        member_ids.add(member["id"])
        email = _norm_email((member.get("profile") or {}).get("email"))
        if email:
            by_email[email] = member["id"]

    db = get_db()
    docs = db.collection("volunteers").where("event_id", "==", event_id).stream()
    missing = []
    checked = 0
    for doc in docs:
        data = doc.to_dict() or {}
        if volunteer_type and data.get("volunteer_type") != volunteer_type:
            continue
        checked += 1
        if str(data.get("slack_user_id") or "").strip():
            continue
        missing.append((doc.id, data))

    # One batched users lookup by application email (exact, as stored).
    users_by_email = get_users_by_emails(
        [d.get("email") for _, d in missing if d.get("email")]
    ) or {}
    # ...and one by PropelAuth id for the ones email alone can't place.
    users_by_propel = _users_by_propel_ids(db, [
        d.get("user_id") for _, d in missing
        if d.get("user_id") and not is_slack_user_id(d.get("user_id"))
        and _norm_email(d.get("email")) not in by_email
    ])

    now = datetime.utcnow().isoformat() + "Z"
    updated, unmatched = [], []
    for doc_id, data in missing:
        email = data.get("email")
        slack_id = by_email.get(_norm_email(email))
        matched_by = "email" if slack_id else None

        if not slack_id:
            slack_id = _slack_id_from_user_doc(users_by_email.get(email), by_email, member_ids)
            if not slack_id:
                slack_id = _slack_id_from_user_doc(
                    users_by_propel.get(data.get("user_id")), by_email, member_ids
                )
            if not slack_id and is_slack_user_id(data.get("user_id")):
                # Legacy docs stored the Slack login id as user_id.
                legacy = extract_slack_user_id(data["user_id"])
                slack_id = legacy if legacy in member_ids else None
            matched_by = "profile" if slack_id else None

        row = {
            "id": doc_id,
            "name": data.get("name") or "",
            "email": email or "",
            "volunteer_type": data.get("volunteer_type") or "",
            "isSelected": bool(data.get("isSelected")),
        }
        if not slack_id:
            unmatched.append(row)
            continue
        if dry_run:
            updated.append({**row, "slack_user_id": slack_id, "matched_by": matched_by})
            continue
        try:
            db.collection("volunteers").document(doc_id).set({
                "slack_user_id": slack_id,
                "slack_user_id_matched_at": now,
                "slack_user_id_matched_by": matched_by,
            }, merge=True)
        except Exception as e:
            warning(logger, "Could not save slack_user_id", volunteer_id=doc_id, error=str(e))
            unmatched.append(row)
            continue
        updated.append({**row, "slack_user_id": slack_id, "matched_by": matched_by})

    if updated and not dry_run:
        _clear_volunteer_list_cache()

    info(logger, "Volunteer Slack refresh done", event_id=event_id,
         volunteer_type=volunteer_type, checked=checked,
         updated=len(updated), unmatched=len(unmatched))
    return {"checked": checked, "updated": updated, "unmatched": unmatched}

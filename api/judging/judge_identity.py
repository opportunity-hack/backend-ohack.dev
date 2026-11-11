"""
Caller identity for the judge-facing routes (Class B in
docs/plans/reliability-bug-classes-2026-11.md, frontend repo).

The judge pages send the caller's PropelAuth id as `judge_id`, but admins
assign judges by the id stored on the judge's volunteer doc
(`volunteer.user_id`, or the doc id when that is blank — see
`JudgingRound1`'s `j.user_id || j.id`). When the two differ — an application
imported from Sheets, a legacy OAuth id, an application filed under another
login with the same email — the judge saw zero teams and every write 403'd.

This module only answers "which stored judge id is this caller?". It never
changes how assignments, scores or drafts are keyed: the views pass the
resolved STORED id to the service exactly where they used to pass the caller
id. When the caller's doc is keyed by their PropelAuth id (the common case)
the resolved id IS the caller id, so behaviour is unchanged.

Resolution goes through the shared three-way resolver in
services/volunteers_service.py (propel UUID -> verified PropelAuth email ->
legacy OAuth id) — the same one mentors, peer votes and my-roles use.
"""
from typing import List, Optional

from common.log import get_logger, warning

logger = get_logger("judge_identity")

JUDGE_TYPE = "judge"


def _judge_snapshot(caller_id: str, event_id: str):
    """The caller's judge volunteer snapshot for one event, or None."""
    from db.db import get_db
    from services.volunteers_service import _find_volunteer_snapshot
    return _find_volunteer_snapshot(get_db(), caller_id, event_id, JUDGE_TYPE)


def _all_event_judge_snapshots(caller_id: str) -> list:
    """Every judge volunteer snapshot the caller owns, across events.

    Used only by the two routes that carry no event id (the /judge index's
    assignment list and the team-detail access check)."""
    from db.db import get_db
    from services.volunteers_service import _caller_identities

    propel_id, email, oauth_user_id = _caller_identities(caller_id)
    db = get_db()
    found = {}
    for field, value in (("user_id", propel_id), ("email", email), ("user_id", oauth_user_id)):
        if not value:
            continue
        try:
            query = db.collection("volunteers").where(field, "==", value) \
                                              .where("volunteer_type", "==", JUDGE_TYPE)
            for snap in query.stream():
                found.setdefault(snap.id, snap)
        except Exception as e:
            warning(logger, "judge identity query failed", field=field, exc_info=e)
    return list(found.values())


def stored_judge_id(snap) -> Optional[str]:
    """The id admins assign this judge by: `user_id`, else the doc id."""
    if snap is None:
        return None
    data = snap.to_dict() or {}
    return data.get("user_id") or snap.id


def resolve_judge_id(caller_id: str, event_id: str) -> str:
    """The stored judge id for the caller at this event; the caller id when
    no judge doc is found (or on any lookup error) — i.e. today's behaviour."""
    if not caller_id or not event_id:
        return caller_id
    try:
        resolved = stored_judge_id(_judge_snapshot(caller_id, event_id))
    except Exception as e:
        warning(logger, "judge identity resolution failed; using caller id",
                event_id=event_id, exc_info=e)
        return caller_id
    return resolved or caller_id


def caller_judge_ids(caller_id: str) -> List[str]:
    """[caller id, then every other stored judge id the caller owns]."""
    ids = [caller_id] if caller_id else []
    if not caller_id:
        return ids
    try:
        snaps = _all_event_judge_snapshots(caller_id)
    except Exception as e:
        warning(logger, "judge identity lookup failed; using caller id", exc_info=e)
        return ids
    for snap in snaps:
        sid = stored_judge_id(snap)
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def may_act_as(caller_id: str, requested_judge_id: str, resolved_id: str) -> bool:
    """A judge may name themselves either by their login id (what the judge
    pages send) or by their stored judge id. Nobody else's id is accepted."""
    return bool(requested_judge_id) and requested_judge_id in (caller_id, resolved_id)

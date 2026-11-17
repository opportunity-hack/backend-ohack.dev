"""
Mentor engagement for one event — admin read side.

Joins the two signals we have about a mentor:
  * check-ins (self check-in on /hack/<event>/mentor-checkin + the desk check-in)
  * what they did in the per-team mentor panel (coverage sign-offs, notes,
    flags raised / resolved, readiness ratings), read off the team docs

and suggests who meets the Heart Certificate bar on /about/mentors ("judges
don't just check that you showed up; they look for evidence that teams
actually benefited"). The level is a SUGGESTION for the organizer — nothing
here issues a certificate.

`summarize_mentor_engagement` is pure (no Firestore, no PropelAuth) so the
rules are unit-testable.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("myapp")

# "engaged" = checked in AND at least this much panel work. Showing up alone
# (or one drive-by click) does not meet the bar on /about/mentors.
ENGAGED_MIN_ACTIONS = 5
ENGAGED_MIN_TEAMS = 1

# Engagement score = one number to scan and sort by. Points are deliberately
# simple so an organizer can explain any score from its breakdown.
SCORE_PER_ACTION = 1
SCORE_PER_TEAM = 2
SCORE_CHECKED_IN = 2
SCORE_PER_HOUR = 1
SCORE_MAX_HOUR_POINTS = 10   # a forgotten check-out must not win the ranking

LEVEL_ENGAGED = "engaged"            # suggested for a certificate
LEVEL_SOME = "some_activity"         # used the panel, under the bar — review
LEVEL_CHECKED_IN_ONLY = "checked_in_only"
LEVEL_NONE = "none"

_COUNT_KEYS = ("coverage_signoffs", "notes", "flags_raised", "flags_resolved", "ratings")


def _parse_ts(value) -> Optional[datetime]:
    """Naive-UTC datetime for any stored timestamp, or None. Mentor-panel
    stamps are naive UTC; check-in stamps carry the Arizona offset."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _checkin_summary(vol: Dict[str, Any]) -> Dict[str, Any]:
    """Check-in facts off one volunteer doc. Hours pair each self check-in with
    the next check-out; a session with no check-out is not counted (we don't
    know when they left)."""
    ins = sorted(t for t in (_parse_ts(v) for v in (vol.get("checkInTimeList") or [])) if t)
    if not ins:
        single = _parse_ts(vol.get("checkInTime"))
        ins = [single] if single else []
    outs = sorted(t for t in (_parse_ts(v) for v in (vol.get("checkoutTimeList") or [])) if t)

    seconds = 0.0
    open_sessions = 0
    for i, start in enumerate(ins):
        next_in = ins[i + 1] if i + 1 < len(ins) else None
        end = next((o for o in outs if o > start and (next_in is None or o <= next_in)), None)
        if end:
            seconds += (end - start).total_seconds()
        else:
            open_sessions += 1

    desk = vol.get("checkedIn") is True
    desk_at = _parse_ts(vol.get("checkedInAt"))
    first = min([t for t in ins + [desk_at] if t], default=None)
    return {
        "checked_in": bool(ins) or desk,
        "self_checkin_count": len(ins),
        "desk_checked_in": desk,
        "currently_checked_in": bool(vol.get("isCheckedIn")),
        "checked_in_hours": round(seconds / 3600, 1),
        "sessions_without_checkout": open_sessions,
        "first_checkin_at": _iso_utc(first),
    }


def _new_activity(name: Optional[str] = None) -> Dict[str, Any]:
    return {"name": name, "teams": {}, "last": None, **{k: 0 for k in _COUNT_KEYS}}


def collect_panel_activity(teams: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{propel_id: activity} from the mentor_* fields on the event's team docs."""
    by_pid: Dict[str, Dict[str, Any]] = {}

    def record(pid, name, key, team, at):
        if not pid or pid == "_legacy":
            return
        act = by_pid.setdefault(pid, _new_activity())
        if name and not act["name"]:
            act["name"] = name
        act[key] += 1
        act["teams"][team["id"]] = team.get("name") or team["id"]
        ts = _parse_ts(at)
        if ts and (act["last"] is None or ts > act["last"]):
            act["last"] = ts

    for team in teams:
        for entry in (team.get("mentor_checklist") or {}).values():
            if not isinstance(entry, dict):
                continue
            in_history = set()
            for ev in entry.get("history") or []:
                if isinstance(ev, dict) and ev.get("action") == "checked":
                    in_history.add(ev.get("propel_id"))
                    record(ev.get("propel_id"), ev.get("name"), "coverage_signoffs", team, ev.get("at"))
            # Sign-offs made before history existed live only in `checks`.
            checks = entry.get("checks")
            if isinstance(checks, dict):
                for pid, check in checks.items():
                    if pid not in in_history and isinstance(check, dict):
                        record(pid, check.get("name"), "coverage_signoffs", team, check.get("checked_at"))
            elif entry.get("done"):
                record(entry.get("checked_by_propel_id"), entry.get("checked_by_name"),
                       "coverage_signoffs", team, entry.get("checked_at"))
        for note in team.get("mentor_notes") or []:
            if isinstance(note, dict) and not note.get("deleted_at"):
                record(note.get("author_propel_id"), note.get("author_name"), "notes", team,
                       note.get("created_at"))
        for flag in team.get("mentor_flags") or []:
            if not isinstance(flag, dict):
                continue
            record(flag.get("raised_by_propel_id"), flag.get("raised_by_name"), "flags_raised", team,
                   flag.get("created_at"))
            if flag.get("resolved_at"):
                record(flag.get("resolved_by_propel_id"), flag.get("resolved_by_name"),
                       "flags_resolved", team, flag.get("resolved_at"))
        for rating in team.get("mentor_ratings") or []:
            if isinstance(rating, dict):
                record(rating.get("rated_by_propel_id"), rating.get("rated_by_name"), "ratings", team,
                       rating.get("rated_at"))
    return by_pid


def _merge(into: Dict[str, Any], other: Dict[str, Any]) -> None:
    for k in _COUNT_KEYS:
        into[k] += other[k]
    into["teams"].update(other["teams"])
    if other["last"] and (into["last"] is None or other["last"] > into["last"]):
        into["last"] = other["last"]
    into["name"] = into["name"] or other["name"]


def _activity_fields(act: Dict[str, Any]) -> Dict[str, Any]:
    teams = sorted(({"id": tid, "name": name} for tid, name in act["teams"].items()),
                   key=lambda t: str(t["name"]).lower())
    return {
        **{k: act[k] for k in _COUNT_KEYS},
        "panel_actions": sum(act[k] for k in _COUNT_KEYS),
        "teams_helped": len(teams),
        "teams": teams,
        "last_panel_activity_at": _iso_utc(act["last"]),
    }


def engagement_level(checked_in: bool, panel_actions: int, teams_helped: int) -> str:
    if checked_in and panel_actions >= ENGAGED_MIN_ACTIONS and teams_helped >= ENGAGED_MIN_TEAMS:
        return LEVEL_ENGAGED
    if panel_actions > 0:
        return LEVEL_SOME
    return LEVEL_CHECKED_IN_ONLY if checked_in else LEVEL_NONE


def engagement_score(checkin: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """{"score", "score_breakdown": [{label, detail, points}]} for one mentor."""
    hours = int(checkin.get("checked_in_hours") or 0)
    parts = [
        ("Mentor panel actions", "%d x %d" % (fields["panel_actions"], SCORE_PER_ACTION),
         fields["panel_actions"] * SCORE_PER_ACTION),
        ("Teams helped", "%d x %d" % (fields["teams_helped"], SCORE_PER_TEAM),
         fields["teams_helped"] * SCORE_PER_TEAM),
        ("Checked in", "yes" if checkin.get("checked_in") else "no",
         SCORE_CHECKED_IN if checkin.get("checked_in") else 0),
        ("Hours checked in", "%d full hours, max %d points" % (hours, SCORE_MAX_HOUR_POINTS),
         min(hours * SCORE_PER_HOUR, SCORE_MAX_HOUR_POINTS)),
    ]
    return {
        "score": sum(p[2] for p in parts),
        "score_breakdown": [{"label": l, "detail": d, "points": pts} for l, d, pts in parts],
    }


def summarize_mentor_engagement(
    mentor_docs: List[Dict[str, Any]],
    teams: List[Dict[str, Any]],
    resolve_email: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """One row per mentor on the roster (isSelected) plus any other mentor
    application with check-in or panel activity.

    Panel writes are stamped with the PropelAuth id; a volunteer doc may be
    stored under another identity, so ids that match no doc's `user_id` are
    resolved to an email (`resolve_email`) and matched on that. Anything still
    unmatched is returned in `unmatched` — never dropped."""
    activity = collect_panel_activity(teams)

    by_user_id: Dict[str, int] = {}
    by_email: Dict[str, int] = {}
    for i, vol in enumerate(mentor_docs):
        if vol.get("user_id"):
            by_user_id.setdefault(str(vol["user_id"]), i)
        if vol.get("email"):
            by_email.setdefault(str(vol["email"]).strip().lower(), i)

    per_doc: Dict[int, Dict[str, Any]] = {}
    unmatched = []
    for pid, act in activity.items():
        idx = by_user_id.get(pid)
        if idx is None and resolve_email:
            try:
                email = resolve_email(pid)
            except Exception as e:  # a PropelAuth hiccup must not fail the report
                logger.warning("mentor engagement: email lookup failed for %s: %s", pid, e)
                email = None
            if email:
                idx = by_email.get(str(email).strip().lower())
        if idx is None:
            unmatched.append({"propel_id": pid, "name": act["name"], **_activity_fields(act)})
        elif idx in per_doc:
            _merge(per_doc[idx], act)
        else:
            per_doc[idx] = act

    rows = []
    for i, vol in enumerate(mentor_docs):
        checkin = _checkin_summary(vol)
        fields = _activity_fields(per_doc.get(i) or _new_activity())
        selected = vol.get("isSelected") is True
        if not selected and not checkin["checked_in"] and not fields["panel_actions"]:
            continue
        rows.append({
            "volunteer_id": vol.get("id"),
            "user_id": vol.get("user_id"),
            "name": vol.get("name") or " ".join(
                p for p in (vol.get("firstName"), vol.get("lastName")) if p) or None,
            "email": vol.get("email"),
            "isSelected": selected,
            "in_person": vol.get("inPerson"),
            **checkin,
            **fields,
            **engagement_score(checkin, fields),
            "level": engagement_level(checkin["checked_in"], fields["panel_actions"],
                                      fields["teams_helped"]),
        })

    order = [LEVEL_ENGAGED, LEVEL_SOME, LEVEL_CHECKED_IN_ONLY, LEVEL_NONE]
    # Most engaged first: level, then score, then panel work.
    rows.sort(key=lambda r: (order.index(r["level"]), -r["score"], -r["panel_actions"],
                             str(r["name"] or "").lower()))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    unmatched.sort(key=lambda r: -r["panel_actions"])

    return {
        "mentors": rows,
        "unmatched": unmatched,
        "summary": {
            "roster": sum(1 for r in rows if r["isSelected"]),
            "checked_in": sum(1 for r in rows if r["checked_in"]),
            "used_panel": sum(1 for r in rows if r["panel_actions"]),
            **{level: sum(1 for r in rows if r["level"] == level) for level in order},
        },
        "thresholds": {"min_actions": ENGAGED_MIN_ACTIONS, "min_teams": ENGAGED_MIN_TEAMS},
    }


def get_mentor_engagement(event_id: str) -> Dict[str, Any]:
    """Uncached on purpose: an organizer opens this a few times per event."""
    from db.db import get_db
    from services.users_service import get_propel_user_details_by_id

    db = get_db()
    mentor_docs = []
    for doc in (db.collection("volunteers")
                .where("event_id", "==", event_id)
                .where("volunteer_type", "==", "mentor").stream()):
        data = doc.to_dict() or {}
        data["id"] = doc.id
        mentor_docs.append(data)

    teams = []
    for doc in db.collection("teams").where("hackathon_event_id", "==", event_id).stream():
        data = doc.to_dict() or {}
        teams.append({
            "id": doc.id,
            "name": data.get("name"),
            **{k: data.get(k) for k in
               ("mentor_checklist", "mentor_notes", "mentor_flags", "mentor_ratings")},
        })

    def resolve_email(propel_id):
        details = get_propel_user_details_by_id(propel_id) or ()
        return details[0] if details else None

    result = summarize_mentor_engagement(mentor_docs, teams, resolve_email)
    result["event_id"] = event_id
    result["team_count"] = len(teams)
    return result

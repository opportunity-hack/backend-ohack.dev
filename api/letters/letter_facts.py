"""
Every value a participation letter prints, built from stored data only.

`build_facts(user, event_id, role)` returns the facts dict the template
(`letter_pdf.compose_letter`) renders, or raises `NotEligible(reason)`.
Nothing here is typed by the recipient except `name_on_letter` (saved to the
user doc as `legal_name`) and the opt-in title/company line, which is the
user's own self-reported profile `role` + `company`.

Eligibility (all scoped to ONE event, and only for events whose end_date is on
or after LETTERS_START_DATE — no backfill):

  judge      volunteers doc type judge, isSelected, AND >= 1 submitted
             judge_scores doc for that judge at the event. Identity goes
             through api/judging/judge_identity (scores are keyed on the STORED
             judge id). checkInTime is NOT required.
  mentor     isSelected AND checkInTime (remote mentors too)
  volunteer  isSelected AND checkInTime
  hacker     approved application (isSelected) AND on a team at the event
             whose `active` flag is true

Data access goes through a per-event `EventContext` so the admin list (every
person at an event) reads the event, its teams, their members and the event's
scores once, not once per row.
"""
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set

import pytz

from common.log import get_logger, warning

logger = get_logger("letter_facts")

# Only events that END on or after this date issue letters by rule. Older
# events are reachable only through the admin exception.
LETTERS_START_DATE = date(2026, 11, 14)

TEMPLATE_VERSION = 1

ROLES = ("judge", "mentor", "volunteer", "hacker")

DEFAULT_TIMEZONE = "America/Phoenix"

# Reason codes (twinned with the frontend reason-code map).
EVENT_NOT_FOUND = "event_not_found"
EVENT_NOT_ENDED = "event_not_ended"
BEFORE_START_DATE = "before_start_date"
NOT_SELECTED = "not_selected"
NO_SCORES = "no_scores"
NO_CHECKIN = "no_checkin"
NO_ACTIVE_TEAM = "no_active_team"
NAME_MISSING = "name_missing"

REASON_CODES = (
    EVENT_NOT_FOUND, EVENT_NOT_ENDED, BEFORE_START_DATE, NOT_SELECTED,
    NO_SCORES, NO_CHECKIN, NO_ACTIVE_TEAM, NAME_MISSING,
)

MAX_NAME_LENGTH = 120
MAX_LISTED_NAMES = 5


class NotEligible(Exception):
    """The person does not qualify for this letter. `reason` is one of
    REASON_CODES; `evidence` carries what was found (for the admin table)."""

    def __init__(self, reason: str, evidence: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}


# --------------------------------------------------------------------------
# Pure helpers (unit-tested without Firestore)
# --------------------------------------------------------------------------

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_NAMES = {v.lower(): v for v in US_STATES.values()}
_VIRTUAL_RE = re.compile(r"\b(virtual|online|remote|global)\b", re.IGNORECASE)
_ZIP_RE = re.compile(r"\s+\d{5}(?:-\d{4})?$")
_SEASON_RE = re.compile(r"\b(spring|summer|fall|autumn|winter)\b", re.IGNORECASE)


def _state_name(token: str) -> Optional[str]:
    token = _ZIP_RE.sub("", (token or "").strip())
    if token.upper() in US_STATES:
        return US_STATES[token.upper()]
    return _STATE_NAMES.get(token.lower())


def parse_location(location: Optional[str]) -> Dict[str, Optional[str]]:
    """Split the hackathon doc's free-text `location` into
    {venue, city_state, virtual}.

    "Tempe, AZ"                          -> city_state "Tempe, Arizona"
    "ASU Tempe, 501 E Tyler Mall, Tempe, AZ 85281"
                                         -> venue "ASU Tempe, 501 E Tyler Mall"
    "Global" / "ASU - Virtual"           -> virtual True
    Anything we can't read as "<city>, <US state>" yields city_state None —
    the letter then omits that phrase rather than guess."""
    out = {"venue": None, "city_state": None, "virtual": False}
    text = (location or "").strip()
    if not text:
        return out
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        state = _state_name(parts[-1])
        if state:
            out["city_state"] = f"{parts[-2]}, {state}"
            if len(parts) > 2:
                out["venue"] = ", ".join(parts[:-2])
    if out["city_state"] is None and _VIRTUAL_RE.search(text) and "&" not in text:
        out["virtual"] = True
    return out


def season_label(event: Dict[str, Any]) -> Optional[str]:
    """"FALL 2026": the season named in the title, else from the start month."""
    start = parse_iso_date(event.get("start_date"))
    year = start.year if start else None
    m = _SEASON_RE.search(event.get("title") or "")
    if m:
        season = "Fall" if m.group(1).lower() == "autumn" else m.group(1).title()
    elif start:
        season = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring",
                  5: "Spring", 6: "Summer", 7: "Summer", 8: "Summer"}.get(start.month, "Fall")
    else:
        return None
    return f"{season} {year}".upper() if year else season.upper()


def parse_iso_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def event_timezone(event: Dict[str, Any]):
    try:
        return pytz.timezone(event.get("timezone") or DEFAULT_TIMEZONE)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TIMEZONE)


def event_end(event: Dict[str, Any]) -> Optional[datetime]:
    """23:59:59 on end_date in the event timezone (tz-aware)."""
    end = parse_iso_date(event.get("end_date"))
    if not end:
        return None
    return event_timezone(event).localize(datetime(end.year, end.month, end.day, 23, 59, 59))


def to_local_date(value, tz) -> Optional[date]:
    """A stored timestamp (ISO string, datetime, Firestore timestamp) as a
    calendar date in `tz`. Naive values are UTC (how the backend stamps)."""
    if value is None or value == "":
        return None
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        try:
            dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        except ValueError:
            return parse_iso_date(raw)
    else:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(tz).date()


def format_long_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def format_date_list(dates: Iterable[date]) -> Optional[str]:
    """Distinct dates as prose: "November 15, 2026", "November 14–15, 2026",
    "November 14 and 16, 2026", "October 31 and November 1, 2026"."""
    ds = sorted(set(d for d in dates if d))
    if not ds:
        return None
    if len(ds) == 1:
        return format_long_date(ds[0])
    first, last = ds[0], ds[-1]
    contiguous = all((b - a).days == 1 for a, b in zip(ds, ds[1:]))
    if contiguous and first.year == last.year:
        if first.month == last.month:
            return f"{first.strftime('%B')} {first.day}–{last.day}, {last.year}"
        return (f"{first.strftime('%B')} {first.day} – "
                f"{last.strftime('%B')} {last.day}, {last.year}")
    if all(d.year == first.year for d in ds):
        if all(d.month == first.month for d in ds):
            days = [str(d.day) for d in ds]
            return f"{first.strftime('%B')} {join_names(days)}, {first.year}"
        return join_names([f"{d.strftime('%B')} {d.day}" for d in ds]) + f", {first.year}"
    return join_names([format_long_date(d) for d in ds])


def event_date_range(event: Dict[str, Any]) -> List[date]:
    start = parse_iso_date(event.get("start_date"))
    end = parse_iso_date(event.get("end_date")) or start
    if not start:
        return [end] if end else []
    if end < start:
        end = start
    out, d = [], start
    while d <= end and len(out) < 31:
        out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


def join_names(names: List[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def attendance_mode(volunteer: Optional[Dict[str, Any]], location: Dict[str, Any]) -> Optional[str]:
    """"in_person" | "virtual" | None, from stored data only: the person's own
    application answer first (`inPerson` / `isInPerson`), else an event whose
    location is plainly virtual. Unknown -> None (the letter omits the phrase)."""
    vol = volunteer or {}
    for key in ("isInPerson", "inPerson"):
        val = vol.get(key)
        if isinstance(val, bool):
            return "in_person" if val else "virtual"
        if isinstance(val, str) and val.strip():
            low = val.strip().lower()
            if low.startswith("yes") or low in ("true", "in person", "in-person"):
                return "in_person"
            if low.startswith("no") or low in ("false", "virtual", "remote"):
                return "virtual"
    if location.get("virtual"):
        return "virtual"
    return None


def clean_name(value: Optional[str]) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    return text[:MAX_NAME_LENGTH]


def title_line(title: Optional[str], company: Optional[str]) -> Optional[str]:
    title, company = clean_name(title), clean_name(company)
    if title and company:
        return f"{title}, {company}"
    return title or company or None


def team_display_name(name: Optional[str]) -> Optional[str]:
    """"Team Rocket" stays; we never print "team team rocket"."""
    name = clean_name(name)
    if not name:
        return None
    return re.sub(r"^team\s+", "", name, flags=re.IGNORECASE) or name


def is_selected(volunteer: Optional[Dict[str, Any]]) -> bool:
    return bool(volunteer) and volunteer.get("isSelected") is True


def volunteer_display_name(vol: Dict[str, Any]) -> str:
    name = clean_name(vol.get("name"))
    if name:
        return name
    return clean_name(f"{vol.get('firstName') or ''} {vol.get('lastName') or ''}")


# --------------------------------------------------------------------------
# Firestore access, cached per event
# --------------------------------------------------------------------------

def _db():
    from db.db import get_db
    return get_db()


def _ref_id(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.rsplit("/", 1)[-1] or None
    return getattr(value, "id", None)


class EventContext:
    """Lazily-loaded, per-request view of one event: the hackathon doc, its
    teams with member identities, its submitted judge scores and the names of
    the nonprofits / projects those teams worked on."""

    def __init__(self, event_id: str, event: Optional[Dict[str, Any]] = None):
        self.event_id = event_id
        self._event = event
        self._event_loaded = event is not None
        self._teams = None
        self._scores = None
        self._names: Dict[str, Optional[str]] = {}
        self._mentor_teams = None

    # -- event ---------------------------------------------------------
    @property
    def event(self) -> Optional[Dict[str, Any]]:
        if not self._event_loaded:
            self._event_loaded = True
            try:
                from common.utils.firebase import get_hackathon_by_event_id
                self._event = get_hackathon_by_event_id(self.event_id)
            except Exception as e:
                warning(logger, "letters: hackathon lookup failed",
                        event_id=self.event_id, exc_info=e)
                self._event = None
        return self._event

    # -- teams + members ----------------------------------------------
    @property
    def teams(self) -> List[Dict[str, Any]]:
        """[{id, name, active, members: [{doc_id, propel_id, user_id, email}],
        nonprofit_id, problem_statement_id, awards, raw}]"""
        if self._teams is not None:
            return self._teams
        self._teams = []
        event = self.event or {}
        refs = [r for r in (event.get("teams") or []) if r is not None]
        if not refs:
            return self._teams
        db = _db()
        try:
            refs = [db.collection("teams").document(r) if isinstance(r, str) else r for r in refs]
            team_snaps = [s for s in db.get_all(refs) if s is not None and s.exists]
        except Exception as e:
            warning(logger, "letters: team batch read failed", event_id=self.event_id, exc_info=e)
            return self._teams
        member_refs = {}
        for snap in team_snaps:
            for ref in (snap.to_dict() or {}).get("users") or []:
                rid = _ref_id(ref)
                if rid:
                    member_refs[rid] = (db.collection("users").document(ref)
                                        if isinstance(ref, str) else ref)
        members = {}
        if member_refs:
            try:
                for snap in db.get_all(list(member_refs.values())):
                    if snap is not None and snap.exists:
                        d = snap.to_dict() or {}
                        members[snap.id] = {
                            "doc_id": snap.id,
                            "propel_id": d.get("propel_id"),
                            "user_id": d.get("user_id"),
                            "email": (d.get("email_address") or d.get("email") or "").lower() or None,
                        }
            except Exception as e:
                warning(logger, "letters: member batch read failed", event_id=self.event_id, exc_info=e)
        for snap in team_snaps:
            d = snap.to_dict() or {}
            ps = next(iter(d.get("problem_statements") or []), None)
            self._teams.append({
                "id": snap.id,
                "name": d.get("name") or "",
                "active": d.get("active") is True,
                "members": [members.get(_ref_id(r)) or {"doc_id": _ref_id(r)}
                            for r in (d.get("users") or [])],
                "nonprofit_id": d.get("selected_nonprofit_id") or None,
                "problem_statement_id": _ref_id(ps),
                "awards": [a for a in (d.get("awards") or []) if isinstance(a, str) and a.strip()],
                "raw": d,
            })
        return self._teams

    def team_by_id(self, team_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.teams if t["id"] == team_id), None)

    def teams_for_person(self, propel_id: Optional[str], email: Optional[str],
                         doc_ids: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        email = (email or "").lower() or None
        doc_ids = set(doc_ids or ())
        out = []
        for team in self.teams:
            for m in team["members"]:
                if (m.get("doc_id") in doc_ids
                        or (propel_id and propel_id in (m.get("propel_id"), m.get("user_id")))
                        or (email and m.get("email") == email)):
                    out.append(team)
                    break
        return out

    # -- judge scores --------------------------------------------------
    @property
    def scores(self) -> List[Dict[str, Any]]:
        """Submitted (non-draft) judge scores for the event."""
        if self._scores is None:
            from db.db import fetch_judge_scores_by_event_id
            result = fetch_judge_scores_by_event_id(self.event_id) or {}
            if result.get("error"):
                raise RuntimeError(f"judge score read failed: {result.get('error')}")
            self._scores = [s for s in (result.get("data") or []) if not s.get("is_draft")]
        return self._scores

    def scores_for(self, judge_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = {j for j in judge_ids if j}
        return [s for s in self.scores if s.get("judge_id") in ids]

    # -- mentor panel activity -----------------------------------------
    def mentor_team_names(self, propel_id: Optional[str]) -> List[str]:
        if not propel_id:
            return []
        if self._mentor_teams is None:
            try:
                from api.mentors.mentor_engagement_service import collect_panel_activity
                self._mentor_teams = collect_panel_activity(
                    [dict(t["raw"], id=t["id"]) for t in self.teams])
            except Exception as e:
                warning(logger, "letters: mentor activity read failed", exc_info=e)
                self._mentor_teams = {}
        teams = (self._mentor_teams.get(propel_id) or {}).get("teams") or {}
        return sorted({team_display_name(n) for n in teams.values() if team_display_name(n)})

    # -- names of nonprofits / projects --------------------------------
    def _doc_field(self, collection: str, doc_id: Optional[str], field: str) -> Optional[str]:
        if not doc_id:
            return None
        key = f"{collection}/{doc_id}"
        if key not in self._names:
            value = None
            try:
                snap = _db().collection(collection).document(doc_id).get()
                if snap.exists:
                    value = clean_name((snap.to_dict() or {}).get(field)) or None
            except Exception as e:
                warning(logger, "letters: name lookup failed", key=key, exc_info=e)
            self._names[key] = value
        return self._names[key]

    def nonprofit_name(self, nonprofit_id: Optional[str]) -> Optional[str]:
        return self._doc_field("nonprofits", nonprofit_id, "name")

    def project_title(self, ps_id: Optional[str]) -> Optional[str]:
        return self._doc_field("problem_statements", ps_id, "title")


def find_volunteer_doc(user: Dict[str, Any], event_id: str, role: str) -> Optional[Dict[str, Any]]:
    """The person's application for (event, role), or None. Looks up by the
    PropelAuth id and the verified email (the same two keys the profile's
    attendance list uses); an approved doc wins over an unapproved duplicate."""
    from services.volunteers_service import get_user_volunteer_docs
    docs = [d for d in get_user_volunteer_docs(user.get("user_id"), user.get("email"))
            if d.get("event_id") == event_id
            and (d.get("volunteer_type") or "").lower() == role]
    if not docs:
        return None
    docs.sort(key=lambda d: (not is_selected(d), bool(d.get("checkInTime")) is False))
    return docs[0]


def stored_judge_ids(volunteer: Optional[Dict[str, Any]]) -> Set[str]:
    """The id admins assign (and scores are keyed by): `user_id`, else doc id."""
    if not volunteer:
        return set()
    sid = volunteer.get("user_id") or volunteer.get("_doc_id")
    return {sid} if sid else set()


# --------------------------------------------------------------------------
# build_facts
# --------------------------------------------------------------------------

def _event_checks(event: Dict[str, Any], now: datetime, enforce_start_date: bool):
    end_dt = event_end(event)
    if end_dt is None or now < end_dt:
        raise NotEligible(EVENT_NOT_ENDED)
    if enforce_start_date and end_dt.date() < LETTERS_START_DATE:
        raise NotEligible(BEFORE_START_DATE)


def build_facts(user: Dict[str, Any], event_id: str, role: str, *,
                ctx: Optional[EventContext] = None,
                volunteer: Any = "lookup",
                judge_ids: Optional[Iterable[str]] = None,
                doc_ids: Optional[Set[str]] = None,
                name_on_letter: Optional[str] = None,
                show_title_line: bool = False,
                enforce: bool = True,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every value the template uses for `user` at `event_id` as `role`.

    `user`: {user_id (PropelAuth id), email, name, legal_name, title, company}.
    `volunteer`: the application doc when the caller already has it (admin
    list); the default looks it up. `judge_ids`: extra stored judge ids for
    the caller (api/judging/judge_identity.caller_judge_ids).
    `enforce=False` is the admin exception: skips LETTERS_START_DATE and every
    eligibility rule, but still snapshots whatever facts exist.

    Raises NotEligible(reason, evidence). The facts dict carries `evidence`
    (score count, check-in time, team) for the admin table.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    ctx = ctx or EventContext(event_id)
    event = ctx.event
    if not event:
        raise NotEligible(EVENT_NOT_FOUND)
    now = now or datetime.now(pytz.utc)
    if enforce:
        _event_checks(event, now, enforce_start_date=True)

    if volunteer == "lookup":
        volunteer = find_volunteer_doc(user, event_id, role)
    vol = volunteer or {}
    tz = event_timezone(event)
    evidence: Dict[str, Any] = {
        "score_count": None, "check_in_time": vol.get("checkInTime") or None,
        "team_name": None, "team_id": None, "is_selected": is_selected(vol),
    }

    if enforce and not is_selected(vol):
        raise NotEligible(NOT_SELECTED, evidence)

    served: List[date] = []
    role_facts: Dict[str, Any] = {}
    if role == "judge":
        ids = set(judge_ids or ()) | stored_judge_ids(vol)
        scores = ctx.scores_for(ids)
        team_ids = sorted({s.get("team_id") for s in scores if s.get("team_id")})
        evidence["score_count"] = len(scores)
        if enforce and not scores:
            raise NotEligible(NO_SCORES, evidence)
        for s in scores:
            served.append(to_local_date(s.get("submitted_at") or s.get("updated_at")
                                        or s.get("created_at"), tz))
        nonprofits = []
        for tid in team_ids:
            team = ctx.team_by_id(tid)
            name = ctx.nonprofit_name(team["nonprofit_id"]) if team else None
            if name and name not in nonprofits:
                nonprofits.append(name)
        role_facts = {"n_teams": len(team_ids) or None, "nonprofits": nonprofits}
    elif role in ("mentor", "volunteer"):
        if enforce and not vol.get("checkInTime"):
            raise NotEligible(NO_CHECKIN, evidence)
        served.append(to_local_date(vol.get("checkInTime"), tz))
        if role == "mentor":
            role_facts = {"mentor_teams": ctx.mentor_team_names(user.get("user_id"))}
    else:  # hacker
        teams = ctx.teams_for_person(user.get("user_id"), user.get("email"), doc_ids)
        active = [t for t in teams if t["active"]]
        team = active[0] if active else (None if enforce else next(iter(teams), None))
        if team:
            evidence["team_name"] = team["name"] or None
            evidence["team_id"] = team["id"]
        if enforce and not team:
            raise NotEligible(NO_ACTIVE_TEAM, evidence)
        if team:
            role_facts = {
                "team_name": team_display_name(team["name"]),
                "project_title": ctx.project_title(team["problem_statement_id"]),
                "nonprofit_name": ctx.nonprofit_name(team["nonprofit_id"]),
                "awards": team["awards"],
            }
        served.append(to_local_date(vol.get("checkInTime"), tz))

    name = clean_name(name_on_letter) or clean_name(user.get("legal_name")) or clean_name(user.get("name"))
    if not name:
        raise NotEligible(NAME_MISSING, evidence)

    served = [d for d in served if d]
    if not served:
        served = event_date_range(event)
    location = parse_location(event.get("location"))
    mode = attendance_mode(vol, location)

    facts = {
        "template_version": TEMPLATE_VERSION,
        "role": role,
        "name": name,
        "title_line": title_line(user.get("title"), user.get("company")) if show_title_line else None,
        "event_id": event_id,
        "event_title": clean_name(event.get("title")) or None,
        "season_label": season_label(event),
        "city_state": location["city_state"],
        "venue": location["venue"] if mode == "in_person" else None,
        "attendance": mode,
        "served_dates": [d.isoformat() for d in sorted(set(served))],
        "served_date": format_date_list(served),
        "n_teams": None, "nonprofits": [], "mentor_teams": [],
        "team_name": None, "project_title": None, "nonprofit_name": None, "awards": [],
        "evidence": evidence,
    }
    facts.update(role_facts)
    return facts

"""Eligibility rules + fact assembly (api/letters/letter_facts.py).

No Firestore: an EventContext is pre-filled with the event, its teams, its
scores and the nonprofit/project names, and the application doc is passed in
directly — exactly what the service does for the admin list."""
import os
from datetime import date, datetime

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytz

from api.letters import letter_facts as lf
from api.letters.tests._fake_db import FakeDb

NOW = pytz.utc.localize(datetime(2026, 11, 27, 18, 0))
EVENT = {
    "event_id": "fall26", "title": "Opportunity Hack Fall 2026",
    "start_date": "2026-11-14", "end_date": "2026-11-15",
    "timezone": "America/Phoenix", "location": "ASU Tempe Campus, Tempe, AZ",
}
USER = {"user_id": "propel-1", "email": "ada@example.com", "name": "Ada Lovelace",
        "legal_name": "", "title": "Engineer", "company": "Analytical Co"}


def make_ctx(event=EVENT, teams=None, scores=None, mentor_teams=None, names=None):
    ctx = lf.EventContext(event["event_id"], event=dict(event))
    ctx._teams = teams or []
    ctx._scores = scores or []
    ctx._mentor_teams = mentor_teams or {}
    ctx._names.update(names or {})
    return ctx


def team(team_id="t1", name="Byte Bandits", active=True, members=None, nonprofit="np1",
         ps="ps1", awards=None):
    return {"id": team_id, "name": name, "active": active,
            "members": members if members is not None else [
                {"doc_id": "u-doc-1", "propel_id": "propel-1", "email": "ada@example.com"}],
            "nonprofit_id": nonprofit, "problem_statement_id": ps,
            "awards": awards or [], "raw": {"name": name}}


def build(role, vol, ctx=None, **kw):
    kw.setdefault("now", NOW)
    return lf.build_facts(USER, "fall26", role, ctx=ctx or make_ctx(), volunteer=vol, **kw)


def reason(role, vol, ctx=None, **kw):
    with pytest.raises(lf.NotEligible) as exc:
        build(role, vol, ctx, **kw)
    return exc.value.reason


# ---- judge -----------------------------------------------------------------

def judge_vol(**extra):
    return {"_doc_id": "jdoc", "user_id": "judge-stored-id", "volunteer_type": "judge",
            "event_id": "fall26", "isSelected": True, "inPerson": "Yes", **extra}


def score(team_id, judge_id="judge-stored-id", at="2026-11-15T22:30:00Z"):
    return {"judge_id": judge_id, "team_id": team_id, "event_id": "fall26",
            "is_draft": False, "submitted_at": at}


def test_judge_with_scores_and_no_checkin_is_eligible():
    ctx = make_ctx(teams=[team("t1"), team("t2", nonprofit="np2")],
                   scores=[score("t1"), score("t2")],
                   names={"nonprofits/np1": "Zuri's Circle", "nonprofits/np2": "NMTSA"})
    facts = build("judge", judge_vol(), ctx)
    assert facts["n_teams"] == 2
    assert facts["nonprofits"] == ["Zuri's Circle", "NMTSA"]
    assert facts["evidence"]["score_count"] == 2
    assert facts["evidence"]["check_in_time"] is None
    assert facts["served_date"] == "November 15, 2026"
    assert facts["attendance"] == "in_person"
    assert facts["venue"] == "ASU Tempe Campus"
    assert facts["city_state"] == "Tempe, Arizona"


def test_judge_scores_found_through_caller_judge_ids():
    """Scores keyed on another stored judge id the caller owns (judge_identity)."""
    ctx = make_ctx(teams=[team("t1")], scores=[score("t1", judge_id="legacy-oauth-id")])
    facts = build("judge", judge_vol(), ctx, judge_ids=["propel-1", "legacy-oauth-id"])
    assert facts["n_teams"] == 1


def test_judge_score_date_is_in_event_timezone():
    # 03:30 UTC on Nov 16 is still Nov 15 in Phoenix.
    ctx = make_ctx(teams=[team("t1")], scores=[score("t1", at="2026-11-16T03:30:00Z")])
    assert build("judge", judge_vol(), ctx)["served_date"] == "November 15, 2026"


def test_judge_without_scores_is_not_eligible():
    assert reason("judge", judge_vol(), make_ctx(scores=[score("t1", judge_id="someone-else")])) \
        == lf.NO_SCORES


def test_judge_not_selected():
    assert reason("judge", judge_vol(isSelected=False), make_ctx(scores=[score("t1")])) \
        == lf.NOT_SELECTED


def test_no_application_is_not_selected():
    assert reason("judge", None) == lf.NOT_SELECTED


# ---- mentor / volunteer -------------------------------------------------------

def mentor_vol(**extra):
    return {"_doc_id": "m1", "user_id": "propel-1", "volunteer_type": "mentor",
            "event_id": "fall26", "isSelected": True,
            "checkInTime": "2026-11-14T09:05:00-07:00", **extra}


def test_mentor_with_checkin_is_eligible_and_lists_panel_teams():
    ctx = make_ctx(mentor_teams={"propel-1": {"teams": {"t1": "Team Byte Bandits",
                                                         "t2": "Null Pointers"}}})
    facts = build("mentor", mentor_vol(inPerson="No, I'll be virtual"), ctx)
    assert facts["mentor_teams"] == ["Byte Bandits", "Null Pointers"]
    assert facts["served_date"] == "November 14, 2026"
    assert facts["attendance"] == "virtual"
    assert facts["venue"] is None


def test_mentor_without_checkin_is_not_eligible():
    assert reason("mentor", mentor_vol(checkInTime=None)) == lf.NO_CHECKIN


def test_remote_mentor_still_needs_checkin():
    assert reason("mentor", mentor_vol(checkInTime="", inPerson=False)) == lf.NO_CHECKIN


def test_volunteer_rules():
    vol = mentor_vol(volunteer_type="volunteer")
    assert build("volunteer", vol)["role"] == "volunteer"
    assert reason("volunteer", dict(vol, checkInTime=None)) == lf.NO_CHECKIN
    assert reason("volunteer", dict(vol, isSelected=False)) == lf.NOT_SELECTED


# ---- hacker ------------------------------------------------------------------

def hacker_vol(**extra):
    return {"_doc_id": "h1", "user_id": "propel-1", "email": "ada@example.com",
            "volunteer_type": "hacker", "event_id": "fall26", "isSelected": True, **extra}


def test_hacker_on_active_team_is_eligible():
    ctx = make_ctx(teams=[team(awards=["1st Place"])],
                   names={"nonprofits/np1": "Zuri's Circle",
                          "problem_statements/ps1": "Volunteer Scheduler"})
    facts = build("hacker", hacker_vol(), ctx)
    assert facts["team_name"] == "Byte Bandits"
    assert facts["project_title"] == "Volunteer Scheduler"
    assert facts["nonprofit_name"] == "Zuri's Circle"
    assert facts["awards"] == ["1st Place"]
    assert facts["evidence"]["team_name"] == "Byte Bandits"
    # No check-in on a hacker doc -> the event's date range.
    assert facts["served_date"] == "November 14–15, 2026"


def test_hacker_matched_by_email_or_user_doc_id():
    by_email = make_ctx(teams=[team(members=[{"doc_id": "x", "email": "ada@example.com"}])])
    assert build("hacker", hacker_vol(), by_email)["team_name"]
    by_doc = make_ctx(teams=[team(members=[{"doc_id": "google-doc"}])])
    assert build("hacker", hacker_vol(), by_doc, doc_ids={"google-doc"})["team_name"]


def test_hacker_on_inactive_team_is_not_eligible():
    assert reason("hacker", hacker_vol(), make_ctx(teams=[team(active=False)])) == lf.NO_ACTIVE_TEAM


def test_hacker_on_no_team_is_not_eligible():
    assert reason("hacker", hacker_vol(), make_ctx(teams=[team(members=[])])) == lf.NO_ACTIVE_TEAM


def test_unapproved_hacker_is_not_selected():
    assert reason("hacker", hacker_vol(isSelected=False), make_ctx(teams=[team()])) \
        == lf.NOT_SELECTED


# ---- event-level rules ----------------------------------------------------------

def test_event_before_start_date_is_not_eligible():
    old = dict(EVENT, start_date="2026-03-14", end_date="2026-03-15")
    assert reason("mentor", mentor_vol(), make_ctx(event=old)) == lf.BEFORE_START_DATE


def test_event_ending_on_start_date_qualifies():
    edge = dict(EVENT, start_date="2026-11-13", end_date="2026-11-14")
    assert build("mentor", mentor_vol(), make_ctx(event=edge))


def test_event_not_ended():
    during = pytz.utc.localize(datetime(2026, 11, 15, 20, 0))
    assert reason("mentor", mentor_vol(), now=during) == lf.EVENT_NOT_ENDED


def test_missing_event():
    ctx = lf.EventContext("nope", event={})
    ctx._event = None
    assert reason("mentor", mentor_vol(), ctx) == lf.EVENT_NOT_FOUND


def test_name_missing():
    user = dict(USER, name="", legal_name="")
    with pytest.raises(lf.NotEligible) as exc:
        lf.build_facts(user, "fall26", "mentor", ctx=make_ctx(), volunteer=mentor_vol(), now=NOW)
    assert exc.value.reason == lf.NAME_MISSING


def test_every_reason_code_is_covered_here():
    assert set(lf.REASON_CODES) == {
        "event_not_found", "event_not_ended", "before_start_date", "not_selected",
        "no_scores", "no_checkin", "no_active_team", "name_missing"}


def test_exception_bypasses_rules_but_keeps_facts():
    old = dict(EVENT, start_date="2026-03-14", end_date="2026-03-15")
    facts = build("mentor", mentor_vol(isSelected=False, checkInTime=None),
                  make_ctx(event=old), enforce=False)
    assert facts["served_date"] == "March 14–15, 2026"


def test_name_precedence_and_title_line_opt_in():
    user = dict(USER, legal_name="Augusta Ada King")
    facts = lf.build_facts(user, "fall26", "mentor", ctx=make_ctx(), volunteer=mentor_vol(),
                           now=NOW)
    assert facts["name"] == "Augusta Ada King"
    assert facts["title_line"] is None
    facts = lf.build_facts(user, "fall26", "mentor", ctx=make_ctx(), volunteer=mentor_vol(),
                           now=NOW, name_on_letter="  Ada   King ", show_title_line=True)
    assert facts["name"] == "Ada King"
    assert facts["title_line"] == "Engineer, Analytical Co"


def test_unknown_role():
    with pytest.raises(ValueError):
        build("sponsor", mentor_vol())


# ---- EventContext team index against a fake Firestore ---------------------------

def test_event_context_reads_teams_and_members(monkeypatch):
    db = FakeDb()
    u1 = db.put("users", "u1", {"propel_id": "propel-1", "email_address": "ADA@example.com"})
    t1 = db.put("teams", "t1", {"name": "Byte Bandits", "active": True, "users": [u1],
                                "selected_nonprofit_id": "np1", "awards": ["1st Place", ""]})
    db.put("teams", "t2", {"name": "Inactive", "active": False, "users": ["u1"]})
    db.put("nonprofits", "np1", {"name": "Zuri's Circle"})
    monkeypatch.setattr(lf, "_db", lambda: db)
    ctx = lf.EventContext("fall26", event=dict(EVENT, teams=[t1, "t2"]))
    found = ctx.teams_for_person("propel-1", None)
    assert [t["id"] for t in found] == ["t1", "t2"]
    assert ctx.teams_for_person(None, "ada@example.com")[0]["awards"] == ["1st Place"]
    assert ctx.nonprofit_name("np1") == "Zuri's Circle"
    assert ctx.nonprofit_name("missing") is None


# ---- pure helpers --------------------------------------------------------------

@pytest.mark.parametrize("location,expected", [
    ("Tempe, AZ", {"venue": None, "city_state": "Tempe, Arizona", "virtual": False}),
    ("ASU, 501 E Tyler Mall, Tempe, AZ 85281",
     {"venue": "ASU, 501 E Tyler Mall", "city_state": "Tempe, Arizona", "virtual": False}),
    ("San Jose, California", {"venue": None, "city_state": "San Jose, California",
                              "virtual": False}),
    ("Global", {"venue": None, "city_state": None, "virtual": True}),
    ("ASU - Virtual", {"venue": None, "city_state": None, "virtual": True}),
    ("Arizona & Global", {"venue": None, "city_state": None, "virtual": False}),
    ("ASU Tempe", {"venue": None, "city_state": None, "virtual": False}),
    ("", {"venue": None, "city_state": None, "virtual": False}),
])
def test_parse_location(location, expected):
    assert lf.parse_location(location) == expected


def test_format_date_list():
    d = date
    assert lf.format_date_list([d(2026, 11, 15)]) == "November 15, 2026"
    assert lf.format_date_list([d(2026, 11, 15), d(2026, 11, 14), d(2026, 11, 15)]) \
        == "November 14–15, 2026"
    assert lf.format_date_list([d(2026, 11, 14), d(2026, 11, 16)]) == "November 14 and 16, 2026"
    assert lf.format_date_list([d(2026, 10, 31), d(2026, 11, 1)]) \
        == "October 31 – November 1, 2026"
    assert lf.format_date_list([]) is None


def test_season_label():
    assert lf.season_label(EVENT) == "FALL 2026"
    assert lf.season_label({"title": "Summer Internship", "start_date": "2026-06-01"}) \
        == "SUMMER 2026"
    assert lf.season_label({"title": "Hack", "start_date": "2026-03-01"}) == "SPRING 2026"
    assert lf.season_label({}) is None


def test_attendance_mode_only_from_stored_data():
    loc = lf.parse_location("Tempe, AZ")
    assert lf.attendance_mode({"inPerson": "Yes!"}, loc) == "in_person"
    assert lf.attendance_mode({"isInPerson": False}, loc) == "virtual"
    assert lf.attendance_mode({}, loc) is None
    assert lf.attendance_mode({}, lf.parse_location("Global")) == "virtual"

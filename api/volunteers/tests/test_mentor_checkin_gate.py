"""
Mentor self check-in: identity resolution, approval gate and the check-in
window (Nov 2026).

- The check-in routes now resolve the caller with the same three-way lookup as
  GET /api/mentor/application (propel UUID -> verified email -> OAuth id); a
  mentor matched by email used to 404 on /status and fail to check in.
- Only approved (`isSelected`) mentors may check in, and only between 00:00
  on the event's start_date and 23:59:59 on its end_date (event timezone).
  Refusals write nothing and send no Slack. Check-out is always allowed.
"""
import os
from datetime import datetime
from unittest.mock import patch

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytz
from mockfirestore import MockFirestore

from services import volunteers_service as vs

EVENT = "fall-2026"
PHX = pytz.timezone("America/Phoenix")


def _at(y, m, d, hh=12, mm=0, tz=PHX):
    return tz.localize(datetime(y, m, d, hh, mm))


# ---------------------------------------------------------------------------
# mentor_checkin_window

HACK = {"start_date": "2026-11-14", "end_date": "2026-11-15", "timezone": "America/Phoenix"}


@pytest.mark.parametrize("now,state", [
    (_at(2026, 11, 13, 23, 59), "upcoming"),
    (_at(2026, 11, 14, 0, 0), "open"),
    (_at(2026, 11, 15, 23, 59), "open"),
    (_at(2026, 11, 16, 0, 0), "closed"),
])
def test_window_before_during_after(now, state):
    win = vs.mentor_checkin_window(HACK, now=now)
    assert win["state"] == state
    assert win["opens_at"] == "2026-11-14T00:00:00-07:00"
    assert win["closes_at"] == "2026-11-15T23:59:59-07:00"


def test_window_midnight_edge_uses_event_timezone():
    # 06:30 UTC on Nov 14 is still Nov 13 (23:30) in Phoenix -> not open yet.
    now = pytz.utc.localize(datetime(2026, 11, 14, 6, 30))
    assert vs.mentor_checkin_window(HACK, now=now)["state"] == "upcoming"
    # ...but it IS open for an event that runs on UTC time.
    utc_hack = dict(HACK, timezone="UTC")
    assert vs.mentor_checkin_window(utc_hack, now=now)["state"] == "open"


def test_window_defaults_and_tolerates_datetime_strings():
    hack = {"start_date": "2026-11-14T09:00:00", "end_date": "2026-11-15"}
    win = vs.mentor_checkin_window(hack, now=_at(2026, 11, 14, 1, 0))
    assert win["state"] == "open"  # opens at 00:00 of the start DATE
    assert win["opens_at"].endswith("-07:00")  # America/Phoenix default


def test_window_missing_dates_is_open():
    win = vs.mentor_checkin_window(None)
    assert win == {"state": "open", "opens_at": None, "closes_at": None}


# ---------------------------------------------------------------------------
# status / check-in / check-out against MockFirestore

MENTOR = {
    "event_id": EVENT, "volunteer_type": "mentor", "name": "Mo Mentor",
    "email": "mentor@example.com", "isSelected": True,
}


@pytest.fixture
def db():
    store = MockFirestore()
    store.collection("hackathons").document("h1").set(
        dict(HACK, event_id=EVENT, title="Opportunity Hack Fall 2026"))
    yield store
    store.reset()


@pytest.fixture
def env(db):
    """get_db -> MockFirestore; the caller resolves to an email-only match."""
    slack = []
    with patch.object(vs, "get_db", return_value=db), \
         patch.object(vs, "_caller_identities",
                      return_value=("propel-1", "mentor@example.com", None)), \
         patch.object(vs, "_clear_volunteer_caches"), \
         patch.object(vs, "send_mentor_checkin_notification",
                      side_effect=lambda *a, **k: slack.append(("in", a)) or True), \
         patch.object(vs, "send_mentor_checkout_notification",
                      side_effect=lambda *a, **k: slack.append(("out", a)) or True):
        yield db, slack


def _open_now():
    return patch.object(vs, "mentor_checkin_window",
                        return_value={"state": "open", "opens_at": "a", "closes_at": "b"})


def test_email_matched_mentor_can_read_status_and_check_in(env):
    db, slack = env
    # Doc keyed on a different user_id (admin import) — only the email matches.
    db.collection("volunteers").document("m1").set(dict(MENTOR, user_id="someone-else"))

    status = vs.get_mentor_checkin_status("propel-1", EVENT)
    assert status["success"] is True
    assert status["isCheckedIn"] is False
    assert status["isSelected"] is True
    assert status["window"]["state"] in ("upcoming", "open", "closed")

    with _open_now():
        result = vs.mentor_checkin("propel-1", EVENT, "Sat morning")
    assert result["success"] is True and result["message"] == "Checked in successfully"
    stored = db.collection("volunteers").document("m1").get().to_dict()
    assert stored["isCheckedIn"] is True and stored["checkedIn"] is True
    assert stored["timeSlot"] == "Sat morning"
    assert len(stored["checkInTimeList"]) == 1
    assert [s[0] for s in slack] == ["in"]

    out = vs.mentor_checkout("propel-1", EVENT)
    assert out["success"] is True
    assert db.collection("volunteers").document("m1").get().to_dict()["isCheckedIn"] is False


def test_unknown_mentor_status_is_not_found(env):
    status = vs.get_mentor_checkin_status("propel-1", EVENT)
    assert status == {"success": False, "error": "Mentor record not found"}


def test_unapproved_mentor_is_refused_without_write_or_slack(env):
    db, slack = env
    db.collection("volunteers").document("m1").set(dict(MENTOR, user_id="propel-1", isSelected=False))
    with _open_now():
        result = vs.mentor_checkin("propel-1", EVENT)
    assert result["success"] is False and result["error"] == "not_approved"
    assert "approved" in result["message"]
    stored = db.collection("volunteers").document("m1").get().to_dict()
    assert "isCheckedIn" not in stored and "checkedIn" not in stored
    assert slack == []
    assert vs.get_mentor_checkin_status("propel-1", EVENT)["isSelected"] is False


@pytest.mark.parametrize("state,code,key", [
    ("upcoming", "checkin_not_open", "opens_at"),
    ("closed", "checkin_closed", "closes_at"),
])
def test_window_refusals_write_nothing(env, state, code, key):
    db, slack = env
    db.collection("volunteers").document("m1").set(dict(MENTOR, user_id="propel-1"))
    win = {"state": state, "opens_at": "2026-11-14T00:00:00-07:00",
           "closes_at": "2026-11-15T23:59:59-07:00"}
    with patch.object(vs, "mentor_checkin_window", return_value=win):
        result = vs.mentor_checkin("propel-1", EVENT)
    assert result["success"] is False and result["error"] == code
    assert result[key] == win[key]
    assert "checkedIn" not in db.collection("volunteers").document("m1").get().to_dict()
    assert slack == []


def test_approval_is_checked_before_the_window(env):
    db, _ = env
    db.collection("volunteers").document("m1").set(dict(MENTOR, user_id="propel-1", isSelected=False))
    with patch.object(vs, "mentor_checkin_window",
                      return_value={"state": "upcoming", "opens_at": "x", "closes_at": "y"}):
        assert vs.mentor_checkin("propel-1", EVENT)["error"] == "not_approved"


def test_checkout_allowed_after_the_event(env):
    db, slack = env
    db.collection("volunteers").document("m1").set(
        dict(MENTOR, user_id="propel-1", isCheckedIn=True, checkedIn=True,
             checkInTime="2026-11-15T18:00:00-07:00"))
    with patch.object(vs, "mentor_checkin_window",
                      return_value={"state": "closed", "opens_at": "x", "closes_at": "y"}):
        out = vs.mentor_checkout("propel-1", EVENT)
    assert out["success"] is True
    assert db.collection("volunteers").document("m1").get().to_dict()["isCheckedIn"] is False
    assert [s[0] for s in slack] == ["out"]


def test_staff_owned_fields_cover_checkin_audit():
    for key in ("checkedInAt", "checkedInBy", "checkin_reset_history"):
        assert key in vs.STAFF_OWNED_VOLUNTEER_FIELDS

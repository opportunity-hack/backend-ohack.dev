"""
checkin_service: lean projection, the wrong-event guard, and the narrow,
idempotent write. `services.hackathons_service` / `services.users_service` /
`common.utils.slack` are replaced with stubs so nothing touches Firestore,
PropelAuth or Slack; the doc store is MockFirestore (ENVIRONMENT=test).
"""
import importlib
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

SERVICE = "api.checkin.checkin_service"

FULL_DOC = {
    "name": "Ada Lovelace", "email": "ada@example.com", "volunteer_type": "hacker",
    "event_id": "fall-2026", "isSelected": True, "status": "approved",
    "phoneNumber": "555-0100", "shirtSize": "M", "arizonaResident": "Yes",
    "linkedinProfile": "https://linkedin.com/in/ada", "slack_user_id": "U1",
}


@pytest.fixture
def svc(monkeypatch):
    calls = {"welcome": [], "audit": [], "cleared": 0}

    hs = types.ModuleType("services.hackathons_service")
    roster = {"data": [dict(FULL_DOC, id="v1"), dict(FULL_DOC, id="v2", checkedIn=True, checkedInAt="t0")]}

    def _cached(fn):
        fn.cache_clear = lambda: calls.__setitem__("cleared", calls["cleared"] + 1)
        return fn

    hs.get_volunteer_by_event = _cached(lambda e, t, admin=False: roster)
    hs.get_volunteer_checked_in_by_event = _cached(lambda e, t: {"data": [roster["data"][1]]})
    hs.volunteer_was_checked_in = lambda d: bool(d) and d.get("checkedIn") is True
    hs.send_checkin_welcome = lambda *a, **k: calls["welcome"].append((a, k))
    monkeypatch.setitem(sys.modules, "services.hackathons_service", hs)

    us = types.ModuleType("services.users_service")
    us.get_propel_user_details_by_id = lambda pid: ("desk@example.com", "oauth|x", None, None, "Desk Helper", "desk")
    monkeypatch.setitem(sys.modules, "services.users_service", us)

    slack = types.ModuleType("common.utils.slack")
    slack.send_slack_audit = lambda **k: calls["audit"].append(k)
    monkeypatch.setitem(sys.modules, "common.utils.slack", slack)

    sys.modules.pop(SERVICE, None)
    mod = importlib.import_module(SERVICE)

    from db.db import get_db
    db = get_db()
    db.collection("volunteers").document("v1").set(FULL_DOC)
    db.collection("volunteers").document("other").set(dict(FULL_DOC, event_id="2025_fall"))
    db.collection("volunteers").document("mentor1").set(dict(FULL_DOC, volunteer_type="mentor"))
    monkeypatch.setattr(mod, "_get_db", lambda: db)

    yield mod, calls, db
    sys.modules.pop(SERVICE, None)


def test_roster_is_lean(svc):
    mod, _, _ = svc
    rows = mod.get_checkin_roster("fall-2026", "hacker")
    assert [r["id"] for r in rows] == ["v1", "v2"]
    assert set(rows[0]) <= set(mod.LEAN_FIELDS)
    for leaked in ("phoneNumber", "shirtSize", "arizonaResident", "linkedinProfile", "slack_user_id"):
        assert leaked not in rows[0]
    assert rows[0]["checkedIn"] is False and rows[1]["checkedIn"] is True


def test_checked_in_list_uses_checked_in_query(svc):
    mod, _, _ = svc
    rows = mod.get_checked_in("fall-2026", "hacker")
    assert [r["id"] for r in rows] == ["v2"]


def test_unknown_volunteer_404(svc):
    mod, _, _ = svc
    with pytest.raises(mod.CheckInError) as e:
        mod.check_in_volunteer("fall-2026", "hacker", "nope", "propel-1")
    assert e.value.status == 404


@pytest.mark.parametrize("vid,vtype", [("other", "hacker"), ("mentor1", "hacker"), ("v1", "mentor")])
def test_wrong_event_or_type_409_writes_nothing(svc, vid, vtype):
    mod, calls, db = svc
    with pytest.raises(mod.CheckInError) as e:
        mod.check_in_volunteer("fall-2026", vtype, vid, "propel-1")
    assert e.value.status == 409 and e.value.error == "wrong_event"
    assert "checkedIn" not in db.collection("volunteers").document(vid).get().to_dict()
    assert calls["welcome"] == []


def test_first_checkin_writes_only_checkin_fields_and_welcomes_once(svc):
    mod, calls, db = svc
    res = mod.check_in_volunteer("fall-2026", "hacker", "v1", "propel-1")
    assert res["already_checked_in"] is False
    assert res["data"]["checkedIn"] is True and res["data"]["id"] == "v1"
    stored = db.collection("volunteers").document("v1").get().to_dict()
    assert stored["checkedIn"] is True
    assert stored["checkedInBy"] == {"userId": "propel-1", "email": "desk@example.com", "name": "Desk Helper"}
    assert stored["updated_by"] == "Desk Helper"
    assert stored["phoneNumber"] == "555-0100"  # untouched
    assert set(stored) - set(FULL_DOC) == {"checkedIn", "checkedInAt", "checkedInBy", "updated_by", "updated_timestamp"}
    assert len(calls["welcome"]) == 1 and len(calls["audit"]) == 1
    assert calls["cleared"] == 2  # both TTL caches

    again = mod.check_in_volunteer("fall-2026", "hacker", "v1", "propel-2")
    assert again["already_checked_in"] is True
    assert again["data"]["checkedInAt"] == stored["checkedInAt"]
    assert len(calls["welcome"]) == 1  # no second Slack welcome
    assert db.collection("volunteers").document("v1").get().to_dict()["checkedInBy"]["userId"] == "propel-1"


# ---------------------------------------------------------------------------
# reset_checkins (admin): clears every check-in field, appends an audit entry,
# skips docs that don't belong to the URL's event/type.

MENTOR = {"name": "Mo", "email": "mo@example.com", "user_id": "propel-mo",
          "volunteer_type": "mentor", "event_id": "fall-2026", "isSelected": True}


@pytest.fixture
def reset_env(svc, monkeypatch):
    mod, calls, db = svc
    cleared = []
    monkeypatch.setattr(mod, "_clear_person_caches", lambda doc, e, t: cleared.append(doc["id"]))
    vols = db.collection("volunteers")
    # Online self check-in (mentor_checkin): isCheckedIn + checkedIn + checkInTime, no checkedInAt
    vols.document("m-online").set(dict(MENTOR, isCheckedIn=True, checkedIn=True,
                                       checkInTime="2026-11-10T09:00:00-07:00", timeSlot="Sat AM",
                                       checkInTimeList=["2026-11-10T09:00:00-07:00"]))
    # Desk scan
    vols.document("m-desk").set(dict(MENTOR, name="Dee", checkedIn=True, checkedInAt="2026-11-14T16:00:00+00:00",
                                     checkedInBy={"userId": "desk"}))
    # Checked out after an online check-in: isCheckedIn False, but checkInTime set
    vols.document("m-out").set(dict(MENTOR, name="Oz", isCheckedIn=False, checkedIn=True,
                                    checkInTime="t", checkOutTime="t2", checkoutTimeList=["t2"]))
    # Never checked in
    vols.document("m-none").set(dict(MENTOR, name="Nia"))
    # Checked-in mentor at another event
    vols.document("m-other").set(dict(MENTOR, event_id="2025_fall", checkedIn=True, isCheckedIn=True))
    return mod, calls, db, cleared


CLEARED = {
    "checkedIn": False, "isCheckedIn": False, "checkInTime": None, "checkedInAt": None,
    "checkedInBy": None, "timeSlot": None, "checkOutTime": None, "checkInDuration": None,
    "checkInTimeList": [], "checkoutTimeList": [],
}


def test_reset_all_clears_every_checked_in_doc_of_that_type(reset_env):
    mod, calls, db, cleared = reset_env
    audits_before = len(calls["audit"])
    res = mod.reset_checkins("fall-2026", "mentor", "propel-1")
    assert res["reset"] == 3 and sorted(res["ids"]) == ["m-desk", "m-online", "m-out"]
    assert res["skipped"] == []
    for vid in res["ids"]:
        stored = db.collection("volunteers").document(vid).get().to_dict()
        for key, value in CLEARED.items():
            assert stored[key] == value, (vid, key)
        assert stored["name"]  # nothing else touched
        assert stored["updated_by"] == "Desk Helper"
        history = stored["checkin_reset_history"]
        assert len(history) == 1 and history[0]["by"] == "Desk Helper"
    online = db.collection("volunteers").document("m-online").get().to_dict()
    assert online["checkin_reset_history"][0]["previous"]["isCheckedIn"] is True
    assert online["checkin_reset_history"][0]["previous"]["timeSlot"] == "Sat AM"
    # Untouched: never checked in, other event.
    assert "checkin_reset_history" not in db.collection("volunteers").document("m-none").get().to_dict()
    assert db.collection("volunteers").document("m-other").get().to_dict()["checkedIn"] is True
    # ONE audit message, caches cleared, no welcome/notification traffic.
    assert len(calls["audit"]) == audits_before + 1
    assert calls["audit"][-1]["action"] == "reset_checkins"
    assert calls["audit"][-1]["payload"]["count"] == 3
    assert calls["welcome"] == []
    assert sorted(cleared) == sorted(res["ids"])
    assert calls["cleared"] >= 2


def test_reset_ids_skips_wrong_event_missing_and_not_checked_in(reset_env):
    mod, _calls, db, _ = reset_env
    res = mod.reset_checkins("fall-2026", "mentor", "propel-1",
                             volunteer_ids=["m-online", "m-other", "nope", "m-none", "m-online"])
    assert res["reset"] == 1 and res["ids"] == ["m-online"]
    assert res["skipped"] == [
        {"id": "m-other", "reason": "wrong_event"},
        {"id": "nope", "reason": "not_found"},
        {"id": "m-none", "reason": "not_checked_in"},
    ]
    assert db.collection("volunteers").document("m-desk").get().to_dict()["checkedIn"] is True


def test_reset_wrong_type_is_skipped(reset_env):
    mod, _, _, _ = reset_env
    res = mod.reset_checkins("fall-2026", "hacker", "propel-1", volunteer_ids=["m-online"])
    assert res["reset"] == 0 and res["skipped"] == [{"id": "m-online", "reason": "wrong_event"}]


def test_second_reset_is_a_noop(reset_env):
    mod, calls, db, _ = reset_env
    mod.reset_checkins("fall-2026", "mentor", "propel-1", volunteer_ids=["m-online"])
    audits = len(calls["audit"])
    again = mod.reset_checkins("fall-2026", "mentor", "propel-1", volunteer_ids=["m-online"])
    assert again == {"reset": 0, "ids": [], "skipped": [{"id": "m-online", "reason": "not_checked_in"}]}
    assert len(calls["audit"]) == audits  # no Slack for a no-op
    stored = db.collection("volunteers").document("m-online").get().to_dict()
    assert len(stored["checkin_reset_history"]) == 1


def test_reset_rows_now_expose_online_checkin_fields(svc):
    mod, _, _ = svc
    row = mod.lean_volunteer({"id": "x", "isCheckedIn": True, "checkInTime": "t", "phoneNumber": "1"})
    assert row["isCheckedIn"] is True and row["checkInTime"] == "t" and "phoneNumber" not in row

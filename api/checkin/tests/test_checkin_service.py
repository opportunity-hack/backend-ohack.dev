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

"""Unit tests for the meeting-poll service (MockFirestore, patched reCAPTCHA
and Google Calendar — no network)."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from unittest.mock import patch
from mockfirestore import MockFirestore

from api.meet import meet_service as svc
from common.utils import google_calendar

SLOTS = [{"start": "2026-12-01T17:00:00Z"}, {"start": "2026-12-02T10:00:00-07:00"}]
ADMIN = {"propel_id": "p-1", "email": "admin@ohack.org", "name": "Admin"}


@pytest.fixture
def db():
    database = MockFirestore()
    with patch.object(svc, "get_db", return_value=database), \
            patch.object(svc, "verify_recaptcha", return_value=True):
        yield database


def _create(**overrides):
    body = {"code": "Team-Sync", "title": "Team sync", "durationMinutes": 45,
            "slots": SLOTS}
    body.update(overrides)
    return svc.admin_create_poll(body, ADMIN)


def _vote(code="team-sync", **overrides):
    poll = svc.get_public_poll(code)[0]["poll"]
    body = {"name": "Ada", "email": "Ada@Example.com", "recaptchaToken": "t",
            "choices": {poll["slots"][0]["id"]: "yes"}}
    body.update(overrides)
    return svc.submit_vote(code, body)


# --- pure helpers ----------------------------------------------------------

def test_normalize_start_requires_offset_and_converts_to_utc():
    assert svc.normalize_start("2026-12-02T10:00:00-07:00") == "2026-12-02T17:00:00Z"
    assert svc.normalize_start("2026-12-02T17:00:00.000Z") == "2026-12-02T17:00:00Z"
    assert svc.normalize_start("2026-12-02T17:00:00") is None
    assert svc.normalize_start("tomorrow") is None


def test_normalize_code():
    assert svc.normalize_code(" Team-Sync ") == "team-sync"
    assert svc.normalize_code("1234") == "1234"
    assert svc.normalize_code("admin") is None
    assert svc.normalize_code("a b") is None
    assert svc.normalize_code("ab") is None


def test_slots_dedupe_sort_and_keep_known_ids():
    slots, err = svc.normalize_slots(
        ["2026-12-03T00:00:00Z", "2026-12-01T00:00:00Z", "2026-12-01T00:00:00+00:00"])
    assert err is None and [s["start"] for s in slots] == [
        "2026-12-01T00:00:00Z", "2026-12-03T00:00:00Z"]
    kept, _ = svc.normalize_slots(
        [{"id": slots[0]["id"], "start": "2026-12-05T00:00:00Z"},
         {"id": "made-up", "start": "2026-12-06T00:00:00Z"}], slots)
    assert kept[0]["id"] == slots[0]["id"]
    assert kept[1]["id"] != "made-up"
    assert svc.normalize_slots([])[1]
    assert svc.normalize_slots([{"start": "2026-12-05T00:00:00"}])[1]


def test_calendar_event_id_is_stable_and_base32hex():
    event_id = svc.calendar_event_id("team-sync", "abc")
    assert event_id == svc.calendar_event_id("team-sync", "abc")
    assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")


# --- create / edit ---------------------------------------------------------

def test_create_poll_and_duplicate_code(db):
    payload, status = _create()
    assert status == 201
    poll = payload["poll"]
    assert poll["code"] == "team-sync" and poll["status"] == "open"
    assert poll["duration_minutes"] == 45
    assert [s["start"] for s in poll["slots"]] == [
        "2026-12-01T17:00:00Z", "2026-12-02T17:00:00Z"]
    assert _create()[1] == 409


def test_create_generates_code_and_validates(db):
    payload, status = _create(code="")
    assert status == 201 and len(payload["poll"]["code"]) == 6
    assert _create(code="x y")[0]["error"] == "invalid_code"
    assert _create(code="t2", title="")[0]["error"] == "invalid_title"
    assert _create(code="t3", slots=[])[0]["error"] == "invalid_slots"
    assert _create(code="t4", durationMinutes=1)[0]["error"] == "invalid_duration"


def test_update_poll_and_close(db):
    _create()
    payload, status = svc.admin_update_poll("team-sync", {"status": "closed", "title": "New"})
    assert status == 200 and payload["poll"]["status"] == "closed"
    assert _vote()[0]["error"] == "poll_closed"
    assert svc.admin_update_poll("team-sync", {"status": "finalized"})[0]["error"] == "invalid_status"
    assert svc.admin_update_poll("nope", {"title": "x"})[1] == 404


# --- voting ----------------------------------------------------------------

def test_vote_then_public_view_hides_email(db):
    _create()
    payload, status = _vote()
    assert status == 201 and payload["editToken"]
    view, _ = svc.get_public_poll("TEAM-SYNC")
    assert len(view["votes"]) == 1
    vote = view["votes"][0]
    assert vote["name"] == "Ada" and "email" not in vote and "edit_token_hash" not in vote
    slot_ids = [s["id"] for s in view["poll"]["slots"]]
    assert vote["choices"] == {slot_ids[0]: "yes", slot_ids[1]: "no"}
    assert "meet_link" not in view["poll"]


def test_vote_update_needs_edit_token(db):
    _create()
    first, _ = _vote()
    assert _vote()[1] == 409
    assert _vote(editToken="wrong")[0]["error"] == "already_responded"
    payload, status = _vote(editToken=first["editToken"], name="Ada L")
    assert status == 200 and payload["updated"] is True
    view, _ = svc.get_public_poll("team-sync")
    assert [v["name"] for v in view["votes"]] == ["Ada L"]


# --- response notifications ------------------------------------------------

def _backdate_notification(db, seconds):
    from datetime import datetime, timedelta, timezone
    doc = next(iter(db.collection(svc.VOTES).stream()))
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    doc.reference.update({"last_notified_at": stamp})
    return stamp


def test_new_response_notifies_and_quick_edits_do_not(db):
    _create()
    slots = [s["id"] for s in svc.get_public_poll("team-sync")[0]["poll"]["slots"]]
    with patch.object(svc, "_notify_response") as notify:
        first, status = _vote()
        assert status == 201 and notify.call_count == 1
        assert notify.call_args.kwargs["is_update"] is False
        # Changed answers inside the throttle window: saved, not announced.
        assert _vote(editToken=first["editToken"],
                     choices={slots[0]: "yes", slots[1]: "maybe"})[1] == 200
        assert notify.call_count == 1


def test_edit_after_interval_notifies_once_and_restamps(db):
    _create()
    slots = [s["id"] for s in svc.get_public_poll("team-sync")[0]["poll"]["slots"]]
    with patch.object(svc, "_notify_response") as notify:
        first, _ = _vote()
        old = _backdate_notification(db, svc.EDIT_NOTIFY_INTERVAL_SECONDS + 5)
        # Same name and answers: an autosave with nothing new is not news.
        assert _vote(editToken=first["editToken"])[1] == 200
        assert notify.call_count == 1
        assert _vote(editToken=first["editToken"], choices={slots[1]: "yes"})[1] == 200
        assert notify.call_count == 2
        assert notify.call_args.kwargs["is_update"] is True
        stored = next(iter(db.collection(svc.VOTES).stream())).to_dict()
        assert stored["last_notified_at"] > old
        assert _vote(editToken=first["editToken"], choices={slots[0]: "maybe"})[1] == 200
        assert notify.call_count == 2


def test_notification_failure_never_fails_the_vote(db):
    _create()
    with patch.object(svc, "_notifications_disabled", side_effect=RuntimeError("boom")):
        assert _vote()[1] == 201


def test_notification_is_off_under_test_environment(db):
    _create()
    with patch.object(svc, "_send_response_notifications") as send:
        assert _vote()[1] == 201
        send.assert_not_called()


def test_notification_text_escapes_public_input_and_omits_email(db):
    _create(title="Sync <b>&</b> plan")
    poll = svc._load_poll(db, "team-sync")
    slots = svc._slot_ids(poll)
    note = svc.build_response_notification(
        poll, "<!channel> <script>x</script>", {slots[0]: "yes", slots[1]: "maybe"},
        False, 3)
    assert "<!channel>" not in note["slack"] and "&lt;!channel&gt;" in note["slack"]
    assert "responded to" in note["slack"]
    assert "yes to 1 of 2 times, maybe to 1 · 3 responses so far" in note["slack"]
    assert "<script>" not in note["html"] and "<b>" not in note["html"]
    assert "\n" not in note["subject"]
    updated = svc.build_response_notification(poll, "Ada", {}, True, 1)
    assert "updated their times for" in updated["subject"]
    assert "1 response so far" in updated["slack"]


def test_last_notified_at_stays_out_of_every_view(db):
    _create()
    _vote()
    assert "last_notified_at" not in svc.get_public_poll("team-sync")[0]["votes"][0]
    assert "last_notified_at" not in svc.admin_get_poll("team-sync")[0]["votes"][0]


def test_send_posts_to_slack_and_email(db, monkeypatch):
    import sys
    import types
    _create()
    poll = svc._load_poll(db, "team-sync")
    sent = {}
    slack = types.ModuleType("common.utils.slack")
    slack.send_slack = lambda **kw: sent.setdefault("slack", kw)
    resend = types.ModuleType("resend")
    resend.Emails = types.SimpleNamespace(
        send=lambda params: sent.setdefault("email", params))
    retry = types.ModuleType("services.resend_utils")
    retry.send_with_retry = lambda fn, log_context=None: (fn(), None)
    monkeypatch.setitem(sys.modules, "common.utils.slack", slack)
    monkeypatch.setitem(sys.modules, "resend", resend)
    monkeypatch.setitem(sys.modules, "services.resend_utils", retry)
    monkeypatch.setenv("RESEND_WELCOME_EMAIL_KEY", "k")
    monkeypatch.delenv("MEET_NOTIFY_SLACK_CHANNEL", raising=False)
    monkeypatch.delenv("MEET_NOTIFY_EMAIL", raising=False)
    svc._send_response_notifications(poll, "Ada", {}, False)
    assert sent["slack"]["channel"] == "meeting-updates"
    assert sent["email"]["to"] == ["meet@ohack.org"]
    # An empty value switches a channel off.
    sent.clear()
    monkeypatch.setenv("MEET_NOTIFY_SLACK_CHANNEL", "")
    monkeypatch.setenv("MEET_NOTIFY_EMAIL", "")
    svc._send_response_notifications(poll, "Ada", {}, False)
    assert sent == {}


def test_vote_validation(db):
    _create()
    assert _vote(name="")[0]["error"] == "invalid_name"
    assert _vote(email="nope")[0]["error"] == "invalid_email"
    assert _vote(choices="yes")[0]["error"] == "invalid_choices"
    slot_id = svc.get_public_poll("team-sync")[0]["poll"]["slots"][0]["id"]
    assert _vote(choices={slot_id: "definitely"})[0]["error"] == "invalid_choices"
    assert svc.submit_vote(
        "missing", {"name": "A", "email": "a@example.com", "choices": {}})[1] == 404


def test_vote_refused_without_recaptcha(db):
    _create()
    with patch.object(svc, "verify_recaptcha", return_value=False), \
            patch.dict(os.environ, {"FLASK_ENV": "production"}):
        assert _vote()[0]["error"] == "invalid_recaptcha"


def test_poll_full(db):
    _create()
    with patch.object(svc, "MAX_VOTES_PER_POLL", 1):
        assert _vote()[1] == 201
        assert _vote(email="b@example.com")[0]["error"] == "poll_full"


def test_admin_views_and_delete_vote(db):
    _create()
    vote_id = _vote()[0]["voteId"]
    detail, _ = svc.admin_get_poll("team-sync")
    assert detail["votes"][0]["email"] == "ada@example.com"
    listing, _ = svc.admin_list_polls()
    assert listing["data"][0]["vote_count"] == 1
    assert listing["calendar_configured"] is False
    assert svc.admin_delete_vote("other", vote_id)[1] == 404
    assert svc.admin_delete_vote("team-sync", vote_id)[1] == 200
    assert svc.admin_get_poll("team-sync")[0]["votes"] == []


def test_delete_poll_removes_votes(db):
    _create()
    _vote()
    assert svc.admin_delete_poll("team-sync")[1] == 200
    assert svc.get_public_poll("team-sync")[1] == 404
    assert list(db.collection(svc.VOTES).stream()) == []


# --- finalize --------------------------------------------------------------

def _slot_id(index=0):
    return svc.get_public_poll("team-sync")[0]["poll"]["slots"][index]["id"]


def test_finalize_without_invites(db):
    _create()
    _vote()
    with patch.object(google_calendar, "create_meet_event") as create:
        payload, status = svc.admin_finalize_poll(
            "team-sync", {"slotId": _slot_id(), "sendInvites": False}, ADMIN)
    assert status == 200 and payload["invited"] == 0
    create.assert_not_called()
    view, _ = svc.get_public_poll("team-sync")
    assert view["poll"]["status"] == "finalized"
    assert view["poll"]["final_slot_id"] == _slot_id()
    assert view["poll"]["invites_sent"] is False
    assert svc.admin_finalize_poll("team-sync", {"slotId": _slot_id()}, ADMIN)[1] == 409
    assert svc.admin_update_poll("team-sync", {"title": "x"})[1] == 409


def test_finalize_needs_calendar_config(db):
    _create()
    _vote()
    payload, status = svc.admin_finalize_poll("team-sync", {"slotId": _slot_id()}, ADMIN)
    assert status == 503 and payload["error"] == "calendar_not_configured"
    assert svc.get_public_poll("team-sync")[0]["poll"]["status"] == "open"


def test_finalize_sends_calendar_invite(db):
    _create()
    ada = _vote()[0]["voteId"]
    _vote(email="bob@example.com", name="Bob")
    event = {"event_id": "evt", "meet_link": "https://meet.google.com/abc-defg-hij",
             "html_link": "https://calendar.google.com/event?eid=1"}
    with patch.object(google_calendar, "is_configured", return_value=True), \
            patch.object(google_calendar, "create_meet_event", return_value=event) as create:
        payload, status = svc.admin_finalize_poll(
            "team-sync",
            {"slotId": _slot_id(1), "voteIds": [ada],
             "extraEmails": ["Guest@Example.com", "ada@example.com"]},
            ADMIN)
    assert status == 200 and payload["invited"] == 2
    kwargs = create.call_args.kwargs
    assert kwargs["start_utc"] == "2026-12-02T17:00:00Z"
    assert kwargs["end_utc"] == "2026-12-02T17:45:00Z"
    assert [a["email"] for a in kwargs["attendees"]] == [
        "ada@example.com", "guest@example.com"]
    assert kwargs["event_id"] == svc.calendar_event_id("team-sync", _slot_id(1))
    assert payload["poll"]["meet_link"] == event["meet_link"]
    public = svc.get_public_poll("team-sync")[0]["poll"]
    assert public["invites_sent"] is True and "meet_link" not in public


def test_calendar_failure_leaves_poll_open(db):
    _create()
    _vote()
    with patch.object(google_calendar, "is_configured", return_value=True), \
            patch.object(google_calendar, "create_meet_event",
                         side_effect=google_calendar.CalendarError("Not Authorized")):
        payload, status = svc.admin_finalize_poll("team-sync", {"slotId": _slot_id()}, ADMIN)
    assert status == 502 and payload["error"] == "calendar_failed"
    assert svc.get_public_poll("team-sync")[0]["poll"]["status"] == "open"


def test_finalize_validation(db):
    _create()
    assert svc.admin_finalize_poll("team-sync", {"slotId": "nope"}, ADMIN)[0]["error"] == "invalid_slot"
    assert svc.admin_finalize_poll(
        "team-sync", {"slotId": _slot_id(), "sendInvites": "false"}, ADMIN
    )[0]["error"] == "invalid_send_invites"
    assert svc.admin_finalize_poll(
        "team-sync", {"slotId": _slot_id(), "extraEmails": ["bad"]}, ADMIN
    )[0]["error"] == "invalid_extra_emails"
    with patch.object(google_calendar, "is_configured", return_value=True):
        assert svc.admin_finalize_poll(
            "team-sync", {"slotId": _slot_id()}, ADMIN)[0]["error"] == "no_attendees"

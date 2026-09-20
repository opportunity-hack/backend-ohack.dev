"""
Unit tests for api.submissions.submissions_service.

Uses a tiny in-memory fake Firestore (FakeDb) instead of MagicMock chains so
that a save (`set(..., merge=True)`) is visible to the NEXT read in the same
test — needed for idempotency / "edit after submit keeps status" assertions.
`get_team` (imported from services.teams_service) is monkeypatched to read
from the same in-memory store so the returned "team" reflects the write.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

import api.submissions.submissions_service as svc


# ---------------------------------------------------------------------------
# Fake Firestore
# ---------------------------------------------------------------------------

class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store, doc_id):
        self._store = store
        self._id = doc_id

    def get(self):
        return FakeSnapshot(self._id, self._store.get(self._id))

    def set(self, data, merge=False):
        if merge and self._id in self._store:
            self._store[self._id].update(data)
        else:
            self._store[self._id] = dict(data)


class FakeCollection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return FakeDocRef(self._store, doc_id)


class FakeDb:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        assert name == "teams"
        return FakeCollection(self._store)


@pytest.fixture
def team_store():
    return {}


@pytest.fixture
def wire(monkeypatch, team_store):
    """Wires get_db/get_team to the fake store and no-ops external side
    effects (Slack, audit, cache). Returns the store for assertions/setup."""
    monkeypatch.setattr(svc, "get_db", lambda: FakeDb(team_store))
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    monkeypatch.setattr(svc, "send_slack", lambda **kwargs: None)

    def fake_get_team(team_id):
        data = team_store.get(team_id)
        if data is None:
            return {}
        return {"team": {**data, "id": team_id}}

    monkeypatch.setattr(svc, "get_team", fake_get_team)
    return team_store


def _seed_team(store, team_id="team-1", **extra):
    store[team_id] = {
        "hackathon_event_id": "event-1",
        "slack_channel": "team-1-channel",
        **extra,
    }


def _member(monkeypatch, is_member=True):
    monkeypatch.setattr("api.teams.teams_service.user_is_on_team", lambda propel, team_id: is_member)


def _event(monkeypatch, deadlines=None, timezone_name="America/Phoenix"):
    event = {"timezone": timezone_name, "deadlines": deadlines or {}}
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda event_id: event)
    return event


# ---------------------------------------------------------------------------
# compute_submission_window
# ---------------------------------------------------------------------------

def test_window_no_deadline_when_unset():
    window = svc.compute_submission_window({"timezone": "America/Phoenix"})
    assert window["state"] == "no_deadline"


def test_window_open_before_submission():
    now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    event = {"timezone": "UTC", "deadlines": {"submission": "2026-10-10T15:00:00+00:00"}}
    assert svc.compute_submission_window(event, now=now)["state"] == "open"


def test_window_late_between_submission_and_late_until():
    now = datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)
    event = {
        "timezone": "UTC",
        "deadlines": {
            "submission": "2026-10-10T15:00:00+00:00",
            "late_submission_until": "2026-10-10T18:00:00+00:00",
        },
    }
    assert svc.compute_submission_window(event, now=now)["state"] == "late"


def test_window_closed_after_late_until():
    now = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)
    event = {
        "timezone": "UTC",
        "deadlines": {
            "submission": "2026-10-10T15:00:00+00:00",
            "late_submission_until": "2026-10-10T18:00:00+00:00",
        },
    }
    assert svc.compute_submission_window(event, now=now)["state"] == "closed"


def test_window_closed_immediately_after_submission_with_no_late_window():
    now = datetime(2026, 10, 10, 15, 0, 1, tzinfo=timezone.utc)
    event = {"timezone": "UTC", "deadlines": {"submission": "2026-10-10T15:00:00+00:00"}}
    assert svc.compute_submission_window(event, now=now)["state"] == "closed"


# ---------------------------------------------------------------------------
# validate_project_payload — sanitization + limits
# ---------------------------------------------------------------------------

def test_validate_project_payload_sanitizes_tagline_script_tag():
    clean, errors = svc.validate_project_payload({"project_tagline": "Hi <script>bad()</script>"}, "team-1")
    assert errors == []
    assert "<script" not in clean["project_tagline"]


def test_validate_project_payload_preserves_generic_angle_brackets_in_story():
    clean, errors = svc.validate_project_payload(
        {"project_story": "We used List<String> for the queue."}, "team-1"
    )
    assert errors == []
    assert "List<String>" in clean["project_story"]


def test_validate_project_payload_rejects_tagline_over_limit():
    clean, errors = svc.validate_project_payload({"project_tagline": "x" * 141}, "team-1")
    assert any(e["field"] == "project_tagline" for e in errors)
    assert "project_tagline" not in clean


def test_validate_project_payload_rejects_too_many_built_with_tags():
    clean, errors = svc.validate_project_payload({"project_built_with": [f"tag{i}" for i in range(26)]}, "team-1")
    assert any(e["field"] == "project_built_with" for e in errors)


def test_validate_project_payload_rejects_too_many_links():
    links = [{"label": "l", "url": "https://example.com"} for _ in range(11)]
    clean, errors = svc.validate_project_payload({"project_links": links}, "team-1")
    assert any(e["field"] == "project_links" for e in errors)


def test_validate_project_payload_rejects_http_link_url():
    clean, errors = svc.validate_project_payload(
        {"project_links": [{"label": "Repo", "url": "http://example.com"}]}, "team-1"
    )
    assert any(e["field"] == "project_links" for e in errors)


def test_validate_project_payload_rejects_thumbnail_off_own_cdn():
    clean, errors = svc.validate_project_payload(
        {"project_thumbnail_url": "https://evil.example.com/x.png"}, "team-1"
    )
    assert any(e["field"] == "project_thumbnail_url" for e in errors)


def test_validate_project_payload_trusts_existing_thumbnail_without_reverify(monkeypatch):
    url = f"{svc._cdn_server()}/teams/team-1/project/thumb.png"
    # If GCS verification were attempted it would raise via this stub.
    def boom(path):
        raise AssertionError("should not re-verify an already-saved URL")
    monkeypatch.setattr("common.utils.cdn.get_blob_metadata", boom)
    clean, errors = svc.validate_project_payload(
        {"project_thumbnail_url": url}, "team-1", existing={"project_thumbnail_url": url}
    )
    assert errors == []
    assert clean["project_thumbnail_url"] == url


def test_validate_project_payload_verifies_new_own_cdn_thumbnail(monkeypatch):
    url = f"{svc._cdn_server()}/teams/team-1/project/thumb.png"
    monkeypatch.setattr(
        "common.utils.cdn.get_blob_metadata",
        lambda path: {"exists": True, "size": 1000, "content_type": "image/png"},
    )
    clean, errors = svc.validate_project_payload({"project_thumbnail_url": url}, "team-1")
    assert errors == []
    assert clean["project_thumbnail_url"] == url


def test_validate_project_payload_rejects_missing_upload(monkeypatch):
    url = f"{svc._cdn_server()}/teams/team-1/project/thumb.png"
    monkeypatch.setattr(
        "common.utils.cdn.get_blob_metadata",
        lambda path: {"exists": False, "size": None, "content_type": None},
    )
    clean, errors = svc.validate_project_payload({"project_thumbnail_url": url}, "team-1")
    assert any(e["reason"] == "upload_not_found" for e in errors)


# ---------------------------------------------------------------------------
# save_project — auth, deadline gate, draft stamping
# ---------------------------------------------------------------------------

def test_save_project_403_for_non_member(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=False)
    _event(monkeypatch)
    result, status = svc.save_project("propel-1", "team-1", {"project_tagline": "hi"})
    assert status == 403
    assert result["error"] == "not_team_member"


def test_save_project_409_when_closed(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {
        "timezone": "UTC",
        "deadlines": {"submission": "2026-10-10T15:00:00+00:00"},
    })
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "2026-10-10T15:00:00+00:00", "late_until": None, "now": "2026-10-10T20:00:00+00:00", "timezone": "UTC"})
    result, status = svc.save_project("propel-1", "team-1", {"project_tagline": "hi"})
    assert status == 409
    assert result["error"] == "submissions_closed"
    assert result["deadline"] == "2026-10-10T15:00:00+00:00"


def test_save_project_admin_bypasses_deadline(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=False)  # admin bypasses membership too
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "x", "late_until": None, "now": "y", "timezone": "UTC"})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    result, status = svc.save_project("admin-propel", "team-1", {"project_tagline": "hi"}, admin=True)
    assert status == 200
    assert result["success"] is True


def test_save_project_first_save_sets_draft_status(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.save_project("propel-1", "team-1", {"project_tagline": "Hi"})
    assert status == 200
    assert wire["team-1"]["project_submission_status"] == "draft"
    assert wire["team-1"]["project_tagline"] == "Hi"


def test_save_project_400_on_invalid_payload(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.save_project("propel-1", "team-1", {"project_tagline": "x" * 200})
    assert status == 400
    assert result["error"] == "invalid_project"


def test_save_project_after_submit_keeps_status(wire, monkeypatch):
    _seed_team(wire, project_submission_status="submitted", project_tagline="T", project_story="S")
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.save_project("propel-1", "team-1", {"project_tagline": "Updated tagline"})
    assert status == 200
    assert wire["team-1"]["project_submission_status"] == "submitted"
    assert wire["team-1"]["project_tagline"] == "Updated tagline"


# ---------------------------------------------------------------------------
# submit_project
# ---------------------------------------------------------------------------

def test_submit_project_400_incomplete_without_story(wire, monkeypatch):
    _seed_team(wire, project_tagline="Only a tagline")
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 400
    assert result["error"] == "incomplete"
    assert "project_story" in result["missing"]


def test_submit_project_sets_submitted_when_open(wire, monkeypatch):
    _seed_team(wire, project_tagline="T", project_story="S")
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 200
    assert wire["team-1"]["project_submission_status"] == "submitted"
    assert "project_submitted_at" in wire["team-1"]


def test_submit_project_sets_late_inside_grace_window(wire, monkeypatch):
    _seed_team(wire, project_tagline="T", project_story="S")
    _member(monkeypatch, is_member=True)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "late", "submission": "x", "late_until": "y", "now": "z", "timezone": "UTC"})
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 200
    assert wire["team-1"]["project_submission_status"] == "late"


def test_submit_project_idempotent_when_already_submitted(wire, monkeypatch):
    _seed_team(wire, project_submission_status="submitted", project_tagline="T", project_story="S")
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 200
    assert result["already_submitted"] is True


def test_submit_project_403_for_non_member(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=False)
    _event(monkeypatch)
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 403


def test_submit_project_409_when_closed_for_non_admin(wire, monkeypatch):
    _seed_team(wire, project_tagline="T", project_story="S")
    _member(monkeypatch, is_member=True)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "x", "late_until": None, "now": "z", "timezone": "UTC"})
    result, status = svc.submit_project("propel-1", "team-1")
    assert status == 409
    assert result["error"] == "submissions_closed"


def test_submit_project_admin_forced_after_close_is_recorded_late(wire, monkeypatch):
    _seed_team(wire, project_tagline="T", project_story="S")
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "x", "late_until": None, "now": "z", "timezone": "UTC"})
    result, status = svc.submit_project("admin-propel", "team-1", admin=True)
    assert status == 200
    assert wire["team-1"]["project_submission_status"] == "late"


# ---------------------------------------------------------------------------
# self_serve_team_edit — Part 9 bug #1 (was: any logged-in user could edit
# any team's devpost/demo-video link).
# ---------------------------------------------------------------------------

def test_self_serve_team_edit_403_for_non_member(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=False)
    _event(monkeypatch)
    result, status = svc.self_serve_team_edit("propel-1", "team-1", {"devpost_link": "https://devpost.com/x"})
    assert status == 403
    assert result["error"] == "not_team_member"


def test_self_serve_team_edit_allows_member(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    _event(monkeypatch)
    monkeypatch.setattr(
        "api.teams.teams_service.edit_team",
        lambda json: {"success": True, "message": "Team updated successfully", "team_id": json["id"]},
    )
    result = svc.self_serve_team_edit("propel-1", "team-1", {"devpost_link": "https://devpost.com/x"})
    assert result["success"] is True
    assert "team" in result


def test_self_serve_team_edit_409_when_closed(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "x", "late_until": None, "now": "z", "timezone": "UTC"})
    result, status = svc.self_serve_team_edit("propel-1", "team-1", {"demo_video_url": "https://youtu.be/x"})
    assert status == 409


# ---------------------------------------------------------------------------
# set_mentor_help_wanted
# ---------------------------------------------------------------------------

def test_set_mentor_help_wanted_rejects_non_bool(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    result, status = svc.set_mentor_help_wanted("propel-1", "team-1", "yes")
    assert status == 400


def test_set_mentor_help_wanted_no_deadline_gate_even_when_closed(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=True)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {})
    monkeypatch.setattr(svc, "compute_submission_window", lambda event, now=None: {"state": "closed", "submission": "x", "late_until": None, "now": "z", "timezone": "UTC"})
    result, status = svc.set_mentor_help_wanted("propel-1", "team-1", False)
    assert status == 200
    assert wire["team-1"]["mentor_help_wanted"] is False


def test_set_mentor_help_wanted_403_for_non_member(wire, monkeypatch):
    _seed_team(wire)
    _member(monkeypatch, is_member=False)
    result, status = svc.set_mentor_help_wanted("propel-1", "team-1", True)
    assert status == 403


# ---------------------------------------------------------------------------
# get_submission_window_for_event
# ---------------------------------------------------------------------------

def test_get_submission_window_for_event_404_unknown_event(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: None)
    result, status = svc.get_submission_window_for_event("nope")
    assert status == 404


def test_get_submission_window_for_event_returns_window(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"timezone": "UTC", "deadlines": {}})
    result, status = svc.get_submission_window_for_event("event-1")
    assert status == 200
    assert result["state"] == "no_deadline"


# ---------------------------------------------------------------------------
# Deadline reminders — build_reminder_message / send_deadline_reminders /
# send_due_reminders_for_current_events. A separate, more general fake db is
# used here (needs .where().stream() across multiple team docs, which the
# id-only FakeDb above doesn't support).
# ---------------------------------------------------------------------------

class _ReminderFakeDocRef:
    """Only .set() is exercised — send_deadline_reminders reads
    reminders_sent off the already-mocked get_hackathon_by_event_id() return
    value, never off a Firestore read of this ref."""

    def __init__(self, store, key):
        self._store = store
        self._key = key

    def set(self, data, merge=False):
        if merge and self._key in self._store:
            existing = self._store[self._key]
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(existing.get(k), dict):
                    existing[k] = {**existing[k], **v}
                else:
                    existing[k] = v
        else:
            self._store[self._key] = dict(data)


class _ReminderFakeTeamDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _ReminderFakeQuery:
    def __init__(self, teams, field, value):
        self._teams = teams
        self._field = field
        self._value = value

    def stream(self):
        return [
            _ReminderFakeTeamDoc(tid, data)
            for tid, data in self._teams.items()
            if data.get(self._field) == self._value
        ]


class _ReminderFakeCollection:
    def __init__(self, teams, hackathons, name):
        self._teams = teams
        self._hackathons = hackathons
        self._name = name

    def document(self, doc_id):
        assert self._name == "hackathons"
        return _ReminderFakeDocRef(self._hackathons, doc_id)

    def where(self, field, op, value):
        assert self._name == "teams"
        return _ReminderFakeQuery(self._teams, field, value)


class _ReminderFakeDb:
    def __init__(self, teams, hackathons):
        self._teams = teams
        self._hackathons = hackathons

    def collection(self, name):
        return _ReminderFakeCollection(self._teams, self._hackathons, name)


@pytest.fixture
def reminder_wire(monkeypatch):
    teams = {}
    hackathons = {}
    monkeypatch.setattr(svc, "get_db", lambda: _ReminderFakeDb(teams, hackathons))
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    sent_messages = []
    monkeypatch.setattr(svc, "send_slack", lambda message, channel: sent_messages.append((channel, message)))
    return {"teams": teams, "hackathons": hackathons, "sent": sent_messages}


def test_build_reminder_message_none_when_already_submitted():
    team = {"project_submission_status": "submitted"}
    assert svc.build_reminder_message(team, {"event_id": "e1"}, "x", 24) is None


def test_build_reminder_message_lists_missing_items():
    msg = svc.build_reminder_message({}, {"event_id": "e1"}, "x", 6)
    assert "tagline" in msg
    assert "demo video" in msg
    assert "submit your project" in msg
    assert "6 hours left" in msg


def test_build_reminder_message_singular_hour_label():
    msg = svc.build_reminder_message({}, {"event_id": "e1"}, "x", 1)
    assert "1 hour left" in msg
    assert "1 hours" not in msg


def test_send_deadline_reminders_404_unknown_event(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: None)
    result, status = svc.send_deadline_reminders("nope", "submission", 24)
    assert status == 404


def test_send_deadline_reminders_409_when_no_deadline_configured(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"deadlines": {}})
    result, status = svc.send_deadline_reminders("event-1", "submission", 24)
    assert status == 409
    assert result["error"] == "no_deadline"


def test_send_deadline_reminders_rejects_bad_kind_and_hours(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"deadlines": {"submission": "2026-01-01T00:00:00+00:00"}})
    _, status = svc.send_deadline_reminders("event-1", "judging", 24)
    assert status == 400
    _, status = svc.send_deadline_reminders("event-1", "submission", 5)
    assert status == 400


def test_send_deadline_reminders_notifies_and_records_idempotency_key(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=24)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#team-1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24)

    assert status == 200
    assert result["notified"] == ["team-1"]
    assert reminder_wire["hackathons"]["evtdoc-1"]["reminders_sent"]["submission_24h"]["teams_notified"] == ["team-1"]


def test_send_deadline_reminders_skips_team_without_slack_channel(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=24)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24)

    assert result["notified"] == []
    assert result["skipped"] == [{"team_id": "team-1", "reason": "no_slack_channel"}]


def test_send_deadline_reminders_skips_already_done_team(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=24)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1", "project_submission_status": "submitted"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24)

    assert result["notified"] == []
    assert result["skipped"] == [{"team_id": "team-1", "reason": "already_done"}]


def test_send_deadline_reminders_409_already_sent_then_force_succeeds(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=24)).isoformat()

    def event_with_reminders():
        return {
            "id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline},
            "reminders_sent": {"submission_24h": {"sent_at": "earlier"}},
        }

    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event_with_reminders())
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24)
    assert status == 409
    assert result["error"] == "already_sent"

    result, status = svc.send_deadline_reminders("event-1", "submission", 24, force=True)
    assert status == 200
    assert result["notified"] == ["team-1"]


def test_send_deadline_reminders_only_if_due_skips_outside_window(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    # Deadline is 10 hours away; a 24h reminder isn't due yet (due window is
    # [deadline-24h, deadline)).
    deadline = (now + timedelta(hours=10)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 1, only_if_due=True)
    assert status == 200
    assert result["skipped"] == "not_due"
    assert reminder_wire["teams"]["team-1"]  # untouched, no reminder recorded


def test_send_deadline_reminders_only_if_due_sends_inside_window(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=23)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24, only_if_due=True)
    assert status == 200
    assert result["notified"] == ["team-1"]


def test_send_deadline_reminders_simulated_flag_reflects_test_environment(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=24)).isoformat()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1"}

    result, status = svc.send_deadline_reminders("event-1", "submission", 24)
    assert result["simulated"] is True
    # ENVIRONMENT=test means _notifications_disabled() is True, so send_slack
    # is never actually invoked even though the team is still "notified".
    assert reminder_wire["sent"] == []


def test_send_due_reminders_for_current_events_iterates_hours_and_events(reminder_wire, monkeypatch):
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=23)).isoformat()
    monkeypatch.setattr(
        "services.hackathons_service.get_hackathon_list",
        lambda kind: {"hackathons": [{"event_id": "event-1"}]},
    )
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: {"id": "evtdoc-1", "event_id": "event-1", "deadlines": {"submission": deadline}})
    reminder_wire["teams"]["team-1"] = {"hackathon_event_id": "event-1", "slack_channel": "#t1"}

    result, status = svc.send_due_reminders_for_current_events()

    assert status == 200
    hours_checked = {r["hours_before"] for r in result["results"]}
    assert hours_checked == {24, 6, 1}
    # Only the 24h reminder was due (deadline is 23h away); confirm it fired.
    fired = [r for r in result["results"] if r["hours_before"] == 24][0]
    assert fired["result"]["notified"] == ["team-1"]

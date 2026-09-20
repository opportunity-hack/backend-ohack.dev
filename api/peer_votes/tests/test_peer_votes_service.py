"""
Unit tests for api.peer_votes.peer_votes_service.

Uses a tiny in-memory fake Firestore (tuple-keyed FakeDb, supporting
collection/document/where/stream/get_all + a FakeTransaction that
_in_transaction is monkeypatched to use) so multi-step flows (slate
persistence, exposure increments, publish) are visible across calls within
one test, the same way real Firestore would behave.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from google.cloud.firestore_v1.transforms import Increment

os.environ.setdefault("ENVIRONMENT", "test")

import api.peer_votes.peer_votes_service as svc


# ---------------------------------------------------------------------------
# Fake Firestore
# ---------------------------------------------------------------------------

def _resolve_value(existing_value, new_value):
    if isinstance(new_value, Increment):
        base = existing_value if isinstance(existing_value, (int, float)) else 0
        return base + new_value.value
    if isinstance(new_value, dict):
        base_dict = existing_value if isinstance(existing_value, dict) else {}
        merged = dict(base_dict)
        for k, v in new_value.items():
            merged[k] = _resolve_value(base_dict.get(k), v)
        return merged
    return new_value


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    def get(self):
        return FakeSnapshot(self._key[-1], self._store.get(self._key))

    def set(self, data, merge=False):
        if merge and self._key in self._store and self._store[self._key] is not None:
            existing = self._store[self._key]
            for k, v in data.items():
                existing[k] = _resolve_value(existing.get(k), v)
        else:
            resolved = {}
            for k, v in data.items():
                resolved[k] = _resolve_value(None, v)
            self._store[self._key] = resolved

    def collection(self, name):
        return FakeCollection(self._store, self._key + (name,))


class FakeQuery:
    def __init__(self, store, path, filters):
        self._store = store
        self._path = path
        self._filters = filters

    def where(self, field, op, value):
        return FakeQuery(self._store, self._path, self._filters + [(field, op, value)])

    def stream(self):
        prefix_len = len(self._path) + 1
        results = []
        for key, data in list(self._store.items()):
            if data is None or len(key) != prefix_len or key[:-1] != self._path:
                continue
            if all(data.get(field) == value for field, op, value in self._filters):
                results.append(FakeSnapshot(key[-1], data))
        return results


class FakeCollection:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def document(self, doc_id):
        return FakeDocRef(self._store, self._path + (doc_id,))

    def where(self, field, op, value):
        return FakeQuery(self._store, self._path, [(field, op, value)])


class FakeDb:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return FakeCollection(self._store, (name,))

    def get_all(self, refs):
        return [ref.get() for ref in refs]


class FakeTransaction:
    def __init__(self, store):
        self._store = store

    def get(self, ref):
        return iter([ref.get()])

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


@pytest.fixture
def store():
    return {}


@pytest.fixture
def wire(monkeypatch, store):
    monkeypatch.setattr(svc, "get_db", lambda: FakeDb(store))
    monkeypatch.setattr(svc, "_in_transaction", lambda db, body: body(FakeTransaction(store)))
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)

    def fake_get_team(team_id):
        data = store.get(("teams", team_id))
        if data is None:
            return {}
        return {"team": {**data, "id": team_id}}

    monkeypatch.setattr(svc, "get_team", fake_get_team)
    return store


def _seed_hackathon(event_id="event-1", doc_id="evtdoc-1", **extra):
    return {"id": doc_id, "event_id": event_id, "timezone": "UTC", "deadlines": {}, "constraints": {}, "end_date": "2026-10-11", **extra}


# Window relative to "now" so these tests don't rot as the calendar moves —
# always currently open, regardless of when the suite runs.
_NOW = datetime.now(timezone.utc)
OPEN_WINDOW = {
    "voting_opens": (_NOW - timedelta(days=1)).isoformat(),
    "voting_closes": (_NOW + timedelta(days=1)).isoformat(),
}


def _seed_team(store, team_id, event_id="event-1", **extra):
    store[("teams", team_id)] = {"hackathon_event_id": event_id, "name": team_id, **extra}


def _eligible(monkeypatch, isSelected=True):
    monkeypatch.setattr(
        "services.volunteers_service.find_volunteer_by_caller_identity",
        lambda propel, event_id, vtype: {"name": "Hacker", "isSelected": isSelected},
    )


def _own_teams(monkeypatch, team_ids):
    monkeypatch.setattr(
        "api.teams.teams_service.get_my_teams_by_event_id",
        lambda propel, event_id: {"teams": [{"id": tid} for tid in team_ids]},
    )


# ---------------------------------------------------------------------------
# compute_voting_window — HIGH finding #5 (naive/"Z"/garbage stored deadline
# strings must not crash) and LOW finding #6 (end_date fallback must not
# double-append a time onto an end_date that already has one).
# ---------------------------------------------------------------------------

def test_compute_voting_window_naive_voting_opens_is_localized():
    now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    event = _seed_hackathon(timezone="UTC", deadlines={
        "voting_opens": "2026-10-10T05:00:00",  # naive, no offset
        "voting_closes": "2026-10-12T00:00:00+00:00",
    })
    window = svc.compute_voting_window(event, now=now)
    assert window["state"] == "open"
    assert window["opens_at"] == "2026-10-10T05:00:00+00:00"


def test_compute_voting_window_z_suffixed_voting_closes_is_normalized():
    now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    event = _seed_hackathon(timezone="UTC", deadlines={
        "voting_opens": "2026-10-09T00:00:00+00:00",
        "voting_closes": "2026-10-11T00:00:00Z",
    })
    window = svc.compute_voting_window(event, now=now)
    assert window["state"] == "open"
    assert window["closes_at"] == "2026-10-11T00:00:00+00:00"


def test_compute_voting_window_garbage_deadline_treated_as_closed():
    event = _seed_hackathon(timezone="UTC", deadlines={
        "voting_opens": "not-a-date",
        "voting_closes": "2026-10-12T00:00:00+00:00",
    })
    window = svc.compute_voting_window(event)
    assert window["state"] == "closed"
    assert window["opens_at"] is None


def test_compute_voting_window_end_date_only_date_appends_end_of_day():
    now = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)
    event = _seed_hackathon(timezone="UTC", end_date="2026-10-11", deadlines={
        "voting_opens": "2026-10-09T00:00:00+00:00",
    })
    window = svc.compute_voting_window(event, now=now)
    assert window["closes_at"] == "2026-10-11T23:59:59+00:00"
    assert window["state"] == "open"


def test_compute_voting_window_end_date_with_existing_time_is_not_double_appended():
    """LOW finding #6 regression: end_date already carrying a time used to
    get "T23:59:59" appended anyway ("...T18:00:00T23:59:59"), which failed
    to parse and silently fell back to permanently closed."""
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    event = _seed_hackathon(timezone="UTC", end_date="2026-10-11T18:00:00", deadlines={
        "voting_opens": "2026-10-09T00:00:00+00:00",
    })
    window = svc.compute_voting_window(event, now=now)
    assert window["closes_at"] == "2026-10-11T18:00:00+00:00"
    assert window["state"] == "open"


# ---------------------------------------------------------------------------
# _settings — LOW finding #12: max_picks must never reach a voter-facing
# route >= slate_size, even if the stored doc has an inconsistent pair (the
# validator only checks the relationship when both fields are in the SAME
# PATCH payload).
# ---------------------------------------------------------------------------

def test_settings_clamps_max_picks_below_stored_slate_size():
    event = _seed_hackathon(constraints={"peer_vote_slate_size": 3, "peer_vote_max_picks": 5})
    settings = svc._settings(event)
    assert settings["slate_size"] == 3
    assert settings["max_picks"] == 2


def test_settings_leaves_consistent_pair_untouched():
    event = _seed_hackathon(constraints={"peer_vote_slate_size": 5, "peer_vote_max_picks": 2})
    settings = svc._settings(event)
    assert settings["max_picks"] == 2


# ---------------------------------------------------------------------------
# wilson_lower_bound — spec values
# ---------------------------------------------------------------------------

def test_wilson_lower_bound_zero_shown_is_zero():
    assert svc.wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_five_of_five():
    assert svc.wilson_lower_bound(5, 5) == pytest.approx(0.566, abs=0.001)


def test_wilson_lower_bound_one_of_one():
    assert svc.wilson_lower_bound(1, 1) == pytest.approx(0.207, abs=0.001)


# ---------------------------------------------------------------------------
# build_slate — exposure balance + determinism
# ---------------------------------------------------------------------------

def test_build_slate_prefers_least_exposed():
    candidates = [{"id": f"t{i}"} for i in range(5)]
    exposure = {"t0": 10, "t1": 0, "t2": 5, "t3": 0, "t4": 8}
    slate = svc.build_slate(candidates, exposure, "event-1", "propel-1", 2)
    assert set(slate) == {"t1", "t3"}


def test_build_slate_deterministic_for_same_voter():
    candidates = [{"id": f"t{i}"} for i in range(6)]
    a = svc.build_slate(candidates, {}, "event-1", "propel-1", 3)
    b = svc.build_slate(candidates, {}, "event-1", "propel-1", 3)
    assert a == b


def test_build_slate_caps_at_n():
    candidates = [{"id": f"t{i}"} for i in range(10)]
    slate = svc.build_slate(candidates, {}, "event-1", "propel-1", 4)
    assert len(slate) == 4


# ---------------------------------------------------------------------------
# get_slate — disabled/eligibility/window/own-team-exclusion/persistence
# ---------------------------------------------------------------------------

def test_get_slate_disabled_when_constraint_unset(wire, monkeypatch):
    event = _seed_hackathon(event_id="event-1")
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    result = svc.get_slate("propel-1", "event-1")
    assert result == {"status": "disabled"}


def test_get_slate_disabled_when_event_missing(wire, monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: None)
    result = svc.get_slate("propel-1", "event-1")
    assert result == {"status": "disabled"}


def test_get_slate_not_eligible_when_not_selected_hacker(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines=OPEN_WINDOW)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch, isSelected=False)
    result = svc.get_slate("propel-1", "event-1")
    assert result["status"] == "not_eligible"


def test_get_slate_excludes_own_team_and_unsubmitted_and_inactive(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True, "peer_vote_slate_size": 5}, deadlines=OPEN_WINDOW)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _own_teams(monkeypatch, ["own-team"])

    _seed_team(wire, "own-team", project_submission_status="submitted")
    _seed_team(wire, "candidate-a", project_submission_status="submitted")
    _seed_team(wire, "candidate-b", project_submission_status="submitted")
    _seed_team(wire, "draft-team", project_submission_status="draft")
    _seed_team(wire, "inactive-team", project_submission_status="submitted", active=False)

    result = svc.get_slate("propel-1", "event-1")
    slate_ids = {t["team_id"] for t in result["slate"]}
    assert "own-team" not in slate_ids
    assert "draft-team" not in slate_ids
    assert "inactive-team" not in slate_ids
    assert slate_ids == {"candidate-a", "candidate-b"}


def test_get_slate_not_enough_submissions_when_fewer_than_two_candidates(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines=OPEN_WINDOW)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _own_teams(monkeypatch, [])
    _seed_team(wire, "only-team", project_submission_status="submitted")

    result = svc.get_slate("propel-1", "event-1")
    assert result["status"] == "open"
    assert result["slate"] == []
    assert result["reason"] == "not_enough_submissions"
    # nothing should have been persisted
    assert ("peer_votes", "event-1__propel-1") not in wire


def test_get_slate_second_call_returns_identical_slate_and_increments_exposure_once(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True, "peer_vote_slate_size": 2}, deadlines=OPEN_WINDOW)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _own_teams(monkeypatch, [])
    for i in range(4):
        _seed_team(wire, f"team-{i}", project_submission_status="submitted")

    first = svc.get_slate("propel-1", "event-1")
    second = svc.get_slate("propel-1", "event-1")

    assert first["slate"] == second["slate"]
    exposure = wire[("hackathons", "evtdoc-1", "peer_vote", "exposure")]["counts"]
    for team in first["slate"]:
        assert exposure[team["team_id"]] == 1


def test_get_slate_renders_voided_status_not_voted(wire, monkeypatch):
    """LOW finding #11 regression: an admin voiding a ballot must not leave
    the voter's own slate page still rendering "voted" with their old picks
    — it must show status "voided" and null out picks."""
    event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines=OPEN_WINDOW)
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _own_teams(monkeypatch, [])
    wire[("peer_votes", "event-1__propel-1")] = {
        "event_id": "event-1", "voter_propel_id": "propel-1", "slate": ["t1", "t2"],
        "picks": ["t1"], "voted_at": "2026-01-01T00:00:00+00:00", "voided": True,
    }

    result = svc.get_slate("propel-1", "event-1")
    assert result["status"] == "voided"
    assert result["picks"] is None


def test_get_slate_upcoming_and_closed_states(wire, monkeypatch):
    _eligible(monkeypatch)
    _own_teams(monkeypatch, [])

    upcoming_event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines={
        "voting_opens": "2099-01-01T00:00:00+00:00", "voting_closes": "2099-01-02T00:00:00+00:00",
    })
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: upcoming_event)
    assert svc.get_slate("propel-1", "event-1")["status"] == "upcoming"

    closed_event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines={
        "voting_opens": "2020-01-01T00:00:00+00:00", "voting_closes": "2020-01-02T00:00:00+00:00",
    })
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: closed_event)
    assert svc.get_slate("propel-1", "event-1")["status"] == "closed"


# ---------------------------------------------------------------------------
# submit_ballot — the pick validation matrix
# ---------------------------------------------------------------------------

def _seed_ballot(store, event_id, propel_id, slate, picks=None, voided=False):
    store[("peer_votes", f"{event_id}__{propel_id}")] = {
        "event_id": event_id, "voter_propel_id": propel_id, "slate": slate,
        "picks": picks, "voted_at": None, "voided": voided,
    }


def _open_event(**overrides):
    base = dict(constraints={"peer_vote_enabled": True, "peer_vote_max_picks": 2}, deadlines={
        "voting_opens": "2020-01-01T00:00:00+00:00", "voting_closes": "2099-01-01T00:00:00+00:00",
    })
    base.update(overrides)
    return _seed_hackathon(**base)


def test_submit_ballot_no_slate_yet(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1"])
    assert status == 400
    assert result["error"] == "no_slate"


def test_submit_ballot_rejects_pick_not_in_slate(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2", "t3"])
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1", "t9"])
    assert status == 400
    assert result["error"] == "invalid_picks"


def test_submit_ballot_rejects_too_many_picks(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2", "t3"])
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1", "t2", "t3"])
    assert status == 400


def test_submit_ballot_rejects_empty_picks(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2"])
    result, status = svc.submit_ballot("propel-1", "event-1", [])
    assert status == 400


def test_submit_ballot_rejects_duplicate_picks(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2"])
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1", "t1"])
    assert status == 400


def test_submit_ballot_accepts_valid_picks(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2", "t3"])
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1", "t3"])
    assert status == 200
    assert wire[("peer_votes", "event-1__propel-1")]["picks"] == ["t1", "t3"]


def test_submit_ballot_409_when_voided(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2"], voided=True)
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1"])
    assert status == 409
    assert result["error"] == "ballot_voided"


def test_submit_ballot_409_when_closed(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True}, deadlines={
        "voting_opens": "2020-01-01T00:00:00+00:00", "voting_closes": "2020-01-02T00:00:00+00:00",
    })
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    _seed_ballot(wire, "event-1", "propel-1", slate=["t1", "t2"])
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1"])
    assert status == 409
    assert result["error"] == "voting_closed"


def test_submit_ballot_403_when_disabled(wire, monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: _seed_hackathon())
    _eligible(monkeypatch)
    result, status = svc.submit_ballot("propel-1", "event-1", ["t1"])
    assert status == 403
    assert result["error"] == "peer_vote_disabled"


def test_submit_ballot_re_vote_keeps_original_voted_at(wire, monkeypatch):
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    wire[("peer_votes", "event-1__propel-1")] = {
        "event_id": "event-1", "slate": ["t1", "t2"], "picks": ["t1"],
        "voted_at": "2026-01-01T00:00:00+00:00", "voided": False,
    }
    svc.submit_ballot("propel-1", "event-1", ["t2"])
    assert wire[("peer_votes", "event-1__propel-1")]["voted_at"] == "2026-01-01T00:00:00+00:00"
    assert wire[("peer_votes", "event-1__propel-1")]["picks"] == ["t2"]


def test_submit_ballot_writes_full_doc_without_merge(wire, monkeypatch):
    """LOW finding #8: the spec calls for a full set() (no merge=True) on
    every ballot write. Spy on FakeDocRef.set to assert both that merge is
    never passed as True AND that the written doc still carries every field
    from the original ballot (event_id/slate/created_at) — a bug that
    resurrected the old partial-set behavior would either flip merge back to
    True, or write a doc missing these fields since a non-merge set() with a
    partial dict would have silently dropped them."""
    event = _open_event()
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    _eligible(monkeypatch)
    wire[("peer_votes", "event-1__propel-1")] = {
        "event_id": "event-1", "voter_propel_id": "propel-1", "slate": ["t1", "t2", "t3"],
        "shown_at": "2026-01-01T00:00:00+00:00", "created_at": "2026-01-01T00:00:00+00:00",
        "picks": None, "voted_at": None, "voided": False,
    }

    calls = []
    original_set = FakeDocRef.set

    def spy_set(self, data, merge=False):
        calls.append((dict(data), merge))
        return original_set(self, data, merge=merge)

    monkeypatch.setattr(FakeDocRef, "set", spy_set)

    svc.submit_ballot("propel-1", "event-1", ["t1"])

    data, merge = calls[-1]
    assert merge is False
    assert data["slate"] == ["t1", "t2", "t3"]
    assert data["event_id"] == "event-1"
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
    assert data["picks"] == ["t1"]


def test_void_ballot_writes_full_doc_without_merge(wire, monkeypatch):
    wire[("peer_votes", "event-1__u1")] = {
        "event_id": "event-1", "voter_propel_id": "u1", "slate": ["t1"],
        "picks": ["t1"], "created_at": "2026-01-01T00:00:00+00:00", "voided": False,
    }

    calls = []
    original_set = FakeDocRef.set

    def spy_set(self, data, merge=False):
        calls.append((dict(data), merge))
        return original_set(self, data, merge=merge)

    monkeypatch.setattr(FakeDocRef, "set", spy_set)

    svc.void_ballot("event-1", "u1", "admin-1")

    data, merge = calls[-1]
    assert merge is False
    assert data["slate"] == ["t1"]
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
    assert data["voided"] is True


# ---------------------------------------------------------------------------
# compute_results / get_results — voided exclusion
# ---------------------------------------------------------------------------

def test_compute_results_excludes_voided_ballots():
    ballots = [
        {"picks": ["t1"], "voided": False},
        {"picks": ["t1", "t2"], "voided": True},  # excluded entirely
    ]
    teams_by_id = {"t1": {"name": "Team One"}, "t2": {"name": "Team Two"}}
    exposure = {"t1": 2, "t2": 2}
    results = svc.compute_results(ballots, teams_by_id, exposure)
    by_id = {r["team_id"]: r for r in results}
    assert by_id["t1"]["approvals"] == 1
    assert by_id["t2"]["approvals"] == 0


def test_compute_results_shown_reflects_cast_ballots_not_raw_exposure():
    """HIGH finding #2 — the reviewer's scenario: team A was persisted into
    10 slates (exposure) but only 2 ballots were actually cast, both
    approving it; team B was persisted into only 3 slates but got 1 approving
    ballot. Scoring on raw exposure would badly under-rate A's 100%-of-2
    approval rate against a denominator of 10; scoring on cast ballots (as
    fixed) ranks A above B. exposure_shown keeps the raw count separately."""
    ballots = [
        {"slate": ["a", "x"], "picks": ["a"], "voided": False},
        {"slate": ["a", "y"], "picks": ["a"], "voided": False},
        {"slate": ["b", "z"], "picks": ["b"], "voided": False},
    ]
    teams_by_id = {"a": {"name": "Team A"}, "b": {"name": "Team B"}}
    exposure = {"a": 10, "b": 3}
    results = svc.compute_results(ballots, teams_by_id, exposure)
    by_id = {r["team_id"]: r for r in results}

    assert by_id["a"]["shown"] == 2
    assert by_id["a"]["exposure_shown"] == 10
    assert by_id["b"]["shown"] == 1
    assert by_id["b"]["exposure_shown"] == 3
    # A's rank must beat B's after the fix.
    assert by_id["a"]["rank"] < by_id["b"]["rank"]


def test_compute_results_voiding_a_ballot_removes_it_from_shown_and_approvals():
    ballots_before = [
        {"slate": ["a"], "picks": ["a"], "voided": False},
        {"slate": ["a"], "picks": ["a"], "voided": False},
    ]
    ballots_after_void = [
        {"slate": ["a"], "picks": ["a"], "voided": False},
        {"slate": ["a"], "picks": ["a"], "voided": True},  # this one got voided
    ]
    teams_by_id = {"a": {"name": "Team A"}}
    exposure = {"a": 2}

    before = svc.compute_results(ballots_before, teams_by_id, exposure)[0]
    after = svc.compute_results(ballots_after_void, teams_by_id, exposure)[0]

    assert before["shown"] == 2 and before["approvals"] == 2
    assert after["shown"] == 1 and after["approvals"] == 1


def test_get_results_reports_voided_count_separately(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {"event_id": "event-1", "picks": ["t1"], "voided": False}
    wire[("peer_votes", "event-1__u2")] = {"event_id": "event-1", "picks": ["t1"], "voided": True}

    result, status = svc.get_results("event-1")
    assert status == 200
    assert result["ballots"] == 1
    assert result["voided"] == 1


def test_get_results_includes_ballots_detail_with_no_names_or_picks(wire, monkeypatch):
    """LOW finding #11: the admin UI needs a per-voter list to drive `void`
    — voter_propel_id (an opaque id, not PII) + voted_at + voided + a COUNT
    of picks, but never the picks themselves or any name/email."""
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {
        "event_id": "event-1", "voter_propel_id": "u1", "picks": ["t1"],
        "voted_at": "2026-01-01T00:00:00+00:00", "voided": False,
    }
    wire[("peer_votes", "event-1__u2")] = {
        "event_id": "event-1", "voter_propel_id": "u2", "picks": None,
        "voted_at": None, "voided": True,
    }

    result, status = svc.get_results("event-1")
    assert status == 200
    detail_by_voter = {d["voter_propel_id"]: d for d in result["ballots_detail"]}
    assert detail_by_voter["u1"] == {
        "voter_propel_id": "u1", "voted_at": "2026-01-01T00:00:00+00:00",
        "voided": False, "picks_count": 1,
    }
    assert detail_by_voter["u2"]["voided"] is True
    assert detail_by_voter["u2"]["picks_count"] == 0
    for detail in result["ballots_detail"]:
        assert "picks" not in detail
        assert "name" not in detail
        assert "email" not in detail


# ---------------------------------------------------------------------------
# publish_results — idempotent
# ---------------------------------------------------------------------------

def test_publish_results_appends_award_once(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {"event_id": "event-1", "picks": ["t1"], "voided": False}

    result1, status1 = svc.publish_results("event-1", "admin-1")
    result2, status2 = svc.publish_results("event-1", "admin-1")

    assert status1 == status2 == 200
    assert result1["winner_team_id"] == "t1"
    assert wire[("teams", "t1")]["awards"].count("Hackers' Choice") == 1


def test_publish_results_409_with_no_ballots(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    result, status = svc.publish_results("event-1", "admin-1")
    assert status == 409
    assert result["error"] == "no_ballots"


def test_publish_results_409_with_submitted_teams_but_zero_ballots(wire, monkeypatch):
    """HIGH finding #1 — the actual reviewer scenario: compute_results emits
    a row for every SUBMITTED team regardless of ballot count, so with 3
    submitted teams and 0 ballots, every row's wilson_lower_bound/approvals
    tie at 0 and the sort falls through to team NAME, crowning an arbitrary
    "winner". Must 409 instead, and must not write an award or a summary
    doc."""
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "aaa-team", project_submission_status="submitted")
    _seed_team(wire, "bbb-team", project_submission_status="submitted")
    _seed_team(wire, "ccc-team", project_submission_status="submitted")
    # No peer_votes docs seeded at all — zero ballots cast.

    result, status = svc.publish_results("event-1", "admin-1")

    assert status == 409
    assert result["error"] == "no_ballots"
    assert "awards" not in wire.get(("teams", "aaa-team"), {})
    assert ("hackathons", "evtdoc-1", "peer_vote", "summary") not in wire


def test_publish_results_409_when_slates_opened_but_nobody_voted(wire, monkeypatch):
    """Ballots exist (slates were persisted — people opened the vote page)
    but nobody actually cast a pick. results["ballots"] is non-zero here, so
    this exercises the SECOND half of the HIGH-1 fix: the rank-1 team having
    zero approvals."""
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    _seed_team(wire, "t2", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {
        "event_id": "event-1", "voter_propel_id": "u1", "slate": ["t1", "t2"],
        "picks": None, "voided": False,
    }

    result, status = svc.publish_results("event-1", "admin-1")

    assert status == 409
    assert result["error"] == "no_ballots"


def test_void_ballot_excludes_from_future_results(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {"event_id": "event-1", "picks": ["t1"], "voided": False}

    void_result, void_status = svc.void_ballot("event-1", "u1", "admin-1")
    assert void_status == 200

    results, status = svc.get_results("event-1")
    assert results["voided"] == 1
    assert results["ballots"] == 0


def test_void_ballot_404_for_unknown_voter(wire, monkeypatch):
    result, status = svc.void_ballot("event-1", "nope", "admin-1")
    assert status == 404


# ---------------------------------------------------------------------------
# get_public_summary — gating
# ---------------------------------------------------------------------------

def test_get_public_summary_unpublished_by_default(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    result, status = svc.get_public_summary("event-1")
    assert status == 200
    assert result == {"published": False}


def test_get_public_summary_after_publish(wire, monkeypatch):
    event = _seed_hackathon(constraints={"peer_vote_enabled": True})
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: event)
    monkeypatch.setattr("services.volunteers_service.get_all_hackers_by_event_id", lambda eid: [])
    _seed_team(wire, "t1", project_submission_status="submitted")
    wire[("peer_votes", "event-1__u1")] = {"event_id": "event-1", "picks": ["t1"], "voided": False}
    svc.publish_results("event-1", "admin-1")

    result, status = svc.get_public_summary("event-1")
    assert status == 200
    assert result["published"] is True
    assert result["winner_team_id"] == "t1"


def test_get_public_summary_unknown_event(monkeypatch):
    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: None)
    result, status = svc.get_public_summary("nope")
    assert result == {"published": False}

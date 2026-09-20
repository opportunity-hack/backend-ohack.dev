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

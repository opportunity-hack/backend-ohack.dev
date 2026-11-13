"""
get_judge_assignments (the /judge dashboard list) must stay cheap: lean event
lookup (title + dates only) and ONE scores query for the judge — never the
full get_single_hackathon_event payload per event. Response shape unchanged.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import api.judging.judging_service as svc


def _a(event_id, round_name, team_id):
    return SimpleNamespace(event_id=event_id, round=round_name, team_id=team_id)


def _s(event_id, round_name):
    return SimpleNamespace(event_id=event_id, round=round_name)


EVENTS = {
    "fall-2026": {"id": "doc1", "event_id": "fall-2026", "title": "Fall 2026",
                  "start_date": "2026-11-14", "end_date": "2026-11-15"},
    "2025_fall": {"id": "doc2", "event_id": "2025_fall", "title": "Fall 2025",
                  "start_date": "2025-10-11", "end_date": "2025-10-12"},
}

ASSIGNMENTS = [
    _a("fall-2026", "round1", "t1"), _a("fall-2026", "round1", "t2"),
    _a("fall-2026", "round1", "t3"), _a("fall-2026", "round2", "t1"),
    _a("2025_fall", "round1", "t4"), _a("2025_fall", "round1", "t5"),
]
SCORES = [
    _s("fall-2026", "round1"), _s("fall-2026", "round1"),
    _s("2025_fall", "round1"), _s("2025_fall", "round1"),
]


def _run(assignments=ASSIGNMENTS, scores=SCORES, lookup=None):
    """Run get_judge_assignments with every data dependency mocked."""
    with ExitStack() as stack:
        m = SimpleNamespace(
            assignments=stack.enter_context(patch.object(
                svc, "fetch_judge_assignments_by_judge_id", return_value=assignments)),
            scores=stack.enter_context(patch.object(
                svc, "fetch_judge_scores_by_judge_id", return_value=scores)),
            hackathon=stack.enter_context(patch.object(
                svc, "get_hackathon_by_event_id",
                side_effect=lookup or (lambda eid: EVENTS.get(eid)))),
            full_event=stack.enter_context(patch.object(
                svc, "get_single_hackathon_event")),
        )
        result = svc.get_judge_assignments("judge-1")
    return result, m


def test_shape_and_counts():
    result, _ = _run()
    assert "error" not in result
    by_id = {h["event_id"]: h for h in result["hackathons"]}
    assert len(result["hackathons"]) == 2
    assert by_id["fall-2026"] == {
        "event_id": "fall-2026", "title": "Fall 2026", "start_date": "2026-11-14",
        "end_date": "2026-11-15", "round1_teams": 3, "round2_teams": 1,
        "judging_status": {"round1": {"completed": 2, "total": 3},
                           "round2": {"completed": 0, "total": 1}}}
    assert by_id["2025_fall"] == {
        "event_id": "2025_fall", "title": "Fall 2025", "start_date": "2025-10-11",
        "end_date": "2025-10-12", "round1_teams": 2, "round2_teams": 0,
        "judging_status": {"round1": {"completed": 2, "total": 2},
                           "round2": {"completed": 0, "total": 0}}}


def test_never_loads_full_event_payload():
    _, m = _run()
    m.full_event.assert_not_called()


def test_one_scores_query_regardless_of_event_count():
    _, m = _run()
    m.scores.assert_called_once_with("judge-1")
    assert m.hackathon.call_count == 2
    assert {c.args[0] for c in m.hackathon.call_args_list} == {"fall-2026", "2025_fall"}


def test_no_assignments_makes_no_other_queries():
    result, m = _run(assignments=[])
    assert result == {"hackathons": []}
    m.scores.assert_not_called()
    m.hackathon.assert_not_called()


def test_unknown_event_is_skipped():
    result, _ = _run(lookup=lambda eid: None if eid == "2025_fall" else EVENTS[eid])
    assert [h["event_id"] for h in result["hackathons"]] == ["fall-2026"]


def test_one_event_lookup_failure_keeps_the_others():
    def lookup(eid):
        if eid == "2025_fall":
            raise RuntimeError("boom")
        return EVENTS[eid]

    result, _ = _run(lookup=lookup)
    assert "error" not in result
    assert [h["event_id"] for h in result["hackathons"]] == ["fall-2026"]


def test_scores_for_unassigned_events_are_ignored():
    result, _ = _run(scores=SCORES + [_s("spring-2020", "round1")])
    assert "error" not in result
    assert {h["event_id"] for h in result["hackathons"]} == {"fall-2026", "2025_fall"}

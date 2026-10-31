"""
Mentor coverage freshness + history (Oct 2026, api/mentors/mentors_service.py):

  * a sign-off on a "recurring" item only counts for MENTOR_COVERAGE_FRESH_HOURS;
    "once" items (introductions, criteria walkthrough) never go stale
  * the 3-mentor cap counts FRESH checks only — a stale Saturday check never
    blocks a Sunday mentor
  * re-checking your own stale item refreshes its timestamp
  * every check / re-check / clear appends to the item's `history`
  * the fully-covered count behind the one-time Slack milestone is fresh-only
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from datetime import datetime, timedelta
from unittest.mock import patch

from firebase_admin import firestore

import api.mentors.mentors_service as svc

NOW = datetime(2026, 11, 14, 15, 0, 0)
ME = "propel-me"


def _ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


def _check(name, hours_ago):
    return {"name": name, "checked_at": _ago(hours_ago)}


class FakeRef:
    def __init__(self):
        self.writes = []

    def set(self, update, merge=False):
        self.writes.append((update, merge))


def _toggle(team_data, slug, done, ref=None):
    ref = ref or FakeRef()
    with patch.object(svc, "_team_doc_or_404", return_value=(ref, team_data, None)), \
         patch.object(svc, "user_is_mentor_for_event", return_value=True), \
         patch.object(svc, "_caller_attribution", return_value="Me Mentor"), \
         patch.object(svc, "_build_response", return_value=({"success": True}, 200)), \
         patch.object(svc, "send_slack_audit"), \
         patch.object(svc, "async_send_slack"), \
         patch.object(svc, "clear_cache"), \
         patch.object(svc, "datetime") as fake_dt:
        fake_dt.now.return_value = NOW
        fake_dt.fromisoformat = datetime.fromisoformat
        body, status = svc.toggle_mentor_coverage(ME, "team-1", slug, done)
    return body, status, ref


# ---------------------------------------------------------------------------
# freshness rule
# ---------------------------------------------------------------------------

def test_recurring_check_is_fresh_under_the_window_and_stale_past_it():
    assert svc._check_is_fresh("scope_reviewed", _check("A", 4.9), NOW)
    assert not svc._check_is_fresh("scope_reviewed", _check("A", 5.1), NOW)


def test_once_items_never_go_stale():
    assert svc._check_is_fresh("intro_made", _check("A", 40), NOW)
    assert svc._check_is_fresh("criteria_walkthrough", _check("A", 40), NOW)


def test_legacy_check_without_timestamp_keeps_counting():
    assert svc._check_is_fresh("scope_reviewed", {"name": "Old"}, NOW)
    assert svc._check_is_fresh("scope_reviewed", {"name": "Old", "checked_at": "garbage"}, NOW)


def test_timestamps_with_z_or_offset_compare_as_utc():
    fresh = (NOW - timedelta(hours=1)).isoformat() + "Z"
    stale = (NOW - timedelta(hours=6)).isoformat() + "+00:00"
    assert svc._check_is_fresh("scope_reviewed", {"checked_at": fresh}, NOW)
    assert not svc._check_is_fresh("scope_reviewed", {"checked_at": stale}, NOW)


def test_done_count_only_counts_items_with_three_fresh_checks():
    three_fresh = {"checks": {"a": _check("A", 1), "b": _check("B", 2), "c": _check("C", 3)}}
    three_stale = {"checks": {"a": _check("A", 6), "b": _check("B", 7), "c": _check("C", 8)}}
    assert svc._coverage_done_count({"scope_reviewed": three_fresh}, NOW) == 1
    assert svc._coverage_done_count({"scope_reviewed": three_stale}, NOW) == 0
    # once-item: age is irrelevant
    assert svc._coverage_done_count({"intro_made": three_stale}, NOW) == 1


# ---------------------------------------------------------------------------
# toggle: cap, re-confirm, clear, history, milestone
# ---------------------------------------------------------------------------

def test_fourth_mentor_can_sign_off_when_the_three_existing_checks_are_stale():
    team = {"mentor_checklist": {"scope_reviewed": {
        "checks": {"a": _check("A", 6), "b": _check("B", 7), "c": _check("C", 8)}}}}
    body, status, ref = _toggle(team, "scope_reviewed", True)
    assert status == 200, body
    update, merge = ref.writes[-1]
    assert merge is True
    entry = update["mentor_checklist"]["scope_reviewed"]
    assert entry["checks"][ME]["checked_at"] == NOW.isoformat()
    assert entry["history"] == [
        {"propel_id": ME, "name": "Me Mentor", "at": NOW.isoformat(), "action": "checked"},
    ]


def test_fourth_mentor_is_refused_when_the_three_checks_are_fresh():
    team = {"mentor_checklist": {"scope_reviewed": {
        "checks": {"a": _check("A", 1), "b": _check("B", 2), "c": _check("C", 3)}}}}
    body, status, ref = _toggle(team, "scope_reviewed", True)
    assert status == 409
    assert ref.writes == []


def test_rechecking_my_own_stale_item_refreshes_my_timestamp_and_appends_history():
    old = _ago(7)
    team = {"mentor_checklist": {"scope_reviewed": {
        "checks": {ME: {"name": "Me Mentor", "checked_at": old}, "a": _check("A", 1)},
        "history": [{"propel_id": ME, "name": "Me Mentor", "at": old, "action": "checked"}],
    }}}
    body, status, ref = _toggle(team, "scope_reviewed", True)
    assert status == 200, body
    entry = ref.writes[-1][0]["mentor_checklist"]["scope_reviewed"]
    assert entry["checks"][ME]["checked_at"] == NOW.isoformat()
    assert [h["action"] for h in entry["history"]] == ["checked", "checked"]
    assert entry["history"][0]["at"] == old
    assert entry["history"][1]["at"] == NOW.isoformat()


def test_clearing_my_check_deletes_the_field_and_records_the_clear():
    team = {"mentor_checklist": {"scope_reviewed": {
        "checks": {ME: {"name": "Me Mentor", "checked_at": _ago(1)}}}}}
    body, status, ref = _toggle(team, "scope_reviewed", False)
    assert status == 200, body
    entry = ref.writes[-1][0]["mentor_checklist"]["scope_reviewed"]
    assert entry["checks"][ME] is firestore.DELETE_FIELD
    assert entry["history"][-1]["action"] == "cleared"


def test_clearing_a_check_i_never_made_records_nothing():
    team = {"mentor_checklist": {"scope_reviewed": {"checks": {"a": _check("A", 1)}}}}
    body, status, ref = _toggle(team, "scope_reviewed", False)
    assert status == 200, body
    entry = ref.writes[-1][0]["mentor_checklist"]["scope_reviewed"]
    assert "history" not in entry


def test_history_is_capped():
    old_rows = [{"propel_id": "x", "name": "X", "at": _ago(50), "action": "checked"}
                for _ in range(svc.MENTOR_COVERAGE_HISTORY_MAX)]
    team = {"mentor_checklist": {"scope_reviewed": {"checks": {}, "history": old_rows}}}
    body, status, ref = _toggle(team, "scope_reviewed", True)
    entry = ref.writes[-1][0]["mentor_checklist"]["scope_reviewed"]
    assert len(entry["history"]) == svc.MENTOR_COVERAGE_HISTORY_MAX
    assert entry["history"][-1]["propel_id"] == ME


def test_milestone_needs_three_fresh_checks_on_every_item():
    def item(hours):
        return {"checks": {"a": _check("A", hours), "b": _check("B", hours), "c": _check("C", hours)}}
    slugs = list(svc.MENTOR_COVERAGE_SLUGS)
    # Every item has 3 checks, but the recurring ones are stale → no milestone.
    stale = {s: item(9) for s in slugs}
    stale["demo_devpost_reviewed"] = {"checks": {"a": _check("A", 9), "b": _check("B", 9)}}
    body, status, ref = _toggle({"mentor_checklist": stale, "slack_channel": "c"},
                                "demo_devpost_reviewed", True)
    assert status == 200, body
    assert "mentor_coverage_completed_at" not in ref.writes[-1][0]

    fresh = {s: item(1) for s in slugs}
    fresh["demo_devpost_reviewed"] = {"checks": {"a": _check("A", 1), "b": _check("B", 1)}}
    body, status, ref = _toggle({"mentor_checklist": fresh, "slack_channel": "c"},
                                "demo_devpost_reviewed", True)
    assert status == 200, body
    assert ref.writes[-1][0]["mentor_coverage_completed_at"] == NOW.isoformat()

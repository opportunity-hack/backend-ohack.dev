"""
Part 9 bug fixes in api/judging/judging_service.py:
  #2 judges never received the team's demo video (get_team_details /
     format_team_for_judge read a phantom `video_url` key)
  #3 get_bulk_judge_details always returned empty (NameError on an undefined
     function name, swallowed by a blanket try/except)
  #4 update_judge_assignment_details always 400'd (looked assignments up by
     an empty judge_id instead of the assignment's own id)

No rubric/scoring/results behavior is touched by any of these.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import patch

import api.judging.judging_service as svc


# ---------------------------------------------------------------------------
# #2 — demo_video_url surfaced to judges (both formatters)
# ---------------------------------------------------------------------------

def test_get_team_details_surfaces_demo_video_url():
    fake_team = {"team": {
        "id": "team-1", "name": "Team One", "users": [],
        "demo_video_url": "https://youtu.be/abc123",
        "devpost_link": "https://devpost.com/x",
    }}
    with patch.object(svc, "get_team", return_value=fake_team):
        result = svc.get_team_details("team-1")

    assert result["team"]["demo_video_url"] == "https://youtu.be/abc123"
    assert result["team"]["video_url"] == "https://youtu.be/abc123"


def test_get_team_details_video_url_falls_back_to_legacy_field_when_no_demo_video():
    fake_team = {"team": {"id": "team-1", "name": "Team One", "users": [], "video_url": "https://legacy.example.com/v.mp4"}}
    with patch.object(svc, "get_team", return_value=fake_team):
        result = svc.get_team_details("team-1")

    assert result["team"]["demo_video_url"] == ""
    assert result["team"]["video_url"] == "https://legacy.example.com/v.mp4"


def test_get_team_details_empty_when_no_video_at_all():
    fake_team = {"team": {"id": "team-1", "name": "Team One", "users": []}}
    with patch.object(svc, "get_team", return_value=fake_team):
        result = svc.get_team_details("team-1")

    assert result["team"]["demo_video_url"] == ""
    assert result["team"]["video_url"] == ""


def test_format_team_for_judge_surfaces_demo_video_url():
    team = {"id": "team-1", "name": "Team One", "users": [], "demo_video_url": "https://vimeo.com/123"}
    result = svc.format_team_for_judge(team)
    assert result["demo_video_url"] == "https://vimeo.com/123"
    assert result["video_url"] == "https://vimeo.com/123"


def test_format_team_for_judge_devpost_and_github_unchanged():
    """Confirms the fix is additive — existing judge-facing fields (github,
    devpost) are untouched by the video_url change."""
    team = {
        "id": "team-1", "name": "Team One", "users": [],
        "devpost_link": "https://devpost.com/x",
        "github_links": [{"link": "https://github.com/org/repo"}],
    }
    result = svc.format_team_for_judge(team)
    assert result["devpost_url"] == "https://devpost.com/x"
    assert result["github_url"] == "https://github.com/org/repo"


# ---------------------------------------------------------------------------
# #3 — get_bulk_judge_details no longer NameErrors into an always-empty result
# ---------------------------------------------------------------------------

def test_get_bulk_judge_details_returns_judges_after_namefix():
    judges_result = {"data": [{"id": "j1", "user_id": "u1", "name": "Judge One", "event_id": "event-1"}]}
    with patch.object(svc, "get_volunteer_from_db_by_event", return_value=judges_result), \
         patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=[]), \
         patch.object(svc, "fetch_judge_scores_by_event_id", return_value=[]) as mock_scores:
        result = svc.get_bulk_judge_details("event-1")

    # The bug: this call used to raise NameError before ever reaching here.
    mock_scores.assert_called_once_with("event-1")
    assert result.get("error") is None
    assert len(result["judges"]) == 1
    assert result["judges"][0]["id"] == "j1"


def test_get_bulk_judge_details_still_handles_service_error_gracefully():
    with patch.object(svc, "get_volunteer_from_db_by_event", return_value={"error": "boom"}), \
         patch.object(svc, "fetch_judge_assignments_by_event_id", return_value=[]), \
         patch.object(svc, "fetch_judge_scores_by_event_id", return_value=[]):
        result = svc.get_bulk_judge_details("event-1")

    assert result["judges"] == []
    assert "error" in result


# ---------------------------------------------------------------------------
# #4 — update_judge_assignment_details looks up by the assignment's own id
# ---------------------------------------------------------------------------

class _FakeAssignment:
    def __init__(self, assignment_id):
        self.id = assignment_id
        self.judge_id = "judge-1"
        self.event_id = "event-1"
        self.team_id = "team-1"
        self.round = "round1"
        self.demo_time = None
        self.room = None
        self.updated_at = None


def test_update_judge_assignment_details_finds_assignment_by_id():
    fake = _FakeAssignment("assignment-1")
    with patch.object(svc, "fetch_judge_assignment_by_id", return_value=fake) as mock_fetch, \
         patch.object(svc, "update_judge_assignment", side_effect=lambda a: a):
        result = svc.update_judge_assignment_details("assignment-1", demo_time="10:00 AM", room="Room A")

    mock_fetch.assert_called_once_with("assignment-1")
    assert result["success"] is True
    assert result["assignment"]["id"] == "assignment-1"
    assert result["assignment"]["demo_time"] == "10:00 AM"
    assert result["assignment"]["room"] == "Room A"


def test_update_judge_assignment_details_404_like_response_for_unknown_id():
    with patch.object(svc, "fetch_judge_assignment_by_id", return_value=None):
        result = svc.update_judge_assignment_details("nope")

    assert result["success"] is False
    assert result["error"] == "Assignment not found"


def test_update_judge_assignment_details_never_queries_by_empty_judge_id():
    """The regression itself: no code path here should call
    fetch_judge_assignments_by_judge_id("") anymore."""
    fake = _FakeAssignment("assignment-1")
    with patch.object(svc, "fetch_judge_assignment_by_id", return_value=fake), \
         patch.object(svc, "update_judge_assignment", side_effect=lambda a: a), \
         patch.object(svc, "fetch_judge_assignments_by_judge_id") as mock_by_judge_id:
        svc.update_judge_assignment_details("assignment-1", demo_time="9:00 AM")

    mock_by_judge_id.assert_not_called()

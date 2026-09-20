"""
Regression tests for MEDIUM finding #3: PATCH /api/team/edit
(api.teams.teams_service.edit_team) is documented (README "Ownership",
CLAUDE.md, submissions_service.py) as the admin override path for a team's
project_* write-up fields, but edit_team's field_mappings never actually
carried them. Covers: all seven project_* fields now flow through
field_mappings, project_submission_status is validated against the
draft/submitted/late catalog (400 on a bad value, no write), tagline/story
get the same sanitize_markdown treatment as the self-serve save_project
path, and a status CHANGE stamps project_updated_at (no stamp when
unchanged).
"""
import os
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import api.teams.teams_service as svc


def _wire(monkeypatch, existing_data):
    mock_snapshot = MagicMock()
    mock_snapshot.to_dict.return_value = dict(existing_data) if existing_data is not None else None
    mock_doc = MagicMock()
    mock_doc.get.return_value = mock_snapshot
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc
    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    monkeypatch.setattr(svc, "get_db", lambda: mock_db)
    monkeypatch.setattr(svc, "clear_cache", lambda: None)
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    return mock_doc


def test_edit_team_persists_project_submission_status(monkeypatch):
    mock_doc = _wire(monkeypatch, {"name": "Team A", "project_submission_status": "draft"})

    result = svc.edit_team({"id": "team-1", "project_submission_status": "submitted"})

    assert result["success"] is True
    written = mock_doc.set.call_args[0][0]
    assert written["project_submission_status"] == "submitted"
    assert "project_updated_at" in written


def test_edit_team_rejects_invalid_submission_status(monkeypatch):
    mock_doc = _wire(monkeypatch, {"name": "Team A"})

    result, status = svc.edit_team({"id": "team-1", "project_submission_status": "bogus"})

    assert status == 400
    assert result["success"] is False
    mock_doc.set.assert_not_called()


def test_edit_team_does_not_stamp_project_updated_at_when_status_unchanged(monkeypatch):
    mock_doc = _wire(monkeypatch, {"name": "Team A", "project_submission_status": "submitted"})

    svc.edit_team({"id": "team-1", "project_submission_status": "submitted"})

    written = mock_doc.set.call_args[0][0]
    assert "project_updated_at" not in written


def test_edit_team_sanitizes_project_tagline_and_story(monkeypatch):
    mock_doc = _wire(monkeypatch, {"name": "Team A"})

    svc.edit_team({
        "id": "team-1",
        "project_tagline": "Hi <script>alert(1)</script>",
        "project_story": "We used List<String> for the queue.",
    })

    written = mock_doc.set.call_args[0][0]
    assert "<script" not in written["project_tagline"]
    assert "List<String>" in written["project_story"]


def test_edit_team_persists_remaining_project_fields(monkeypatch):
    mock_doc = _wire(monkeypatch, {"name": "Team A"})

    svc.edit_team({
        "id": "team-1",
        "project_built_with": ["Python", "React"],
        "project_links": [{"label": "Repo", "url": "https://github.com/x/y"}],
        "project_thumbnail_url": "https://cdn.ohack.dev/teams/team-1/project/thumb.png",
        "project_images": ["https://cdn.ohack.dev/teams/team-1/project/1.png"],
    })

    written = mock_doc.set.call_args[0][0]
    assert written["project_built_with"] == ["Python", "React"]
    assert written["project_links"] == [{"label": "Repo", "url": "https://github.com/x/y"}]
    assert written["project_thumbnail_url"] == "https://cdn.ohack.dev/teams/team-1/project/thumb.png"
    assert written["project_images"] == ["https://cdn.ohack.dev/teams/team-1/project/1.png"]

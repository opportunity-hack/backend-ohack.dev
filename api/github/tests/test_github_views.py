"""
Route-level tests for the /issues org-required fix (Part 9 bug #7) and the
new /activity route.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from flask import Flask
from unittest.mock import MagicMock

from api.github import github_views


def _app():
    app = Flask(__name__)
    app.register_blueprint(github_views.bp)
    return app


def test_issues_requires_org(monkeypatch):
    client = _app().test_client()
    res = client.get("/api/github/issues?repo=some-repo")
    assert res.status_code == 400
    assert "org" in res.get_json()["error"]


def test_issues_requires_repo():
    client = _app().test_client()
    res = client.get("/api/github/issues?org=some-org")
    assert res.status_code == 400
    assert "repo" in res.get_json()["error"]


def test_issues_logs_issue_count_not_dict_key_count(monkeypatch, caplog):
    monkeypatch.setattr(
        github_views,
        "get_github_issues",
        lambda repo_name, org_name, state: {"success": True, "issues": [{"issue_number": 1}, {"issue_number": 2}]},
    )
    client = _app().test_client()
    with caplog.at_level("INFO"):
        res = client.get("/api/github/issues?org=o&repo=r")
    assert res.status_code == 200
    assert any("Retrieved 2 issues" in r.message for r in caplog.records)


def test_activity_route_requires_org_and_repo():
    client = _app().test_client()
    assert client.get("/api/github/activity?repo=r").status_code == 400
    assert client.get("/api/github/activity?org=o").status_code == 400


def test_activity_route_dispatches_to_service(monkeypatch):
    monkeypatch.setattr(github_views, "get_github_activity", MagicMock(return_value=({"success": True}, 200)))
    client = _app().test_client()
    res = client.get("/api/github/activity?org=o&repo=r")
    assert res.status_code == 200
    assert res.get_json()["success"] is True

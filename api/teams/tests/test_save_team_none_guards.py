"""Regression tests: save_team must not crash on missing Firestore docs.

Sentry (backend-ohack-prod, issue 7528843925, 5 events):
  TypeError: 'NoneType' object is not subscriptable

Root cause: two lookups in services.teams_service.save_team subscript a
value that can be None:
  - problem_statement.get().to_dict() is None when the problem-statement
    doc does not exist, so ["title"] crashed.
  - get_hackathon_by_event_id(...) returns None when no hackathon matches
    the event id, so ["id"] crashed.
Both paths now log the ids involved and return save_team's usual
{"message": "Error: ..."} result instead of raising.
"""
import importlib
import logging
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest

SERVICE_MODULE = "services.teams_service"

PAYLOAD = {
    "name": "Rockets",
    "slackChannel": "rockets",
    "eventId": "fall-2026",
    "problemStatementId": "ps-1",
    "githubUsername": "gregv",
}


def _stub_module(monkeypatch, name, path=None, **attrs):
    # Through monkeypatch so stubs are restored after each test instead of
    # leaking into later test files (PR #296 rule).
    mod = types.ModuleType(name)
    if path:
        mod.__path__ = path
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def svc(monkeypatch):
    # Stub the heavy import chain; services.teams_service itself loads for
    # real from the services package path.
    _stub_module(monkeypatch, "services", path=["services"])
    _stub_module(monkeypatch, "common", path=["common"])
    _stub_module(monkeypatch, "common.utils", path=["common/utils"])
    _stub_module(monkeypatch, "db", path=["db"])
    _stub_module(monkeypatch, "api", path=["api"])
    _stub_module(monkeypatch, "api.messages", path=["api/messages"])

    _stub_module(
        monkeypatch,
        "common.log",
        get_logger=lambda name: logging.getLogger(name),
    )
    _stub_module(
        monkeypatch,
        "common.utils.firestore_helpers",
        doc_to_json=MagicMock(),
        log_execution_time=lambda fn: fn,
        register_cache=lambda cache: None,
    )
    _stub_module(
        monkeypatch,
        "common.utils.slack",
        send_slack_audit=MagicMock(),
        send_slack=MagicMock(),
        create_slack_channel=MagicMock(),
        invite_user_to_channel=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "common.utils.github",
        create_github_repo=MagicMock(),
        validate_github_username=MagicMock(return_value=True),
        get_all_repos=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "common.utils.firebase",
        get_hackathon_by_event_id=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "common.utils.oauth_providers",
        extract_slack_user_id=MagicMock(),
        is_slack_user_id=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "db.db",
        get_db=MagicMock(),
        get_user_doc_reference=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "services.users_service",
        get_propel_user_details_by_id=MagicMock(),
        get_slack_user_from_propel_user_id=MagicMock(),
        get_user_from_slack_id=MagicMock(),
    )
    _stub_module(
        monkeypatch,
        "services.nonprofits_service",
        get_single_npo=MagicMock(),
    )

    class _Message:
        def __init__(self, message=""):
            self.message = message

    _stub_module(monkeypatch, "api.messages.message", Message=_Message)

    stub_firestore = types.ModuleType("firebase_admin.firestore")
    _stub_module(monkeypatch, "firebase_admin", firestore=stub_firestore)

    sys.modules.pop(SERVICE_MODULE, None)
    module = importlib.import_module(SERVICE_MODULE)
    yield module
    sys.modules.pop(SERVICE_MODULE, None)


def _wire(svc, monkeypatch, problem_statement_doc, hackathon):
    """Wire save_team's collaborators; the two lookups under test vary."""
    monkeypatch.setattr(svc, "send_slack_audit", lambda **kwargs: None)
    monkeypatch.setattr(
        svc,
        "get_propel_user_details_by_id",
        lambda pid: ("greg@example.com", "slack:U123", None, "img", "Greg", "greg"),
    )
    monkeypatch.setattr(svc, "extract_slack_user_id", lambda s: "U123")

    user_ref = MagicMock()
    user_snap = MagicMock()
    user_snap.to_dict.return_value = {"teams": [], "name": "Greg", "profile_image": "img"}
    user_ref.get.return_value = user_snap
    user_ref.id = "user-doc-1"
    monkeypatch.setattr(svc, "get_user_doc_reference", lambda sid: user_ref)

    monkeypatch.setattr(svc, "get_db", lambda: MagicMock())
    monkeypatch.setattr(svc, "validate_github_username", lambda u: True)

    ps_ref = MagicMock()
    ps_snap = MagicMock()
    ps_snap.to_dict.return_value = problem_statement_doc
    ps_ref.get.return_value = ps_snap
    monkeypatch.setattr(svc, "get_problem_statement_from_id_old", lambda psid: ps_ref)

    monkeypatch.setattr(svc, "get_hackathon_by_event_id", lambda eid: hackathon)

    create_repo = MagicMock(
        return_value={"full_url": "https://github.com/opportunity-hack/x", "repo_name": "x"}
    )
    monkeypatch.setattr(svc, "create_github_repo", create_repo)
    monkeypatch.setattr(svc, "create_slack_channel", lambda c: None)
    monkeypatch.setattr(svc, "invite_user_to_channel", lambda u, c: None)
    monkeypatch.setattr(svc, "send_slack", lambda m, c: None)
    monkeypatch.setattr(svc, "_clear_cache", lambda: None)
    monkeypatch.setattr(svc, "get_teams_list", lambda doc_id: {"id": doc_id})
    return create_repo


def test_missing_problem_statement_doc_returns_error_not_typeerror(svc, monkeypatch):
    create_repo = _wire(svc, monkeypatch, problem_statement_doc=None, hackathon={"id": "h1"})

    result = svc.save_team("propel-1", dict(PAYLOAD))

    assert isinstance(result, dict)
    assert "Problem Statement not found" in result["message"]
    # The guard fires before any GitHub/Slack side effects.
    create_repo.assert_not_called()


def test_missing_hackathon_returns_error_not_typeerror(svc, monkeypatch):
    _wire(svc, monkeypatch, problem_statement_doc={"title": "Clean Water"}, hackathon=None)

    result = svc.save_team("propel-1", dict(PAYLOAD))

    assert isinstance(result, dict)
    assert "Hackathon not found" in result["message"]

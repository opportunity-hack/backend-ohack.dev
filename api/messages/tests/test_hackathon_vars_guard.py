"""Route-level tests: hackathon views must return service error tuples as-is.

Sentry (backend-ohack-prod, issue 7773371562, 2 events):
  TypeError: vars() argument must have __dict__ attribute
  at api/messages/messages_views.py, in add_hackathon:
      return vars(save_hackathon(request.get_json(), user_id))

Root cause: save_hackathon returns a Message on success but an
({"error": ...}, status) tuple on validation failure or an unexpected
error. vars() only works on objects with __dict__, so every failed save
became a TypeError and a 500 instead of the intended 400/500 JSON body.
PATCH /hackathon already guards with isinstance(result, tuple); POST
/hackathon and PATCH /hackathon/problem_statements did not, and
update_hackathon_visible_problem_statements returns the same tuple shape.
"""
import functools
import importlib
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, g
from werkzeug.local import LocalProxy

VIEWS_MODULE = "api.messages.messages_views"


class FakeMessage:
    """Stand-in for api.messages.message.Message (vars() needs __dict__)."""

    def __init__(self, message):
        self.message = message


def _stub_module(monkeypatch, name, path=None, **attrs):
    # Through monkeypatch so stubs are restored after each test instead of
    # leaking into later test files (see PR #296).
    mod = types.ModuleType(name)
    if path:
        mod.__path__ = path
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _service_stub(monkeypatch, name, **funcs):
    attrs = {key: MagicMock(name=f"{name}.{key}") for key in funcs}
    return _stub_module(monkeypatch, name, **attrs)


@pytest.fixture
def views(monkeypatch):
    # Skip api/__init__.py (flask_cors, talisman, ...) and the heavy service
    # modules; the views under test only need the hackathon services mocked.
    _stub_module(monkeypatch, "api", path=["api"])
    _stub_module(monkeypatch, "api.messages", path=["api/messages"])
    _stub_module(monkeypatch, "services", path=["services"])
    _stub_module(monkeypatch, "common", path=["common"])
    _stub_module(
        monkeypatch,
        "common.log",
        get_logger=lambda name: MagicMock(name=f"logger:{name}"),
        debug=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    _service_stub(
        monkeypatch,
        "api.messages.messages_service",
        get_problem_statement_list_old=1,
        get_profile_metadata_old=1,
        get_public_message=1,
        get_protected_message=1,
        get_admin_message=1,
        get_single_problem_statement_old=1,
        get_user_by_id_old=1,
        save_helping_status_old=1,
        save_profile_metadata_old=1,
        save_problem_statement_old=1,
        get_all_profiles=1,
        get_github_profile=1,
    )
    _service_stub(
        monkeypatch,
        "services.news_service",
        save_news=1,
        get_news=1,
        get_all_praises=1,
        get_praises_about_user=1,
        save_praise=1,
        admin_create_news=1,
        admin_update_news=1,
        admin_delete_news=1,
        admin_list_news=1,
    )
    _service_stub(
        monkeypatch, "services.feedback_service", save_feedback=1, get_user_feedback=1
    )
    _service_stub(
        monkeypatch,
        "services.giveaway_service",
        save_giveaway=1,
        get_user_giveaway=1,
        get_all_giveaways=1,
    )
    _service_stub(monkeypatch, "services.onboarding_service", save_onboarding_feedback=1)
    _service_stub(
        monkeypatch,
        "services.teams_service",
        get_teams_list=1,
        get_team=1,
        get_teams_batch=1,
        get_teams_by_event_id=1,
        save_team=1,
        unjoin_team=1,
        join_team=1,
        get_github_repos=1,
    )
    _service_stub(monkeypatch, "services.email_service", save_lead_async=1)
    _service_stub(
        monkeypatch,
        "services.nonprofits_service",
        get_single_npo=1,
        get_npo_list=1,
        get_npo_by_hackathon_id=1,
        get_npos_by_hackathon_id=1,
        get_nonprofits_by_problem_statement_id=1,
        save_npo_legacy=1,
        update_npo_legacy=1,
        remove_npo_legacy=1,
        save_npo_application=1,
        get_npo_applications=1,
        update_npo_application=1,
    )
    _service_stub(
        monkeypatch,
        "services.hackathons_service",
        get_volunteer_checked_in_by_event=1,
        get_single_hackathon_event=1,
        single_add_volunteer=1,
        get_single_hackathon_id=1,
        add_nonprofit_to_hackathon=1,
        remove_nonprofit_from_hackathon=1,
        save_hackathon=1,
        create_hackathon=1,
        get_hackathon_request_by_id=1,
        update_hackathon_request=1,
        update_hackathon_volunteers=1,
        update_hackathon_visible_problem_statements=1,
        get_hackathon_list=1,
        get_volunteer_by_event=1,
        get_hackathon_funnel=1,
        get_hackathon_funnel_aggregate=1,
    )

    stub_auth = types.ModuleType("common.auth")

    def _passthrough(*_args, **_kwargs):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                g.propelauth_current_user = types.SimpleNamespace(user_id="u1")
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    stub_auth.auth = types.SimpleNamespace(
        require_user=_passthrough(),
        require_org_member_with_permission=_passthrough,
    )
    stub_auth.auth_user = LocalProxy(lambda: g.get("propelauth_current_user"))
    stub_auth.getOrgId = lambda req: req.headers.get("X-Org-Id")
    monkeypatch.setitem(sys.modules, "common.auth", stub_auth)

    sys.modules.pop(VIEWS_MODULE, None)
    module = importlib.import_module(VIEWS_MODULE)
    yield module
    sys.modules.pop(VIEWS_MODULE, None)


@pytest.fixture
def client(views):
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    return app.test_client()


def _mock_service(views, name, return_value):
    # The view imported the service function by name, so patch the name in
    # the view module's namespace.
    mock = MagicMock(return_value=return_value)
    setattr(views, name, mock)
    return mock


def test_add_hackathon_validation_error_returns_400_not_typeerror(views, client):
    _mock_service(views, "save_hackathon", ({"error": "title is required"}, 400))

    res = client.post("/api/messages/hackathon", json={"description": "x"})

    assert res.status_code == 400, (
        f"expected the service's 400 to pass through, got {res.status_code}: "
        "vars() on the error tuple raises TypeError and Flask answers 500"
    )
    assert res.get_json()["error"] == "title is required"


def test_add_hackathon_success_returns_message(views, client):
    _mock_service(views, "save_hackathon", FakeMessage("Saved Hackathon"))

    res = client.post("/api/messages/hackathon", json={"title": "Fall 2026"})

    assert res.status_code == 200
    assert res.get_json()["message"] == "Saved Hackathon"


def test_visible_problem_statements_missing_id_returns_400(views, client):
    _mock_service(
        views,
        "update_hackathon_visible_problem_statements",
        ({"error": "hackathonId is required"}, 400),
    )

    res = client.patch("/api/messages/hackathon/problem_statements", json={})

    assert res.status_code == 400
    assert res.get_json()["error"] == "hackathonId is required"


def test_visible_problem_statements_success_returns_message(views, client):
    _mock_service(
        views,
        "update_hackathon_visible_problem_statements",
        FakeMessage("Updated visible problem statements"),
    )

    res = client.patch(
        "/api/messages/hackathon/problem_statements",
        json={"hackathonId": "h1", "problemStatementIds": ["p1"]},
    )

    assert res.status_code == 200
    assert res.get_json()["message"] == "Updated visible problem statements"

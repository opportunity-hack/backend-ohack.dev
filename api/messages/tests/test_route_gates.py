"""
Route-level proof that admin/secret-gated routes actually reject anonymous
callers, using the REJECTING common.auth stub (no Authorization -> 401, no
X-Org-Id -> 403). Also covers the X-Api-Key token checks and the news
`limit` parsing on the legacy messages blueprint.
"""
import importlib
import os
import sys

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import rejecting_auth_module

MODULES = (
    "api.messages.messages_views",
    "api.problemstatements.problem_statement_views",
    "api.newsletters.newsletter_views",
)
ADMIN_HEADERS = {"Authorization": "Bearer x", "X-Org-Id": "org-1"}


@pytest.fixture
def load(monkeypatch):
    monkeypatch.setitem(sys.modules, "common.auth", rejecting_auth_module())
    loaded = []

    def _load(module_name):
        sys.modules.pop(module_name, None)
        views = importlib.import_module(module_name)
        loaded.append(module_name)
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app.test_client()

    yield _load
    for name in loaded:
        sys.modules.pop(name, None)


# --- 1.1 decorator order --------------------------------------------------

def test_checkins_list_requires_auth(load, monkeypatch):
    """Was 200 for anonymous callers: the auth decorators sat above @bp.route."""
    views, client = load("api.messages.messages_views")
    monkeypatch.setattr(views, "get_volunteer_checked_in_by_event", lambda e, t: {"data": []})
    assert client.get("/api/messages/hackathon/x/hacker/checkins").status_code == 401
    assert client.get("/api/messages/hackathon/x/hacker/checkins", headers=ADMIN_HEADERS).status_code == 200


def test_problem_statement_events_patch_requires_auth(load, monkeypatch):
    """Was reachable anonymously for the same decorator-order reason."""
    views, client = load("api.problemstatements.problem_statement_views")
    monkeypatch.setattr(views.service, "link_problem_statements_to_events", lambda body: None)
    assert client.patch("/api/problem-statements/events", json={}).status_code == 401
    assert client.patch("/api/problem-statements/events", json={}, headers={"Authorization": "Bearer x"}).status_code == 403


# --- 1.2 newsletter open relay --------------------------------------------

def test_send_newsletter_requires_admin(load, monkeypatch):
    """Before: the module built its own PropelAuth client at import (ValueError
    under the stub / test env), and send_newsletter had its auth commented out
    so anyone could POST arbitrary addresses+body (open mail relay, 200)."""
    views, client = load("api.newsletters.newsletter_views")
    sent = []
    monkeypatch.setattr(views, "send_newsletters", lambda **kw: sent.append(kw))
    body = {"addresses": ["a@x"], "body": "hi", "subject": "s", "role": "r"}
    assert client.post("/api/newsletter/send_newsletter", json=body).status_code == 401
    assert client.post("/api/newsletter/send_newsletter", json=body, headers={"Authorization": "Bearer x"}).status_code == 403
    assert sent == []
    assert client.post("/api/newsletter/send_newsletter", json=body, headers=ADMIN_HEADERS).status_code == 200
    assert len(sent) == 1


def test_newsletter_preview_and_lookup_require_admin(load, monkeypatch):
    views, client = load("api.newsletters.newsletter_views")
    monkeypatch.setattr(views, "check_subscription_list", lambda **kw: "ok")
    assert client.post("/api/newsletter/preview_newsletter", json={"body": "x"}).status_code == 401
    assert client.get("/api/newsletter/some-user").status_code == 401
    assert client.get("/api/newsletter/some-user", headers=ADMIN_HEADERS).status_code == 200


# --- 1.10 token checks + news limit ---------------------------------------

@pytest.fixture
def news_client(load, monkeypatch):
    monkeypatch.setenv("BACKEND_NEWS_TOKEN", "right-token")
    monkeypatch.setenv("BACKEND_PRAISE_TOKEN", "praise-token")
    views, client = load("api.messages.messages_views")
    calls = {"save": [], "get": [], "praise": []}

    class _R:
        pass

    def _save(body):
        calls["save"].append(body)
        return _R()

    def _get(news_limit, news_id):
        calls["get"].append(news_limit)
        return _R()

    def _praise(body):
        calls["praise"].append(body)
        return _R()

    monkeypatch.setattr(views, "save_news", _save)
    monkeypatch.setattr(views, "get_news", _get)
    monkeypatch.setattr(views, "save_praise", _praise)
    client.calls = calls
    return client


def test_news_post_token(news_client):
    assert news_client.post("/api/messages/news", json={}).status_code == 401
    assert news_client.post("/api/messages/news", json={}, headers={"X-Api-Key": "wrong"}).status_code == 401
    assert news_client.calls["save"] == []
    assert news_client.post("/api/messages/news", json={"a": 1}, headers={"X-Api-Key": "right-token"}).status_code == 200
    assert news_client.calls["save"] == [{"a": 1}]


def test_news_post_rejects_when_env_token_unset(news_client, monkeypatch):
    monkeypatch.delenv("BACKEND_NEWS_TOKEN")
    assert news_client.post("/api/messages/news", json={}, headers={"X-Api-Key": ""}).status_code == 401
    assert news_client.post("/api/messages/news", json={}, headers={"X-Api-Key": "None"}).status_code == 401


def test_praise_post_token(news_client):
    body = {"praise_sender": "a", "praise_receiver": "b"}
    assert news_client.post("/api/messages/praise", json=body).status_code == 401
    assert news_client.post("/api/messages/praise", json=body, headers={"X-Api-Key": "wrong"}).status_code == 401
    assert news_client.post("/api/messages/praise", json=body, headers={"X-Api-Key": "praise-token"}).status_code == 200
    assert len(news_client.calls["praise"]) == 1


def test_news_limit_parsing(news_client):
    """Was int(limit) with no guard -> 500 on ?limit=abc, and unbounded."""
    assert news_client.get("/api/messages/news?limit=abc").status_code == 400
    assert news_client.get("/api/messages/news?limit=abc").get_json() == {"error": "invalid_limit"}
    assert news_client.get("/api/messages/news?limit=99999").status_code == 200
    assert news_client.get("/api/messages/news?limit=0").status_code == 200
    assert news_client.get("/api/messages/news").status_code == 200
    assert news_client.calls["get"] == [200, 1, 3]

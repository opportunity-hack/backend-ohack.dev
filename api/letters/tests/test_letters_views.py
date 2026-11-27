"""Route tests for api/letters/letters_views.py — common.auth stubbed via
sys.modules (the real module boots PropelAuth at import), service stubbed.
Covers auth gates, owner/admin access, the public verify payload and the
view-signature == URL-params guard."""
import importlib
import inspect
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.letters.letters_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}
OWNER = types.SimpleNamespace(user_id="owner-1", email="owner@example.com")
OTHER = types.SimpleNamespace(user_id="other-1", email="other@example.com")


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module, is_admin=False):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        auth_module.user_has_any_permission = lambda user, perms: is_admin
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
        svc = views.svc

        def pdf_bytes(letter_id, propel_id, admin):
            if letter_id == "MISSINGMISSI":
                raise svc.LetterError(404, "not_found", "Letter not found.")
            if propel_id != "owner-1" and not admin:
                raise svc.LetterError(403, "forbidden", "This letter belongs to someone else.")
            return b"%PDF-1.4 stored", f"OpportunityHack-Letter-{letter_id}.pdf"
        monkeypatch.setattr(svc, "letter_pdf_bytes", pdf_bytes)
        monkeypatch.setattr(svc, "verify", lambda letter_id: {
            "name_on_letter": "Ada King", "role": "mentor", "event_title": "Fall 2026",
            "served_date": "November 14, 2026", "issued_at": "2026-11-27T18:00:00Z",
            "status": "issued"})
        monkeypatch.setattr(svc, "list_mine", lambda pid, email: {"rows": [], "caller": pid})
        monkeypatch.setattr(svc, "issue_mine", lambda pid, email, body: {
            "letter_id": "ABCDEFGHJKMN", "status": "issued", "created": True})
        monkeypatch.setattr(svc, "preview_mine", lambda *a: b"%PDF-1.4 preview")
        monkeypatch.setattr(svc, "admin_list", lambda *a: {"rows": []})
        monkeypatch.setattr(svc, "admin_preview", lambda *a: (
            b"%PDF-1.4 admin", {"X-Letter-Eligible": "true"}))
        monkeypatch.setattr(svc, "admin_revoke", lambda lid, reason, admin: {
            "letter_id": lid, "status": "revoked", "by": admin})
        monkeypatch.setattr(svc, "admin_exception", lambda body, admin: {
            "letter_id": "ABCDEFGHJKMN", "status": "issued"})
        app = Flask(__name__)
        app.register_blueprint(views.bp)
        return views, app.test_client()

    yield _load
    sys.modules.pop(VIEWS, None)


def test_every_view_signature_matches_its_url_params(load):
    views, _ = load(passthrough_auth_module())
    app = Flask(__name__)
    app.register_blueprint(views.bp)
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        params = set(inspect.signature(inspect.unwrap(app.view_functions[rule.endpoint])).parameters)
        assert params == set(rule.arguments), rule


def test_signed_in_routes_reject_anonymous(load):
    _, client = load(rejecting_auth_module(OWNER))
    assert client.get("/api/letters/mine").status_code == 401
    assert client.post("/api/letters", json={}).status_code == 401
    assert client.get("/api/letters/preview?event_id=e&role=mentor").status_code == 401
    assert client.get("/api/letters/ABCDEFGHJKMN/pdf").status_code == 401


def test_admin_routes_need_org_permission(load):
    _, client = load(rejecting_auth_module(OWNER))
    no_org = {"Authorization": "Bearer x"}
    assert client.get("/api/admin/letters?event_id=e", headers=no_org).status_code == 403
    assert client.get("/api/admin/letters/preview?user_id=u&event_id=e&role=mentor",
                      headers=no_org).status_code == 403
    assert client.post("/api/admin/letters/ABCDEFGHJKMN/revoke", json={"reason": "x"},
                       headers=no_org).status_code == 403
    assert client.post("/api/admin/letters/exception", json={}, headers=no_org).status_code == 403
    assert client.get("/api/admin/letters?event_id=e", headers=HEADERS).status_code == 200


def test_verify_is_public_and_leaks_nothing_else(load):
    _, client = load(rejecting_auth_module(OWNER))
    r = client.get("/api/letters/verify/ABCDEFGHJKMN")
    assert r.status_code == 200
    assert set(r.get_json()) == {"name_on_letter", "role", "event_title", "served_date",
                                 "issued_at", "status"}


def test_pdf_owner_ok_other_403_admin_ok(load):
    _, client = load(passthrough_auth_module(OWNER))
    r = client.get("/api/letters/ABCDEFGHJKMN/pdf", headers=HEADERS)
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.headers["Cache-Control"] == "private, no-store"
    assert "inline" in r.headers["Content-Disposition"]
    r = client.get("/api/letters/ABCDEFGHJKMN/pdf?download=1", headers=HEADERS)
    assert "attachment" in r.headers["Content-Disposition"]

    _, client = load(passthrough_auth_module(OTHER))
    r = client.get("/api/letters/ABCDEFGHJKMN/pdf", headers=HEADERS)
    assert r.status_code == 403
    assert r.get_json()["error"] == "forbidden"

    _, client = load(passthrough_auth_module(OTHER), is_admin=True)
    assert client.get("/api/letters/ABCDEFGHJKMN/pdf", headers=HEADERS).status_code == 200
    assert client.get("/api/letters/MISSINGMISSI/pdf", headers=HEADERS).status_code == 404


def test_issue_and_preview_routes(load):
    _, client = load(passthrough_auth_module(OWNER))
    assert client.post("/api/letters", data="nope", headers=HEADERS).status_code == 400
    r = client.post("/api/letters", json={"event_id": "e", "role": "mentor"}, headers=HEADERS)
    assert r.status_code == 201 and r.get_json()["letter_id"] == "ABCDEFGHJKMN"
    r = client.get("/api/letters/preview?event_id=e&role=mentor", headers=HEADERS)
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    assert client.get("/api/letters/mine", headers=HEADERS).get_json()["caller"] == "owner-1"


def test_admin_preview_exposes_verdict_headers(load):
    _, client = load(passthrough_auth_module(OWNER))
    r = client.get("/api/admin/letters/preview?user_id=u&event_id=e&role=mentor", headers=HEADERS)
    assert r.headers["X-Letter-Eligible"] == "true"
    assert "X-Letter-Eligible" in r.headers["Access-Control-Expose-Headers"]


def test_revoke_records_admin_from_token(load):
    _, client = load(passthrough_auth_module(OWNER))
    r = client.post("/api/admin/letters/ABCDEFGHJKMN/revoke", json={"reason": "x"},
                    headers=HEADERS)
    assert r.get_json()["by"] == "owner-1"

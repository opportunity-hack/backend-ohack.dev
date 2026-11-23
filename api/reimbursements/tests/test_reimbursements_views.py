"""Route tests for /api/reimbursements.

Follows api/checkin/tests/test_checkin_views.py: stub common.auth via
sys.modules (the real module builds a PropelAuth client at import) and stub
the service module, then dispatch through a bare Flask app. Covers the auth
gates (401 anonymous / 403 without X-Org-Id on admin routes), the public
routes staying open, and the view-signature == URL-params guard.
"""
import importlib
import os
import sys
import types

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask

from test.common.auth_stubs import passthrough_auth_module, rejecting_auth_module

VIEWS = "api.reimbursements.reimbursements_views"
HEADERS = {"Authorization": "Bearer <redacted>", "X-Org-Id": "org-1"}


def _service_stub():
    svc = types.ModuleType("api.reimbursements.reimbursements_service")
    svc.submit_reimbursement = lambda ip, data: (
        {"success": True, "id": "doc-1", "referenceId": "REIMB-2026-ABCDEF",
         "status": "submitted"}, 201)
    svc.mint_receipt_upload_url = lambda ip, data: (
        {"upload_url": "https://signed.example/put",
         "blob_path": "reimbursements/x/receipt_y.jpg",
         "final_url": "https://cdn.ohack.dev/reimbursements/x/receipt_y.jpg",
         "required_headers": {"Content-Type": "image/jpeg"}}, 200)
    svc.get_reimbursement_status = lambda email, ref: (
        {"referenceId": ref, "status": "submitted", "amountCents": 12345,
         "expenseDate": "2026-11-15", "updatedAt": "2026-11-16T00:00:00+00:00"}, 200)
    svc.admin_list_reimbursements = lambda **kw: ({"data": [], "count": 0}, 200)
    svc.admin_get_reimbursement = lambda doc_id: ({"id": doc_id, "status": "submitted"}, 200)
    svc.admin_review_reimbursement = lambda doc_id, data, reviewer: (
        {"success": True, "id": doc_id, "status": data.get("status")}, 200)
    return svc


@pytest.fixture
def load(monkeypatch):
    def _load(auth_module):
        monkeypatch.setitem(sys.modules, "common.auth", auth_module)
        stub = _service_stub()
        monkeypatch.setitem(
            sys.modules, "api.reimbursements.reimbursements_service", stub)
        import api.reimbursements as pkg
        monkeypatch.setattr(pkg, "reimbursements_service", stub, raising=False)
        sys.modules.pop(VIEWS, None)
        views = importlib.import_module(VIEWS)
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
        fn = app.view_functions[rule.endpoint]
        import inspect
        params = set(inspect.signature(inspect.unwrap(fn)).parameters)
        assert params == set(rule.arguments), rule


def test_public_routes_open_without_auth(load):
    _, client = load(rejecting_auth_module())
    r = client.post("/api/reimbursements", json={"submitterName": "Ada"})
    assert r.status_code == 201
    r = client.post("/api/reimbursements/receipt-upload-url",
                    json={"contentType": "image/jpeg"})
    assert r.status_code == 200
    r = client.get("/api/reimbursements/status?email=a@example.com&reference_id=REIMB-2026-ABCDEF")
    assert r.status_code == 200
    assert r.json["referenceId"] == "REIMB-2026-ABCDEF"


def test_public_post_empty_body_is_400(load):
    _, client = load(passthrough_auth_module())
    assert client.post("/api/reimbursements", json={}).status_code == 400
    assert client.post("/api/reimbursements/receipt-upload-url", json={}).status_code == 400


@pytest.mark.parametrize("method,path", [
    ("get", "/api/reimbursements/admin"),
    ("get", "/api/reimbursements/admin/doc-1"),
    ("patch", "/api/reimbursements/admin/doc-1"),
])
def test_admin_routes_reject_anonymous_and_orgless(load, method, path):
    _, client = load(rejecting_auth_module())
    call = getattr(client, method)
    kwargs = {"json": {"status": "approved"}} if method == "patch" else {}
    assert call(path, **kwargs).status_code == 401
    assert call(path, headers={"Authorization": "Bearer <redacted>"}, **kwargs).status_code == 403
    assert call(path, headers=HEADERS, **kwargs).status_code == 200


def test_admin_list_and_review_shape(load):
    _, client = load(passthrough_auth_module())
    r = client.get("/api/reimbursements/admin?status=submitted", headers=HEADERS)
    assert r.status_code == 200 and r.json == {"data": [], "count": 0}
    r = client.patch("/api/reimbursements/admin/doc-1",
                     json={"status": "approved", "reviewNote": "ok"}, headers=HEADERS)
    assert r.status_code == 200 and r.json["status"] == "approved"


def test_admin_patch_empty_body_is_400(load):
    _, client = load(passthrough_auth_module())
    assert client.patch("/api/reimbursements/admin/doc-1",
                        json={}, headers=HEADERS).status_code == 400

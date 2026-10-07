"""
upload_image_to_cdn(allow_overwrite=False) must refuse to replace an existing
blob — non-admin uploads used to silently overwrite whatever lived at
<directory>/<filename> (upload_to_cdn always overwrites by design; it's
shared with intentional overwriters, so the check lives in the caller).
"""
import io
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from flask import Flask, request

import api.messages.messages_service as svc


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(svc, "_optimize_image_for_web", lambda path: None, raising=False)
    return Flask(__name__)


def _call(app, monkeypatch, exists, allow_overwrite):
    uploaded, checked = [], []
    monkeypatch.setattr("common.utils.cdn.blob_exists", lambda d, f: checked.append((d, f)) or exists)
    monkeypatch.setattr(
        "common.utils.cdn.upload_to_cdn",
        lambda d, src, dest=None: uploaded.append((d, dest)) or f"https://cdn.test/{d}/{dest}",
    )
    with app.test_request_context(
        "/api/messages/upload-image",
        method="POST",
        data={"file": (io.BytesIO(b"x"), "pic.png"), "directory": "hackers"},
        content_type="multipart/form-data",
    ):
        return svc.upload_image_to_cdn(request, allow_overwrite=allow_overwrite), uploaded, checked


def test_existing_blob_is_409_without_overwrite(app, monkeypatch):
    result, uploaded, checked = _call(app, monkeypatch, exists=True, allow_overwrite=False)
    assert result == ({"success": False, "error": "file_exists"}, 409)
    assert uploaded == []
    assert checked == [("hackers", "pic.png")]


def test_new_blob_uploads_without_overwrite(app, monkeypatch):
    result, uploaded, _ = _call(app, monkeypatch, exists=False, allow_overwrite=False)
    assert result["success"] is True
    assert uploaded == [("hackers", "pic.png")]


def test_overwrite_allowed_skips_the_check(app, monkeypatch):
    result, uploaded, checked = _call(app, monkeypatch, exists=True, allow_overwrite=True)
    assert result["success"] is True
    assert uploaded == [("hackers", "pic.png")]
    assert checked == []

import os

import pytest
from flask import Flask

from api.health.health_views import CONFIGURED_FLAGS, bp


def make_app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(CONFIGURED_FLAGS) + [
        "COMMIT_SHA", "FLY_APP_NAME", "FLY_MACHINE_ID", "FLY_REGION",
        "REDIS_URL", "ENVIRONMENT", "FLASK_ENV",
    ]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("path", ["/api/health", "/api/health/"])
def test_health_returns_ok(clean_env, path):
    resp = make_app().test_client().get(path)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["worker_pid"] == os.getpid()
    assert body["cache_backend"] == "local"
    assert body["environment"] == "local"
    assert body["commit_sha"] is None
    assert resp.headers["Cache-Control"] == "no-store"
    for key in CONFIGURED_FLAGS.values():
        assert body[key] is False


def test_health_reports_presence_never_values(clean_env):
    clean_env.setenv("FLY_APP_NAME", "backend-ohack-prod")
    clean_env.setenv("FLY_MACHINE_ID", "d8d3d0da793138")
    clean_env.setenv("COMMIT_SHA", "abc123")
    clean_env.setenv("REDIS_URL", "redis://user:secret-pw@host:6379")
    clean_env.setenv("STRIPE_SECRET_KEY", "sk_live_super_secret")
    clean_env.setenv("RESEND_API_KEY", "")  # empty counts as not configured
    resp = make_app().test_client().get("/api/health")
    body = resp.get_json()
    assert body["environment"] == "production"
    assert body["machine_id"] == "d8d3d0da793138"
    assert body["commit_sha"] == "abc123"
    assert body["cache_backend"] == "redis"
    assert body["stripe_configured"] is True
    assert body["resend_configured"] is False
    raw = resp.get_data(as_text=True)
    assert "secret-pw" not in raw and "sk_live" not in raw


def test_health_staging_name(clean_env):
    clean_env.setenv("FLY_APP_NAME", "backend-ohack-staging")
    body = make_app().test_client().get("/api/health").get_json()
    assert body["environment"] == "staging"

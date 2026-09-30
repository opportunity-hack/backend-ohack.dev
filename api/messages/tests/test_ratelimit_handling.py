"""Rate limiter hardening (3.2): a tripped @limits returns 429 (not 500), and
get_npo_list's cache hits don't count against its limit."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from unittest.mock import MagicMock, patch

from flask import Flask
from ratelimit.exception import RateLimitException

import api.exception_views as exception_views
import services.nonprofits_service as npo_svc


def _app():
    app = Flask(__name__)
    app.register_blueprint(exception_views.bp)

    @app.route("/api/x")
    def _x():
        raise RateLimitException("too many calls", 12)

    @app.route("/api/y")
    def _y():
        raise RateLimitException("too many calls", 0)

    return app


def test_rate_limit_exception_returns_429_json_with_retry_after():
    resp = _app().test_client().get("/api/x")
    assert resp.status_code == 429
    assert resp.get_json() == {"error": "rate_limited"}
    assert resp.headers["Retry-After"] == "12"


def test_rate_limit_retry_after_defaults_when_period_elapsed():
    resp = _app().test_client().get("/api/y")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"


def test_get_npo_list_cache_hits_do_not_count_against_rate_limit():
    npo_svc._npo_list_cache.clear()
    db = MagicMock()
    db.collection.return_value.stream.return_value = []
    try:
        with patch.object(npo_svc, "_get_db", return_value=db):
            for _ in range(30):
                assert npo_svc.get_npo_list() == {"nonprofits": []}
        assert db.collection.return_value.stream.call_count == 1
    finally:
        npo_svc._npo_list_cache.clear()

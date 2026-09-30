from flask import Flask

from api.health.health_views import bp


def make_app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_health_no_trailing_slash():
    client = make_app().test_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_health_trailing_slash():
    client = make_app().test_client()
    resp = client.get("/api/health/")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}

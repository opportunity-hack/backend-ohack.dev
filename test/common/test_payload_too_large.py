"""An over-limit body must get a JSON 413 on /api/ paths (frontend callers
do res.json(); werkzeug's default is an HTML page)."""
import os

os.environ.setdefault("ENVIRONMENT", "test")

from flask import Flask, request

from api import exception_views


def test_413_is_json():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 10
    app.register_blueprint(exception_views.bp)

    @app.route("/api/x", methods=["POST"])
    def _x():
        # Werkzeug enforces MAX_CONTENT_LENGTH when the body is READ, so the
        # route must touch request.form; the response stays constant (echoing
        # the form keys here tripped CodeQL's reflected-XSS check).
        request.form  # noqa: B018 — forces body parsing -> 413
        return {"ok": True}

    resp = app.test_client().post("/api/x", data={"f": "x" * 100})
    assert resp.status_code == 413
    assert resp.get_json() == {"error": "payload_too_large", "max_bytes": 10}


def test_app_sets_max_content_length():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "api", "__init__.py")).read()
    assert 'app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024' in src

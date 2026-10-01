"""
Shared `common.auth` stand-ins for route-level tests.

Route tests import a views module against a bare Flask app with
`sys.modules["common.auth"]` replaced (the real module builds a PropelAuth
client at import, which raises under ENVIRONMENT=test). Two flavours:

- `passthrough_auth_module()` — every decorator lets the request through and
  sets the fake user (the pattern api/messages/tests/test_upload_image_gate.py
  started with).
- `rejecting_auth_module()` — behaves like PropelAuth at the edge: no
  `Authorization` header -> 401, no `X-Org-Id` header -> 403. Use it to prove
  a route is actually gated (a decorator placed ABOVE `@bp.route` is silently
  dropped by Flask, so the route answers 200 to anonymous callers).
"""
import functools
import types

from flask import g, request
from werkzeug.local import LocalProxy

DEFAULT_FAKE_USER = types.SimpleNamespace(user_id="fake-propel-uuid", email="fake@example.com")


def _module(auth_ns):
    stub = types.ModuleType("common.auth")
    stub.auth = auth_ns
    stub.auth_user = LocalProxy(lambda: getattr(g, "propelauth_current_user", None))
    return stub


def passthrough_auth_module(user=DEFAULT_FAKE_USER):
    def _passthrough(*_args, **_kwargs):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                g.propelauth_current_user = user
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    return _module(
        types.SimpleNamespace(
            require_org_member_with_permission=_passthrough,
            require_user=_passthrough(),
            optional_user=_passthrough(),
        )
    )


def rejecting_auth_module(user=DEFAULT_FAKE_USER):
    def require_user(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not request.headers.get("Authorization"):
                return {"error": "unauthorized"}, 401
            g.propelauth_current_user = user
            return fn(*args, **kwargs)

        return wrapper

    def require_org_member_with_permission(_perm, req_to_org_id=None):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                if not request.headers.get("X-Org-Id"):
                    return {"error": "forbidden"}, 403
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    def optional_user(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if request.headers.get("Authorization"):
                g.propelauth_current_user = user
            return fn(*args, **kwargs)

        return wrapper

    return _module(
        types.SimpleNamespace(
            require_user=require_user,
            require_org_member_with_permission=require_org_member_with_permission,
            optional_user=optional_user,
        )
    )

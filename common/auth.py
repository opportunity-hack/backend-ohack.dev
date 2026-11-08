from common.utils import safe_get_env_var

PROPEL_AUTH_URL = safe_get_env_var("PROPEL_AUTH_URL")
PROPEL_AUTH_KEY = safe_get_env_var("PROPEL_AUTH_KEY")

from propelauth_flask import init_auth, current_user

auth = init_auth(
    auth_url=PROPEL_AUTH_URL,
    api_key=PROPEL_AUTH_KEY
)

auth_user = current_user

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")


# --- Combined permission gates ---------------------------------------------
#
# propelauth_flask ships `require_org_member_with_permission` (ONE permission)
# and `require_org_member_with_all_permissions` (ALL of a list) but no any-of
# variant. `require_any_permission` fills that gap for routes that a narrower
# role may also use (e.g. the registration desk: `volunteer.admin` OR
# `checkin.admin`). It mirrors `is_admin` in hackathon_planning_service.
#
# NOTE: route tests replace this whole module via sys.modules (see
# test/common/auth_stubs.py) — keep the stub namespaces in sync when adding
# names here.

import functools as _functools

from flask import abort as _abort, request as _request

# `checkin.admin` = registration-desk helpers: check people in, read the lean
# roster, nothing else. `volunteer.admin` is treated as a superset everywhere.
CHECKIN_PERMISSIONS = ("volunteer.admin", "checkin.admin")


def user_has_any_permission(propel_user, permissions) -> bool:
    """True when the PropelAuth user holds ANY of `permissions` in ANY org.

    OHack has a single org, so any membership counts. Falls back to the raw
    `user_permissions` list when the OrgMemberInfo object lacks the method
    (older propelauth_py versions / test doubles).
    """
    if not propel_user or not getattr(propel_user, "user_id", None):
        return False
    wanted = tuple(permissions)
    org_map = getattr(propel_user, "org_id_to_org_member_info", None) or {}
    for org_info in org_map.values():
        try:
            if any(org_info.user_has_permission(p) for p in wanted):
                return True
        except Exception:
            granted = getattr(org_info, "user_permissions", None) or []
            if any(p in granted for p in wanted):
                return True
    return False


def require_any_permission(permissions, req_to_org_id=None):
    """Decorator: `@auth.require_user` + 403 unless the user has any of `permissions`.

    `req_to_org_id` keeps the same shape as the PropelAuth decorators (pass
    `getOrgId`); a missing/empty org id answers 403 like they do.
    """
    wanted = tuple(permissions)

    def decorator(fn):
        @_functools.wraps(fn)
        @auth.require_user
        def wrapper(*args, **kwargs):
            if req_to_org_id is not None and not req_to_org_id(_request):
                _abort(403)
            if not user_has_any_permission(auth_user, wanted):
                _abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator

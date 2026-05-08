"""Auth helpers for the per-hackathon planning board.

Two decorators are exported:
- ``require_plan_editor(event_id_arg)``: gates editor-write routes. Combines
  ``@auth.require_user`` with: (a) hackathon doc lookup, (b) ``planning.enabled``
  check, (c) admin-or-editor check. Stashes the loaded doc on
  ``flask.g.hackathon`` for handlers.
- ``require_logged_in_on_enabled_plan(event_id_arg)``: lighter wrapper for the
  comment-write route — any logged-in user may comment on an enabled board.

The editor list is intentionally NOT cached. Removal mid-session must take
effect on the next mutation, so every call re-reads the parent hackathon doc.
"""

import logging
from functools import wraps

from flask import abort, g

from common.auth import auth, auth_user
from common.utils.firebase import get_hackathon_by_event_id

logger = logging.getLogger("hackathon_planning_service")

ADMIN_PERMISSION = "volunteer.admin"


def is_admin(propel_user) -> bool:
    """Return True if the authenticated user has the global volunteer.admin permission.

    PropelAuth's flask SDK exposes per-org info on ``current_user.org_id_to_org_info``.
    OHack only has one org ("Opportunity Hack Org") so we accept ``volunteer.admin`` in
    any org membership. If the user has no org memberships at all, they are not admin.
    """
    if not propel_user or not getattr(propel_user, "user_id", None):
        return False
    org_id_to_org_info = getattr(propel_user, "org_id_to_org_info", None) or {}
    for org_info in org_id_to_org_info.values():
        if not isinstance(org_info, dict):
            continue
        permissions = org_info.get("user_permissions") or []
        if ADMIN_PERMISSION in permissions:
            return True
    return False


def can_write_plan(propel_user, hackathon_doc) -> bool:
    """Return True if the user may edit the planning board for this hackathon.

    Truth table:
        anonymous           -> False
        logged-in non-editor non-admin -> False
        logged-in editor    -> True iff propel_user_id is in planning.editors[]
        logged-in admin     -> True regardless of editors list
    """
    if not propel_user or not getattr(propel_user, "user_id", None):
        return False
    if is_admin(propel_user):
        return True
    if not isinstance(hackathon_doc, dict):
        return False
    planning = hackathon_doc.get("planning") or {}
    editors = planning.get("editors") or []
    return propel_user.user_id in editors


def can_comment(propel_user) -> bool:
    """Any logged-in user can comment on an enabled plan."""
    return bool(propel_user and getattr(propel_user, "user_id", None))


def load_hackathon_or_404(event_id):
    """Read the parent hackathon doc by event_id; 404 if missing."""
    if not isinstance(event_id, str) or not event_id.strip():
        abort(404)
    doc = get_hackathon_by_event_id(event_id)
    if not doc:
        abort(404)
    return doc


def _planning_enabled(hackathon_doc) -> bool:
    return bool((hackathon_doc.get("planning") or {}).get("enabled"))


def require_plan_editor(event_id_arg="event_id"):
    """Decorator: requires logged-in editor (or admin) for the named event.

    ``planning.enabled`` is enforced — disabled boards return 404 (not 403) so
    probing for boards is indistinguishable from probing for nonexistent events.

    The loaded hackathon dict is stashed on ``flask.g.hackathon`` so handlers
    can construct Firestore paths from ``g.hackathon["id"]`` rather than
    trusting parent IDs in the request body. This blocks cross-event mutations
    where a card_id from event B is sent against event A's URL.
    """

    def decorator(fn):
        @wraps(fn)
        @auth.require_user
        def inner(*args, **kwargs):
            event_id = kwargs.get(event_id_arg)
            doc = load_hackathon_or_404(event_id)
            if not _planning_enabled(doc):
                abort(404)
            if not can_write_plan(auth_user, doc):
                abort(403)
            g.hackathon = doc
            g.propel_user_id = auth_user.user_id
            return fn(*args, **kwargs)

        return inner

    return decorator


def require_logged_in_on_enabled_plan(event_id_arg="event_id"):
    """Decorator: requires logged-in user; planning.enabled enforced (404 if off)."""

    def decorator(fn):
        @wraps(fn)
        @auth.require_user
        def inner(*args, **kwargs):
            event_id = kwargs.get(event_id_arg)
            doc = load_hackathon_or_404(event_id)
            if not _planning_enabled(doc):
                abort(404)
            if not can_comment(auth_user):
                abort(403)
            g.hackathon = doc
            g.propel_user_id = auth_user.user_id
            return fn(*args, **kwargs)

        return inner

    return decorator


def require_admin_on_event(event_id_arg="event_id"):
    """Decorator for admin-only planning routes (editors-list management, seed-template).

    Unlike ``require_plan_editor``, this also enforces global admin. Editors cannot
    add other editors or seed templates.
    """

    def decorator(fn):
        @wraps(fn)
        @auth.require_user
        def inner(*args, **kwargs):
            event_id = kwargs.get(event_id_arg)
            doc = load_hackathon_or_404(event_id)
            if not is_admin(auth_user):
                abort(403)
            g.hackathon = doc
            g.propel_user_id = auth_user.user_id
            return fn(*args, **kwargs)

        return inner

    return decorator

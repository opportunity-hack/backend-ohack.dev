"""Participation letter routes. Rules + data model: api/letters/README.md.

Signed-in (owner):
  GET  /api/letters/mine                  eligibility rows for the caller
  POST /api/letters                       issue (idempotent per event+role)
  GET  /api/letters/preview?event_id=&role=&name=&show_title_line=
                                          watermarked PDF, no signature / ID
  GET  /api/letters/<letter_id>/pdf       owner or admin, issued only
Public:
  GET  /api/letters/verify/<letter_id>
Admin (volunteer.admin):
  GET  /api/admin/letters?event_id=&status=&role=
  GET  /api/admin/letters/preview?user_id=&event_id=&role=
  POST /api/admin/letters/<letter_id>/revoke  {reason}
  POST /api/admin/letters/exception           {user_id|email, event_id, role, note}

View params == URL params only (PropelAuth decorators inject nothing);
identity comes from `auth_user` inside the body. Admin routes pass
`req_to_org_id=getOrgId`.
"""
from flask import Blueprint, Response, request

from common.auth import auth, auth_user, getOrgId, user_has_any_permission
from common.log import get_logger
from api.letters import letters_service as svc

logger = get_logger(__name__)

bp = Blueprint("letters", __name__, url_prefix="/api")

ADMIN_PERMISSION = "volunteer.admin"


def _caller():
    user = auth_user
    return getattr(user, "user_id", None), getattr(user, "email", None)


def _error(e: "svc.LetterError"):
    return e.body(), e.status


def _internal(where: str):
    logger.exception("letters: %s failed", where)
    return {"error": "internal_error", "message": "Something went wrong. Try again."}, 500


def _pdf_response(pdf: bytes, filename: str, inline: bool = True, headers=None):
    disposition = "inline" if inline else "attachment"
    resp = Response(pdf, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    if headers:
        resp.headers["Access-Control-Expose-Headers"] = ", ".join(headers.keys())
    return resp


@bp.route("/letters/mine", methods=["GET"])
@auth.require_user
def my_letters():
    propel_id, email = _caller()
    try:
        return svc.list_mine(propel_id, email), 200
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("list_mine")


@bp.route("/letters", methods=["POST"])
@auth.require_user
def request_letter():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return {"error": "missing_fields", "message": "Empty request body"}, 400
    propel_id, email = _caller()
    try:
        result = svc.issue_mine(propel_id, email, body)
        return result, 201 if result.get("created") else 200
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("issue_mine")


@bp.route("/letters/preview", methods=["GET"])
@auth.require_user
def preview_letter():
    propel_id, email = _caller()
    args = request.args
    try:
        pdf = svc.preview_mine(propel_id, email, args.get("event_id"), args.get("role"),
                               args.get("name"), args.get("show_title_line") == "true")
        return _pdf_response(pdf, "OpportunityHack-Letter-preview.pdf")
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("preview_mine")


@bp.route("/letters/<letter_id>/pdf", methods=["GET"])
@auth.require_user
def letter_pdf(letter_id):
    propel_id, _ = _caller()
    try:
        is_admin = user_has_any_permission(auth_user, (ADMIN_PERMISSION,))
        pdf, filename = svc.letter_pdf_bytes(letter_id, propel_id, is_admin)
        return _pdf_response(pdf, filename, inline=request.args.get("download") != "1")
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("letter_pdf")


@bp.route("/letters/verify/<letter_id>", methods=["GET"])
def verify_letter(letter_id):
    try:
        return svc.verify(letter_id), 200
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("verify")


@bp.route("/admin/letters", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission(ADMIN_PERMISSION, req_to_org_id=getOrgId)
def admin_list_letters():
    args = request.args
    try:
        return svc.admin_list(args.get("event_id"), args.get("status") or None,
                              args.get("role") or None), 200
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("admin_list")


@bp.route("/admin/letters/preview", methods=["GET"])
@auth.require_user
@auth.require_org_member_with_permission(ADMIN_PERMISSION, req_to_org_id=getOrgId)
def admin_preview_letter():
    args = request.args
    try:
        pdf, headers = svc.admin_preview(args.get("user_id"), args.get("event_id"),
                                         args.get("role"))
        return _pdf_response(pdf, "OpportunityHack-Letter-admin-preview.pdf", headers=headers)
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("admin_preview")


@bp.route("/admin/letters/<letter_id>/revoke", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission(ADMIN_PERMISSION, req_to_org_id=getOrgId)
def admin_revoke_letter(letter_id):
    body = request.get_json(silent=True) or {}
    propel_id, _ = _caller()
    try:
        return svc.admin_revoke(letter_id, body.get("reason"), propel_id), 200
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("admin_revoke")


@bp.route("/admin/letters/exception", methods=["POST"])
@auth.require_user
@auth.require_org_member_with_permission(ADMIN_PERMISSION, req_to_org_id=getOrgId)
def admin_letter_exception():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return {"error": "missing_fields", "message": "Empty request body"}, 400
    propel_id, _ = _caller()
    try:
        return svc.admin_exception(body, propel_id), 201
    except svc.LetterError as e:
        return _error(e)
    except Exception:
        return _internal("admin_exception")

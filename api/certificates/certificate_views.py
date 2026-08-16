from typing import Dict

from flask import Blueprint
from flask import request
from api.certificates.certificate_service import generate_certificate, validateCertificate, generate_certificate_from_slack, get_cert_info, get_recent_certs, get_certificates_by_github_username
from common.auth import auth, auth_user

bp_name = "api-certificates"
bp_url_prefix = "/api/certificates"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

ADMIN_PERMISSION = "volunteer.admin"


def _user_has_admin_permission(propel_user):
    """True when the logged-in user holds volunteer.admin in any org.

    Same pattern as services/hackathon_planning_service.is_admin — OHack has a
    single org, so any-org membership is sufficient.
    """
    if not propel_user or not getattr(propel_user, "user_id", None):
        return False
    org_id_to_org_member_info = (
        getattr(propel_user, "org_id_to_org_member_info", None) or {}
    )
    for org_info in org_id_to_org_member_info.values():
        try:
            if org_info.user_has_permission(ADMIN_PERMISSION):
                return True
        except Exception:
            permissions = getattr(org_info, "user_permissions", None) or []
            if ADMIN_PERMISSION in permissions:
                return True
    return False


@bp.route("/generate", methods=["POST"])
@auth.require_user
def generateCertificate():
    form = request.get_json()
    if "slack_channel" in form:
        # Batch mode clones every team repo and notifies people via Slack +
        # email — admin only.
        if not _user_has_admin_permission(auth_user):
            return {"error": "Admin permission required for batch generation"}, 403
        return {"images": generate_certificate_from_slack(slack_channel=form["slack_channel"])}


    if ("repoURL" not in form or "username" not in form): return {}
    repoUrl: str = form["repoURL"]
    username: str = form["username"]
    return {"img data": generate_certificate(repoUrl, username)}

@bp.route("/verify", methods=["POST"])
def verifyCertificate():
    form = request.get_json()
    if ("image" not in form): return {}
    imgData = form["image"]
    return {"valid": validateCertificate(imgData)}

@bp.route("/<id>", methods=["GET"])
def getCert(id):    
    return get_cert_info(id)

@bp.route("/recent", methods=["GET"])
def getRecentCerts():
    return {
        "certs": get_recent_certs()
    }

@bp.route("", methods=["GET"])
def getCertsByGithub():
    """GET /api/certificates?github=<username> — a user's certificates."""
    github = request.args.get("github", "").strip()
    if not github:
        return {"error": "github query param is required"}, 400
    return {"certs": get_certificates_by_github_username(github)}
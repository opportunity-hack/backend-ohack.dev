from flask import Blueprint
from flask import request
from api.certificates.certificate_service import generate_certificate, validateCertificate

bp_name = "api-certificates"
bp_url_prefix = "/api/certificates"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

@bp.route("/generate/<string:username>/<path:repoUrl>", methods=["POST"])
def generateCertificate(repoUrl: str, username: str):
    form = request.form
    if ("repoURL" not in form or "username" not in form): return {}
    repoUrl: str = form["repoURL"]
    username: str = form["username"]
    return {"img data": generate_certificate(repoUrl, username)}

@bp.route("/verify/<string:username>/<path:repoUrl>", methods=["POST"])
def verifyCertificate():
    form = request.form
    if ("image" not in form): return {}
    imgData = form["image"]
    return {"img data": validateCertificate(imgData)}
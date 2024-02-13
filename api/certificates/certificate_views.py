from typing import Dict

from flask import Blueprint
from flask import request
from api.certificates.certificate_service import generate_certificate, validateCertificate, generate_certificate_from_slack, get_cert_info, get_recent_certs

bp_name = "api-certificates"
bp_url_prefix = "/api/certificates"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

@bp.route("/generate", methods=["POST"])
def generateCertificate():        
    form = request.get_json()
    if "slack_channel"  in form:
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
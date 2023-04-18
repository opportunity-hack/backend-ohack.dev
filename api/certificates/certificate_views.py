from flask import Blueprint
from api.certificates.certificate_service import generate_certificate

bp_name = "api-certificates"
bp_url_prefix = "/api/certificates"
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

@bp.route("/generate")
def generateCertificate():
    return {"img data": generate_certificate(None).decode()}
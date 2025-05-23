from api.newsletters.newsletter_service import address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list
from .smtp import send_newsletters,format_message
import json
import os
from common.log import get_logger, info, debug, warning, error, exception
from flask import (
    Blueprint,
    request
)

from propelauth_flask import init_auth, current_user
auth = init_auth(
    os.getenv("PROPEL_AUTH_URL"),
    os.getenv("PROPEL_AUTH_KEY"),
)    


bp_name = 'api-newsletter'
bp_url_prefix = '/api/newsletter'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

logger = get_logger("newsletter_views")


@bp.route("/")
@auth.require_user
@auth.require_org_member_with_permission("admin_permissions")
def newsletter():
    return get_subscription_list()


@bp.route("/<user_id>")
# @auth.require_user
# @auth.require_org_member_with_permission("admin_permissions")
def check_sub(user_id):
    return check_subscription_list(user_id=user_id)

@bp.route("/send_newsletter", methods=["POST"])
# @auth.require_user
# @auth.require_org_member_with_permission("admin_permissions")
def send_newsletter():
    data = request.get_json()
    try:
        info(logger, "Subscription list", addresses=data["addresses"])
        send_newsletters(addresses=data["addresses"],message=data["body"],subject=data["subject"],role=data["role"])
    except  Exception as e:
        exception(logger, "Error getting subscription list", exc_info=e)
        return "False" 
    return "True"

@bp.route("/preview_newsletter", methods=["POST"])
def preview_newsletter():
    debug(logger, "Sending newsletter")
    data = request.get_json()
    try:
        # logger.info(data["body"])
        content = format_message(message=data["body"], address={"id": "A_Random_user_id"})
    except  Exception as e:
        exception(logger, "Error sending newsletter", exc_info=e)
        content =  "Error".format(str(e))
    return content


@bp.route("/<subscribe>/<doc_id>", methods=["POST"])
@auth.require_user
# @auth.require_org_member_with_permission("admin_permissions")
def newsletter_signup(subscribe, doc_id):
    debug(logger, "User authorized")
    if subscribe == "subscribe":
        return add_to_subscription_list(doc_id)
    elif subscribe == "verify":
        # returns a boolean
        return check_subscription_list(doc_id)
    elif subscribe == "unsubscribe":
        return remove_from_subscription_list(doc_id)
    else: 
        return "errors"
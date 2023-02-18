from api.newsletters.newsletter_service import address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list
from .smtp import send_newsletters,format_message
import json

import logging
from flask import (
    Blueprint,
    request
)
from api.security.guards import (
    authorization_guard,
    permissions_guard,
    admin_messages_permissions
)


bp_name = 'api-newsletter'
bp_url_prefix = '/api/newsletter'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

logger = logging.getLogger("myapp")


@bp.route("/")
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def newsletter():
    return get_subscription_list()


@bp.route("/<user_id>")
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def check_sub(user_id):
    return check_subscription_list(user_id=user_id)

@bp.route("/send_newsletter", methods=["POST"])
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def send_newsletter():
    data = request.get_json()
    try:
        logger.info(data["addresses"])
        send_newsletters(addresses=data["addresses"],message=data["body"],subject=data["subject"],role=data["role"])
    except  Exception as e:
        logger.debug("Error" + (str(e)))
        return "False" 
    return "True"

@bp.route("/preview_newsletter", methods=["POST"])
def preview_newsletter():
    logger.debug("running")
    data = request.get_json()
    try:
        # logger.info(data["body"])
        content = format_message(message=data["body"], address={"id": "A_Random_user_id"})
    except  Exception as e:
        logger.debug(str(e))
        content =  "Error".format(str(e))
    return content


@bp.route("/<subscribe>/<doc_id>", methods=["POST"])
@authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter_signup(subscribe, doc_id):
    print("authorized")
    if subscribe == "subscribe":
        return add_to_subscription_list(doc_id)
    elif subscribe == "verify":
        # returns a boolean
        return check_subscription_list(doc_id)
    elif subscribe == "unsubscribe":
        return remove_from_subscription_list(doc_id)
    else: 
        return "errors"
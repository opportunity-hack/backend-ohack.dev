from api.newsletters.newsletter_service import(address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list)
from api.newsletters.smtp import(send_newsletters)
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

@bp.route("/<subscribe>/<user_id>", methods=["GET"])
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter_signup(subscribe, user_id):
    logger.debug("subscribing user >>>>>"+user_id)
    if subscribe == "subscribe":
        return add_to_subscription_list(user_id)
    elif subscribe == "verify":
        # returns a boolean
        return check_subscription_list(user_id)
    else:
        return remove_from_subscription_list(user_id)

@bp.route("/send_newsletter", methods=["POST"])
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def send_newsletter():
    # addresses = get_subscription_list()["active"]
    # logger.debug(addresses)?
    data   = request.get_json()

    logger.debug(data)
    try:
        send_newsletters(addresses=data["addresses"],message=data["body"],subject=data["subject"],is_html=data["is_html"])
    except  Exception as e:
        logger.debug(e)
        return "some error"
    return "True"

@bp.route("/unsubscribe/<email_address>", methods=["GET"])
def unsubscribe(email_address):
    logger.debug('unsubscribe user')
    remove_from_subscription_list(email_address)
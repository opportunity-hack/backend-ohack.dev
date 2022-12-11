from api.newsletters.newsletter_service import(address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list)
from api.newsletters.smtp import(send_newsletters)

import logging
from flask import (
    Blueprint,
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
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
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
    if subscribe == "subscribe":
        return add_to_subscription_list(user_id)
    elif subscribe == "verify":
        return check_subscription_list(user_id)
    else:
        return remove_from_subscription_list(user_id)

@bp.route("/send_newsletter")
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def send_newsletter():
    # addresses = get_subscription_list()

# !SECTION testing output from get_subscription_list
    addresses = []
    addresses.append(address("leon1@mailinator.com","leon"))
    addresses.append(address("leon2@mailinator.com","crying"))
    addresses.append(address("leon3@mailinator.com","laughing"))
###########################

    try:
        send_newsletters(addresses=addresses,message="#hello",subject="first topic",is_html=False)
    except  Exception as e:
        logger.debug(f"get_profile_metadata {e}")
        return "some error"
    return "True"
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
    if subscribe == "subscribe":
        return add_to_subscription_list(user_id)
    elif subscribe == "verify":
        return check_subscription_list(user_id)
    else:
        return remove_from_subscription_list(user_id)

dicti ={
    "subject": "ths is test4",
    "body" : """
                       <p>     Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod
                            tempor incididunt ut labore et dolore magna aliqua. Vel facilisis volutpat
                            est velit egestas dui. Ornare arcu odio ut sem nulla pharetra diam sit.
                            Curabitur vitae nunc sed velit dignissim. Lacus suspendisse faucibus
                            interdum posuere. Cras adipiscing enim eu turpis egestas pretium aenean
                            pharetra. Pellentesque elit eget gravida cum sociis natoque penatibus et.
                            Fermentum dui faucibus in ornare. Vel elit scelerisque mauris
                            pellentesque. Duis at consectetur lorem donec massa sapien. Ac odio tempor
                            orci dapibus ultrices in iaculis nunc sed. Mi in nulla posuere
                            sollicitudin aliquam ultrices. Nibh praesent tristique magna sit amet
                            purus gravida quis. Arcu odio ut sem nulla. Odio facilisis mauris sit amet
                            massa. Laoreet non curabitur gravida arcu ac tortor dignissim </p>
                   
    """,
    "is_html": True
}
jsonObj = json.dumps(dicti)

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
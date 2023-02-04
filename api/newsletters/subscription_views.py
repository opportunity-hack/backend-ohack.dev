from api.newsletters.newsletter_service import(address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list)
from api.newsletters.smtp import (send_newsletters,format_message)
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



bp_name = 'api-newsletter-subscription'
bp_url_prefix = '/api/newsletter-subs'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

logger = logging.getLogger("myapp")

@bp.route("/")
@authorization_guard
@permissions_guard([admin_messages_permissions.read])
def test_net():
    print("lolololo")
    return {"true": "false"}

@bp.route("/subscribe/<doc_id>", methods=["POST"])
@authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter_sub(doc_id):
    return add_to_subscription_list(doc_id)

@bp.route("/verify/<doc_id>", methods=["POST"])
@authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def verify_sub(doc_id):
    return check_subscription_list(doc_id)

@bp.route("/unsubscribe/<doc_id>", methods=["POST"])
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter_un_sub(doc_id):
    return remove_from_subscription_list(doc_id)

# @bp_subs.route("/<subscribe>/<doc_id>", methods=["POST"])
# @authorization_guard
# # @permissions_guard([admin_messages_permissions.read])
# def newsletter_signup(subscribe, doc_id):
#     print("authorized")
#     if subscribe == "subscribe":
#         return add_to_subscription_list(doc_id)
#     elif subscribe == "verify":
#         # returns a boolean
#         return check_subscription_list(doc_id)
#     elif subscribe == "unsubscribe":
#         return remove_from_subscription_list(doc_id)
#     else: 
#         return "errors"

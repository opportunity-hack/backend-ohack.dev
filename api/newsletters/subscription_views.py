from api.newsletters.newsletter_service import(address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list)
from api.newsletters.smtp import (send,format_message)
import json

import logging
from flask import (
    Blueprint
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
    return {"true": "false"}

@bp.route("/subscribe/<doc_id>", methods=["POST"])
@authorization_guard
def newsletter_sub(doc_id):
    return add_to_subscription_list(doc_id)

@bp.route("/verify/<doc_id>", methods=["POST"])
@authorization_guard
def verify_sub(doc_id):
    return check_subscription_list(doc_id)

@bp.route("/unsubscribe/<doc_id>", methods=["POST"])
@authorization_guard
def newsletter_un_sub(doc_id):
    return remove_from_subscription_list(doc_id)
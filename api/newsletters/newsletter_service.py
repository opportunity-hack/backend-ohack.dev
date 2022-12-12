import logging
from ratelimit import limits

from api.messages.messages_service import (get_db, get_user_from_slack_id, ONE_MINUTE)

logger = logging.getLogger("myapp")

class address:
    def __init__(self,email, name):
        self.email = email
        self.name = name

# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_subscription_list():
    # fetch subscription list from slack
    subscription_list = []
    db = get_db() 
    docs  = db.collection('users').stream()
    for doc in docs:
        data = doc.to_dict()
        # TODO DANGER remove not here
        if "subscribed" not in data and "name" in data:
            subscription_list.append(
                address(data["email_address"],data["name"].split(" ")[0]).__dict__
            )
            logger.debug(data["email_address"])
    return {"active": subscription_list}


@limits(calls=100, period=ONE_MINUTE)
def add_to_subscription_list(user_id):
    user_doc = get_user_from_slack_id(user_id)
    db = get_db()

    update_subs = db.collection("users").document(user_doc.id).update(
        {
            "subscribe" : True
    })
    logger.debug(f"Update Result: {update_subs}")
    return user_doc.id

@limits(calls=100, period=ONE_MINUTE)
def remove_from_subscription_list(email):
    db = get_db()
    update_subs = db.collection("users").where("email_address","==", email).update(
        {
            "subscribe" : False
    })
    logger.debug(f"Update Result: {update_subs}")
    return email+"unsubscribed"

@limits(calls=100, period=ONE_MINUTE)
def check_subscription_list(user_id):
    user_doc = get_user_from_slack_id(user_id).to_dict()
    if "subscribe" in user_doc:
        if user_doc["subscribe"]:
            return True
    return False
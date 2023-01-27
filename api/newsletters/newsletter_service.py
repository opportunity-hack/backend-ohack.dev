import logging
from ratelimit import limits

from api.messages.messages_service import (get_db, get_user_from_slack_id, ONE_MINUTE)

logger = logging.getLogger("myapp")

class address:
    def __init__(self,email,id, name):
        self.email = email
        self.id = id
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
        # NOTE: the not is there because no user is subscribed yet
        # this function theoretically gets the list of all non subscribers
        # NOTE: "no name" just means the name hasn't been found. It's preferable to have emails sent to users with their names
        if "subscribed" not in data:
            subscription_list.append(
                address(data["email_address"],doc.id,"no name".split(" ")[0]).__dict__
            )
            logger.debug(doc.id)
    return {"active": subscription_list}


@limits(calls=100, period=ONE_MINUTE)
def get_full_list():
    # fetch subscription list from slack
    all_users = []
    db = get_db() 
    docs  = db.collection('users').stream()
    for doc in docs:
        data = doc.to_dict()
        all_users.append(
             # NOTE: "no name" just means the name hasn't been found. It's preferable to have emails sent to users with their names
            address(data["email_address"],doc.id,"no name".split(" ")[0]).__dict__
        )
    return {"all_users": all_users}

@limits(calls=100, period=ONE_MINUTE)
def add_to_subscription_list(user_id):
    db = get_db()
    update_subs = db.collection("users").where("user_id" == user_id).update(
        {
            "subscribe" : True
    })
    logger.debug(f"Update Result: {update_subs}")
    return user_id

@limits(calls=100, period=ONE_MINUTE)
def remove_from_subscription_list(user_id):
    db = get_db()
    update_subs = db.collection("users").where("user_id" == user_id).update(
        {
            "subscribe" : False
    })
    logger.debug(f"Update Result: {update_subs}")
    return user_id+"unsubscribed"

@limits(calls=100, period=ONE_MINUTE)
def check_subscription_list(user_id):
    user_doc = get_user_from_slack_id(user_id).to_dict()
    if "subscribe" in user_doc:
        if user_doc["subscribe"]:
            return True
    return False
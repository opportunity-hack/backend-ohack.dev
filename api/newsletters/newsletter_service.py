import logging
from ratelimit import limits

from api.messages.messages_service import (get_db, get_user_from_slack_id, ONE_MINUTE)

logger = logging.getLogger("myapp")

# When a user doesn't have a role or a name or email in the beackend, the above values are used in place when sending the email
NO_ROLE="no role"
NO_NAME="no name"
NO_EMAIL="noemail@mail.com"

# roles available for ohack
USER_ROLES = ["volunteer","hacker","mentor", "no role"]

class address:
    def __init__(self,email,id,name, role, subscribe):
        self.email = email
        self.id = id
        self.name = name
        self.role = role
        self.subscribe = subscribe

# Caching is not needed because the parent method already is caching
@limits(calls=100, period=ONE_MINUTE)
def get_subscription_list():
    # fetch subscription list from slack
    subscription_list = {}

    for i in USER_ROLES:
        subscription_list[i]=[]

    db = get_db() 
    docs  = db.collection('users').stream()
    for doc in docs:
        data = doc.to_dict() 
        user_name =  data["name"]   if "name" in data  else NO_NAME
        user_role = data["role"] if "role" in data   else NO_ROLE
        user_email= data["email_address"] if "email_address" in data else NO_EMAIL
        user_subscribe= data["subscribe"] if "subscribe" in data else False
        subscription_list[user_role].append(
            address(user_email,doc.id,user_name, user_role, user_subscribe).__dict__
        )
        logger.debug(doc.id)
    return {"active": subscription_list}


@limits(calls=100, period=ONE_MINUTE)
def add_to_subscription_list(user_id):
    db = get_db()
    db.collection("users").document(user_id).update(
        {
            "subscribe" : True
    })
    return {"subscribed": "true"}

@limits(calls=100, period=ONE_MINUTE)
def remove_from_subscription_list(user_id):
    db = get_db()
    db.collection("users").document(user_id).update(
        {
            "subscribe" : False
    })
    return {"subscribed": "false" }

@limits(calls=100, period=ONE_MINUTE)
def check_subscription_list(user_id):
    db = get_db()

    user_doc = db.collection("users").document(user_id).get().to_dict()
    if "subscribe" in user_doc:
        if user_doc["subscribe"]:
            return {"is_subscribed" : "true"}
    return {"subscribed" : "false"}
from ratelimit import limits
from model.user import User
from db.db import get_user, save_user, upsert_user
import logging

#TODO: service level logging?
logger = logging.getLogger("myapp")
logger.setLevel(logging.INFO)

#TODO consts file?
ONE_MINUTE = 1*60

@limits(calls=50, period=ONE_MINUTE)
def save_user(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
    
    res = None

    logger.info(f"User Save for {user_id} {email} {last_login} {profile_image} {name} {nickname}")
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc


    if user_id is None or email is None or last_login is None or profile_image is None:
        logger.error(
            f"Empty values provided for user_id: {user_id},\
                email: {email}, or last_login: {last_login}\
                    or profile_image: {profile_image}")
        return None

    # TODO: Call get_user from db here
    user = get_user(user_id)

    if user is not None:
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        res = upsert_user(user)
    else:
        user = User()
        user.user_id = user_id
        user.email_address = email
        user.last_login = last_login
        user.profile_image = profile_image
        user.name = name
        user.nickname = nickname
        res = save_user(user)

    return user.id if res is not None else None
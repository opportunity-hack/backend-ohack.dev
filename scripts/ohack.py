import argparse
import os
import sys
import logging
from dotenv import load_dotenv
from api.users import users_service
from common.utils import safe_get_env_var
from db.mem import flush
sys.path.append("../")
load_dotenv()
# add logger
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)

# TODO: Select db interface based on env
in_memory = safe_get_env_var("IN_MEMORY_DATABASE") == 'True'

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(title="Entities")

users_parser = subparsers.add_parser("users")
users_subparsers = users_parser.add_subparsers(title="Commands")

create_user_parser = users_subparsers.add_parser("create")

'''
Save User api method signature:
def save_user(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None):
'''
create_user_parser.add_argument("-u", "--user-id", action="store_const", required=True)
create_user_parser.add_argument("-n", "--name", action="store_const", required=True)
create_user_parser.add_argument("-e", "--email", action="store_const", required=True)
create_user_parser.add_argument("--nickname", action="store_const", required=False, default=None)
# Skipping last login as it really doesn't make any sense here



def create_user(user_id, email, name, nickname=None, profile_image=None):

    if in_memory:
        flush()

args = parser.parse_args()

#TODO: Figure out what command has been called and parse args accordingly
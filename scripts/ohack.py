import argparse
import os
import sys
sys.path.append("../") #TODO: Handle passing scripts/ohack.py to the interpreter. IOW, don't require python to be run from this directory.
import logging
from dotenv import load_dotenv
from services import users_service
from common.utils import safe_get_env_var
from db.mem import flush
from datetime import datetime

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
subparsers = parser.add_subparsers(title="Entities", dest='command')

users_parser = subparsers.add_parser("users")
users_subparsers = users_parser.add_subparsers(title="Commands", dest='users_command')

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
create_user_parser.add_argument("-u", "--user-id", required=True)
create_user_parser.add_argument("-n", "--name", required=False, default=None) # Not required according to users service
create_user_parser.add_argument("-e", "--email", required=True)
create_user_parser.add_argument("--nickname", required=False, default=None)
create_user_parser.add_argument("-p", "--profile-image", required=True) # Required according to users service
# Skipping last login as it really doesn't make any sense here

delete_user_parser = users_subparsers.add_parser("delete")



def create_user(user_id, email, name, nickname=None, profile_image=None):

    users_service.save_user(
        user_id=user_id, 
        email=email, 
        name=name, 
        nickname=nickname, 
        profile_image=profile_image,
        last_login=str(datetime.now()))

    if in_memory:
        flush()

args = parser.parse_args()

print(args)

if hasattr(args, 'command'):
    print(f'Command: {args.command}')

    if args.command == 'users':
        # Handle user related commands
        if hasattr(args, 'users_command'):
            print(f'Users command: {args.users_command}')

            if args.users_command == 'create':
                print('Creating a user')
                create_user(args.user_id, args.email, args.name, args.nickname, args.profile_image if 'profile_image' in args else None)       



#TODO: Figure out what command has been called and parse args accordingly
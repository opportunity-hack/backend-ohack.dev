import argparse
import os
import sys
import json
import logging

sys.path.append("../") #TODO: Handle passing scripts/ohack.py to the interpreter. IOW, don't require python to be run from this directory.

from dotenv import load_dotenv
from services import users_service
from model.user import User
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

user_id_parser = argparse.ArgumentParser(add_help=False)
user_id_parser.add_argument("-u", "--user-id", required=False, default=None)
user_id_parser.add_argument("-i", "--id", required=False, default=None)

user_attributes_parser = argparse.ArgumentParser(add_help=False)
user_attributes_parser.add_argument("-n", "--name", required=False, default=None) # Not required according to users service
user_attributes_parser.add_argument("-e", "--email", required=False, default=None)
user_attributes_parser.add_argument("--nickname", required=False, default=None)
user_attributes_parser.add_argument("-p", "--profile-image", required=False, default=None) # Required according to users service
user_attributes_parser.add_argument("-w", "--why", required=False, default=None)

create_user_parser = users_subparsers.add_parser("create", parents=[user_id_parser, user_attributes_parser])

get_user_parser = users_subparsers.add_parser("get", parents=[user_id_parser])

delete_user_parser = users_subparsers.add_parser("delete", parents=[user_id_parser])

update_user_parser = users_subparsers.add_parser("update", parents=[user_id_parser, user_attributes_parser])

def get_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.get_user_from_slack_id(user_id)
    elif id is not None:
        u = users_service.get_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    
    print(f'User: \n {json.dumps(vars(u))}')

    if in_memory:
        flush()

def delete_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.remove_user_by_slack_id(user_id)
    elif id is not None:
        u = users_service.remove_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    
    print(f'Deleted: \n {json.dumps(vars(u))}')

    if in_memory:
        flush()

def update_user(user_id, id, profile_image, name, nickname):
    if user_id is  None and id is None:
        raise ValueError("Either user id or db id must be provided")
    
    u = users_service.upsert_user(
        id,
        user_id,
        str(datetime.now()), #last_login
        profile_image,
        name,
        nickname)

    print(f'Updated: \n {json.dumps(vars(u))}')

    if in_memory:
        flush()
    

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

            if args.users_command == 'get':
                get_user(args.user_id, args.id)

            if args.users_command == 'create':
                print('Creating a user')
                create_user(args.user_id, args.email, args.name, args.nickname, args.profile_image if 'profile_image' in args else None)       

            if args.users_command == 'delete':
                delete_user(args.user_id, args.id)

            if args.users_command == 'update':
                update_user(args.user_id, args.id, args.profile_image, args.name, args.nickname)

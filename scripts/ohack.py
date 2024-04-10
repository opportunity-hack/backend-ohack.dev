# USAGE: $ python ohack.py -h
# > usage: ohack.py [-h] {users,problemstatements} ...
# >
# > optional arguments:
# > -h, --help            show this help message and exit
# >
# > Entities:
# > {users,problemstatements}
#
# ENTITY HELP: $ python ohack.py users -h
# > usage: ohack.py users [-h] {create,get,delete,update} ...
# >
# > optional arguments:
# > -h, --help            show this help message and exit
# >
# > Commands:
# >  {create,get,delete,update}
# COMMAND HELP: $ python ohack.py users get -h
# > usage: ohack.py users get [-h] [-u USER_ID] [-i ID]
# >
# > optional arguments:
# >  -h, --help            show this help message and exit
# >  -u USER_ID, --user-id USER_ID
# >  -i ID, --id ID

import argparse
import os
import sys
import json
import logging

sys.path.append("../") #TODO: Handle passing scripts/ohack.py to the interpreter. IOW, don't require python to be run from this directory.

from model.problem_statement import ProblemStatement
from dotenv import load_dotenv
from services import users_service, problem_statements_service
from model.user import User
from common.utils import safe_get_env_var
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

create_user_parser = users_subparsers.add_parser("create", parents=[user_id_parser, user_attributes_parser])

get_user_parser = users_subparsers.add_parser("get", parents=[user_id_parser])

delete_user_parser = users_subparsers.add_parser("delete", parents=[user_id_parser])

update_user_parser = users_subparsers.add_parser("update", parents=[user_id_parser, user_attributes_parser])

problem_statements_parser = subparsers.add_parser("problemstatements")
problem_statements_subparsers = problem_statements_parser.add_subparsers(title="Commands", dest='problem_statements_command')

problem_statement_id_parser = argparse.ArgumentParser(add_help=False)
problem_statement_id_parser.add_argument("-s", "--problem-statement-id", required=False, default=None)

get_problem_statement_parser = problem_statements_subparsers.add_parser("get", parents=[problem_statement_id_parser])

problem_statement_attributes_parser = argparse.ArgumentParser(add_help=False)
problem_statement_attributes_parser.add_argument("-d", "--description", required=False, default=None)
problem_statement_attributes_parser.add_argument("-f", "--first-thought-of", required=False, default=None)
problem_statement_attributes_parser.add_argument("-g", "--github", required=False, default=None)
problem_statement_attributes_parser.add_argument("--status", required=False, default=None)


create_problem_statement_parser = problem_statements_subparsers.add_parser("create", parents=[problem_statement_attributes_parser])
create_problem_statement_parser.add_argument("-t", "--title", required=True)

update_problem_statement_parser = problem_statements_subparsers.add_parser("update", parents=[problem_statement_id_parser ,problem_statement_attributes_parser])

delete_problem_statement_parser = problem_statements_subparsers.add_parser("delete", parents=[problem_statement_id_parser])


def get_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.get_user_from_slack_id(user_id)
    elif id is not None:
        u = users_service.get_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    
    print(f'User: \n {json.dumps(vars(u), indent=4)}')

def delete_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.remove_user_by_slack_id(user_id)
    elif id is not None:
        u = users_service.remove_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    
    print(f'Deleted: \n {json.dumps(vars(u), indent=4)}')

def update_user(user_id, id, profile_image, name, nickname):
    if user_id is  None and id is None:
        raise ValueError("Either user id or db id must be provided")
    
    u = users_service.update_user_fields(
        id,
        user_id,
        str(datetime.now()), #last_login
        profile_image,
        name,
        nickname)

    print(f'Updated: \n {json.dumps(vars(u), indent=4)}')
    

def create_user(user_id, email, name, nickname=None, profile_image=None):

    u = users_service.save_user(
        user_id=user_id, 
        email=email, 
        name=name, 
        nickname=nickname, 
        profile_image=profile_image,
        last_login=str(datetime.now()))
    
    print(f'Created: \n {json.dumps(vars(u), indent=4)}')


def get_problem_statement(id):
    s = problem_statements_service.get_problem_statement(id)
    print(f'User: \n {json.dumps(vars(s), indent=4)}')

def create_problem_statement(title, description=None, first_thought_of=None, github=None, status=None):
    p = problem_statements_service.save_problem_statement({
        'title' : title,
        'description' : description,
        'first_thought_of' : first_thought_of,
        'github' : first_thought_of,
        'status' : status
    })

    print(f'Created: \n {json.dumps(vars(p), indent=4)}')

def update_problem_statement(id, description=None, first_thought_of=None, github=None, status=None):
    p = problem_statements_service.update_problem_statement_fields({
        'id': id,
        'description' : description,
        'first_thought_of' : first_thought_of,
        'github' : first_thought_of,
        'status' : status
    })

    print(f'Updated: \n {json.dumps(vars(p), indent=4)}')
    
def delete_problem_statement(id):
    p: ProblemStatement | None = None
    if id is not None:
        p = problem_statements_service.remove_problem_statement(id)
    else:
        raise ValueError("DB id must be provided")
    
    print(f'Deleted: \n {json.dumps(vars(p), indent=4)}')

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

            elif args.users_command == 'create':
                print('Creating a user')
                create_user(args.user_id, args.email, args.name, args.nickname, args.profile_image if 'profile_image' in args else None)       

            elif args.users_command == 'delete':
                delete_user(args.user_id, args.id)

            elif args.users_command == 'update':
                update_user(args.user_id, args.id, args.profile_image, args.name, args.nickname)

    elif args.command == 'problemstatements':
        # Handle problem statement related commands
        if hasattr(args, 'problem_statements_command'):
            
            if args.problem_statements_command == 'get':
                get_problem_statement(args.problem_statement_id)

            if args.problem_statements_command == 'create':
                create_problem_statement(args.title, args.description, args.first_thought_of, args.github, args.status)

            if args.problem_statements_command == 'update':
                update_problem_statement(args.problem_statement_id, args.description, args.first_thought_of, args.github, args.status)

            if args.problem_statements_command == 'delete':
                delete_problem_statement(args.problem_statement_id)

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
from dotenv import load_dotenv
from datetime import datetime

#------------------------------ Load .env first -------------------------------- #


load_dotenv()

#------------------------------ Check args for log level -------------------------------- #

print(f"args: {sys.argv}")

if '--log-debug' in sys.argv:
    os.environ['GLOBAL_LOG_LEVEL'] = 'debug'
    print(f'set global log leveL to DEBUG')

sys.path.append("../") #TODO: Handle passing scripts/ohack.py to the interpreter. IOW, don't require python to be run from this directory.

#------------------------------ Now import log level function --------------------------- #
from common.log import get_log_level

# ----------------------------- Handle logging configuration ---------------------------- #
# add logger
logger = logging.getLogger("ohack")
logger.setLevel(get_log_level())

# Add stdout handler, with level INFO
console = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(name)-13s: %(levelname)-8s %(message)s')
console.setFormatter(formatter)

# set root logger to standard out
logging.getLogger().addHandler(console)

#------------------------------ Now import OHack modules ---------------------------------#

from model.problem_statement import ProblemStatement

from services import users_service, problem_statements_service, nonprofits_service
from model.user import User
from common.utils import safe_get_env_var
from model.hackathon import Hackathon

# TODO: Temporary stop gap. Should not be accessing db layer directly from here
from db.db import fetch_hackathon, fetch_hackathons, insert_hackathon

# TODO: Select db interface based on env
in_memory = safe_get_env_var("IN_MEMORY_DATABASE") == 'True'

parser = argparse.ArgumentParser()

json_parser = argparse.ArgumentParser(add_help=False)
json_parser.add_argument("-j", "--json", required=False, default=None)

log_level_parser = argparse.ArgumentParser(add_help=False)
log_level_parser.add_argument("--log-debug", action='store_true')

all_parser = argparse.ArgumentParser(add_help=False)
all_parser.add_argument("--all", action='store_true')

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

create_user_parser = users_subparsers.add_parser("create", parents=[user_id_parser, user_attributes_parser, log_level_parser])

get_user_parser = users_subparsers.add_parser("get", parents=[user_id_parser, all_parser, log_level_parser])

delete_user_parser = users_subparsers.add_parser("delete", parents=[user_id_parser, log_level_parser])

update_user_parser = users_subparsers.add_parser("update", parents=[user_id_parser, user_attributes_parser, log_level_parser])

problem_statements_parser = subparsers.add_parser("problemstatements")
problem_statements_subparsers = problem_statements_parser.add_subparsers(title="Commands", dest='problem_statements_command')

problem_statement_id_parser = argparse.ArgumentParser(add_help=False)
problem_statement_id_parser.add_argument("-s", "--problem-statement-id", required=False, default=None)

get_problem_statement_parser = problem_statements_subparsers.add_parser("get", parents=[problem_statement_id_parser, all_parser, log_level_parser])

problem_statement_attributes_parser = argparse.ArgumentParser(add_help=False)
problem_statement_attributes_parser.add_argument("-d", "--description", required=False, default=None)
problem_statement_attributes_parser.add_argument("-f", "--first-thought-of", required=False, default=None)
problem_statement_attributes_parser.add_argument("-g", "--github", required=False, default=None)
problem_statement_attributes_parser.add_argument("--status", required=False, default=None)

create_problem_statement_parser = problem_statements_subparsers.add_parser("create", parents=[problem_statement_attributes_parser, log_level_parser])
create_problem_statement_parser.add_argument("-t", "--title", required=True)

update_problem_statement_parser = problem_statements_subparsers.add_parser("update", parents=[problem_statement_id_parser ,problem_statement_attributes_parser, log_level_parser])

delete_problem_statement_parser = problem_statements_subparsers.add_parser("delete", parents=[problem_statement_id_parser, log_level_parser])

helping_parser = problem_statements_subparsers.add_parser("helpout", parents=[problem_statement_id_parser, user_id_parser, log_level_parser])
helping_parser.add_argument("--cannot-help", action='store_true')
helping_parser.add_argument("--mentor", action='store_true')
helping_parser.add_argument("--hacker", action='store_true')

problem_statement_link_parser = problem_statements_subparsers.add_parser("link")
problem_statement_link_parser_subparsers  = problem_statement_link_parser.add_subparsers(title="Links", dest='problem_statement_link_command')

hackathon_id_parser = argparse.ArgumentParser(add_help=False)
hackathon_id_parser.add_argument("--hackathon-id", required=False, default=None)

link_hackathon_parser = problem_statement_link_parser_subparsers.add_parser("hackathon", parents=[problem_statement_id_parser, hackathon_id_parser, log_level_parser])

hackathons_parser = subparsers.add_parser("hackathons")
hackathons_subparsers = hackathons_parser.add_subparsers(title="Commands", dest='hackathons_command')

get_hackathon_parser = hackathons_subparsers.add_parser("get", parents=[hackathon_id_parser, all_parser, log_level_parser])

import_hackathon_parser = hackathons_subparsers.add_parser("import", parents=[json_parser, log_level_parser])

nonprofit_id_parser = argparse.ArgumentParser(add_help=False)
nonprofit_id_parser.add_argument("--nonprofit-id", required=False, default=None)

nonprofit_attributes_parser = argparse.ArgumentParser(add_help=False)
nonprofit_attributes_parser.add_argument("--npo-name", required=True) 
nonprofit_attributes_parser.add_argument("--npo-slack-channel", required=False, default=None) 
nonprofit_attributes_parser.add_argument("--npo-website", required=False, default=None) 
nonprofit_attributes_parser.add_argument("--npo-description", required=False, default=None) 
nonprofit_attributes_parser.add_argument("--need", required=False, default=None) 


nonprofits_parser = subparsers.add_parser("nonprofits")
nonprofits_subparsers = nonprofits_parser.add_subparsers(title="Commands", dest='nonprofits_command')

get_nonprofit_parser = nonprofits_subparsers.add_parser("get", parents=[nonprofit_id_parser, all_parser, log_level_parser])

create_nonprofit_parser = nonprofits_subparsers.add_parser("create", parents=[nonprofit_attributes_parser, log_level_parser])



def do_get_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.get_user_from_slack_id(user_id)
    elif id is not None:
        u = users_service.get_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    return u

def get_user(user_id, id):
    u = do_get_user(user_id, id)
    
    logger.info(f'User: \n {json.dumps(vars(u), indent=4)}')

def delete_user(user_id, id):
    u:User | None = None
    if user_id is not None:
        u = users_service.remove_user_by_slack_id(user_id)
    elif id is not None:
        u = users_service.remove_user_by_db_id(id)
    else:
        raise ValueError("Either user id or db id must be provided")
    
    logger.info(f'Deleted: \n {json.dumps(vars(u), indent=4)}')

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

    logger.info(f'Updated: \n {json.dumps(vars(u), indent=4)}')
    

def create_user(user_id, email, name, nickname=None, profile_image=None):

    u = users_service.save_user(
        user_id=user_id, 
        email=email, 
        name=name, 
        nickname=nickname, 
        profile_image=profile_image,
        last_login=str(datetime.now()))
    
    logger.info(f'Created: \n {json.dumps(vars(u), indent=4)}')

def get_users():
    res = users_service.get_users()
    output = []
    for p in res:
        output.append(p.serialize())
    logger.info(f'Users: \n {json.dumps(output, indent=4)}')
    

def get_problem_statement(id):
    logger.info(f'Finding problem statement my id {id}')
    s = problem_statements_service.get_problem_statement(id)
    logger.info(f'Problem statement: \n {json.dumps(vars(s), indent=4)}')

def get_problem_statements():
    res = problem_statements_service.get_problem_statements()
    output = []
    for p in res:
        output.append(p.serialize())
    logger.info(f'Problem Statements: \n {json.dumps(output, indent=4)}')
    

def create_problem_statement(title, description=None, first_thought_of=None, github=None, status=None):
    p = problem_statements_service.save_problem_statement({
        'title' : title,
        'description' : description,
        'first_thought_of' : first_thought_of,
        'github' : first_thought_of,
        'status' : status
    })

    logger.info(f'Created: \n {json.dumps(vars(p), indent=4)}')

def update_problem_statement(id, description=None, first_thought_of=None, github=None, status=None):
    p = problem_statements_service.update_problem_statement_fields({
        'id': id,
        'description' : description,
        'first_thought_of' : first_thought_of,
        'github' : first_thought_of,
        'status' : status
    })

    logger.info(f'Updated: \n {json.dumps(vars(p), indent=4)}')
    
def delete_problem_statement(id):
    p: ProblemStatement | None = None
    if id is not None:
        p = problem_statements_service.remove_problem_statement(id)
    else:
        raise ValueError("DB id must be provided")
    
    logger.info(f'Deleted: \n {json.dumps(vars(p), indent=4)}')

def handle_helping(problem_statement_id, id, user_id, mentor, hacker, cannot_help):
    u = do_get_user(user_id, id)

    p = problem_statements_service.save_user_helping_status(u, {
        "status": "not_helping" if cannot_help else "helping",
        "type": "mentor" if mentor else "hacker",
        "problem_statement_id": problem_statement_id  
    })

    d = p.serialize()

    logger.info(f'Problem statement: \n {json.dumps(d, indent=4)}')

def get_hackathons():
    # TODO: This is a temporary stop-gap. We should eventually call a method on the
    # hackathons service as opposed to calling the DB layer directly
    res = fetch_hackathons()
    output = []
    for h in res:
        output.append(h.serialize())
    logger.info(f'Hackathons: \n {json.dumps(output, indent=4)}')

def get_hackathon(id):
    res = fetch_hackathon(id)
    temp = res.serialize()
    logger.info(f'Hackathon: \n {json.dumps(temp, indent=4)}')

def import_hackathons(json_file):
    # TODO: Handle relative paths
    f = open(json_file, 'r')
    l = json.load(f)
    for temp in l:
        h = Hackathon.deserialize(temp)
        insert_hackathon(h)

def link_hackathon_to_problem_statement(problem_statement_id, hackathon_id):
    # JSON should be in the format of
    # {
    #   "mapping": {
    #     "<problemStatementId>" : [ "<eventTitle1>|<eventId1>", "<eventId2>" ]
    #   }
    # }

    events = [hackathon_id]

    problem_statement = problem_statements_service.get_problem_statement(problem_statement_id)

    for h in problem_statement.hackathons:
        if h.id not in events:
            events.append(h.id)

    payload = {
        'mapping': {
            problem_statement_id: events
        }
    }

    print(f"payload: {payload}")

    result = problem_statements_service.link_problem_statements_to_events(payload)

    problem_statement = result[0]

    d = problem_statement.serialize()

    logger.info(f'Problem statement: \n {json.dumps(d, indent=4)}')

def get_nonprofits():
    res = nonprofits_service.get_npos()
    output = []
    for n in res:
        output.append(n.serialize())
    logger.info(f'Nonprofits: \n {json.dumps(output, indent=4)}')
    
def get_nonprofit(id):
    res = nonprofits_service.get_npo(id)
    temp = res.serialize()
    logger.info(f'Hackathon: \n {json.dumps(temp, indent=4)}')

def create_nonprofit(name, description, website, slack_channel, need):
    p = nonprofits_service.save_npo({
        'name' : name,
        'description' : description,
        'website' : website,
        'slack_channel' : slack_channel,
        'need' : need
    })

    logger.info(f'Created: \n {json.dumps(vars(p), indent=4)}')

args = parser.parse_args()

if args.log_debug:
    logger.info("Debug logging") 

logger.info(args)

if hasattr(args, 'command'):
    logger.info(f'Command: {args.command}')

    if args.command == 'users':
        # Handle user related commands
        if hasattr(args, 'users_command'):
            logger.info(f'Users command: {args.users_command}')

            if args.users_command == 'get':
                if args.all:
                    get_users()
                else:
                    get_user(args.user_id, args.id)

            elif args.users_command == 'create':
                logger.info('Creating a user')
                create_user(args.user_id, args.email, args.name, args.nickname, args.profile_image if 'profile_image' in args else None)       

            elif args.users_command == 'delete':
                delete_user(args.user_id, args.id)

            elif args.users_command == 'update':
                update_user(args.user_id, args.id, args.profile_image, args.name, args.nickname)

    elif args.command == 'problemstatements':
        # Handle problem statement related commands
        if hasattr(args, 'problem_statements_command'):
            
            if args.problem_statements_command == 'get':
                if args.all:
                    get_problem_statements()
                else: 
                    get_problem_statement(args.problem_statement_id)

            if args.problem_statements_command == 'create':
                create_problem_statement(args.title, args.description, args.first_thought_of, args.github, args.status)

            if args.problem_statements_command == 'update':
                update_problem_statement(args.problem_statement_id, args.description, args.first_thought_of, args.github, args.status)

            if args.problem_statements_command == 'delete':
                delete_problem_statement(args.problem_statement_id)

            if args.problem_statements_command == 'helpout':
                handle_helping(args.problem_statement_id, args.id, args.user_id, args.mentor, args.hacker, args.cannot_help)
    
            if args.problem_statements_command == 'link':

                if hasattr(args, 'problem_statement_link_command'):

                    if args.problem_statement_link_command == 'hackathon':

                        link_hackathon_to_problem_statement(args.problem_statement_id, args.hackathon_id)

    elif args.command == 'hackathons':
         # Handle hackathon related commands
        if hasattr(args, 'hackathons_command'):

            if args.hackathons_command == 'get':

                if args.all:
                    get_hackathons()

                else:
                    get_hackathon(args.hackathon_id)

            elif args.hackathons_command == 'import':
                import_hackathons(args.json)

    if args.command == 'nonprofits':
        # Handle user related commands
        if hasattr(args, 'nonprofits_command'):
            logger.info(f'Nonprofits command: {args.nonprofits_command}')

            if args.nonprofits_command == 'get':
                if args.all:
                    get_nonprofits()
                else:
                    get_nonprofit(args.nonprofit_id)
                    
            elif args.nonprofits_command == 'create':
                create_nonprofit(
                    args.npo_name, 
                    args.npo_description, 
                    args.npo_website, 
                    args.npo_slack_channel,
                    args.need)
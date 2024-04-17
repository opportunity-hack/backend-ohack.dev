from common.utils import safe_get_env_var
from model.hackathon import Hackathon
from model.problem_statement import ProblemStatement
from model.user import User

from db.interface import DatabaseInterface

db:DatabaseInterface = None

# TODO: Select db interface based on env
in_memory = safe_get_env_var("IN_MEMORY_DATABASE") == 'True'

if in_memory: 
    from db.mem import InMemoryDatabaseInterface
    db = InMemoryDatabaseInterface()
else:
    from db.firestore import FirestoreDatabaseInterface
    db = FirestoreDatabaseInterface()

#Users

def fetch_user_by_user_id(user_id):
    u = db.fetch_user_by_user_id(user_id)
    return u

def fetch_user_by_db_id(id):
    u = db.fetch_user_by_db_id(id)
    return u

def upsert_profile_metadata(user: User):
    return db.upsert_profile_metadata(user)

def update_user(user:User):
    return db.update_user(user)

def insert_user(user:User):
    print('Inserting user')
    return db.insert_user(user)

def get_user_profile_by_db_id(id):
    return db.get_user_profile_by_db_id(id)

def delete_user_by_user_id(user_id):
    return db.delete_user_by_user_id(user_id)

def delete_user_by_db_id(id):
    return db.delete_user_by_db_id(id)

def fetch_users():
    return db.fetch_users()

# Problem Statements
def fetch_problem_statement(id):
    return db.fetch_problem_statement(id)

def fetch_problem_statements():
    return db.fetch_problem_statements()

def insert_problem_statement(problem_statement: ProblemStatement):
    return db.insert_problem_statement(problem_statement)

def update_problem_statement(problem_statement: ProblemStatement):
    return db.update_problem_statement(problem_statement)

def delete_problem_statement(id):
    return db.delete_problem_statement(id)

def insert_helping(problem_statement_id, user: User, mentor_or_hacker, helping_date):
    return db.insert_helping(problem_statement_id, user, mentor_or_hacker, helping_date)

def delete_helping(problem_statement_id, user: User):
    return db.delete_helping(problem_statement_id, user)

# Hackathons

def fetch_hackathons():
    return db.fetch_hackathons()

def insert_hackathon(h : Hackathon):
    return db.insert_hackathon(h)

#TODO: Kill with fire. Leaky abstraction
def get_user_doc_reference(user_id):
    return db.get_user_doc_reference(user_id)
from common.utils import safe_get_env_var
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

# Problem Statements
def fetch_problem_statement_by_id(id):
    return db.fetch_problem_statement_by_id(id)

def insert_problem_statement(problem_statement: ProblemStatement):
    return db.insert_problem_statement(problem_statement)



#TODO: Kill with fire. Leaky abstraction
def get_user_doc_reference(user_id):
    return db.get_user_doc_reference(user_id)
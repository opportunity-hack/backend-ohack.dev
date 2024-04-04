from common.utils import safe_get_env_var
from model.user import User
from firestore import FirestoreDatabaseInterface
from mem import InMemoryDatabaseInterface
from interface import DatabaseInterface

db:DatabaseInterface = None

# TODO: Select db interface based on env
in_memory = safe_get_env_var("FIREBASE_CERT_CONFIG") == 'True'

if in_memory:
    db = InMemoryDatabaseInterface()
else:
    db = FirestoreDatabaseInterface()

def get_user(user_id):
    u = db.get_user(user_id)
    return u

def get_user_by_doc_id(id):
    u = db.get_user_by_doc_id(id)
    return u

def upsert_user(user:User):
    return db.upsert_user(user)

def save_user(user:User):
    return db.save_user(user)

#TODO: Kill with fire. Leaky abstraction
def get_user_doc_reference(user_id):
    return db.get_user_doc_reference(user_id)
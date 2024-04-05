import firebase_admin
from firebase_admin import credentials, firestore
from common.utils import safe_get_env_var
from mockfirestore import MockFirestore
import json
from model.user import User
from db.interface import DatabaseInterface
import logging

mockfirestore = None

#TODO: Put in .env? Feels configurable. Or maybe something we would want to protect with a secret?
SLACK_PREFIX = "oauth2|slack|T1Q7936BH-"

if safe_get_env_var("ENVIRONMENT") == "test":
    mockfirestore = MockFirestore() #Only used when testing
else: 
    cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))
    cred = credentials.Certificate(cert_env)
    # see if firebase_admin is already been initialized
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credential=cred)

# add logger
logger = logging.getLogger(__name__)
# set log level
logger.setLevel(logging.INFO)

class FirestoreDatabaseInterface(DatabaseInterface):
    def get_db(self):
        if safe_get_env_var("ENVIRONMENT") == "test":
            return mockfirestore
        
        return firestore.client()
    
    def get_default_badge(self):
        db = self.get_db()
        default_badge = db.collection('badges').document("fU7c3ne90Rd1TB5P7NTV")
        return default_badge

    def get_user(self, user_id):
        u = None
        db = self.get_db()
        temp = self.get_user_raw(db, user_id)
        if temp is not None:
            u = User.deserialize(temp)
        return u

    def get_user_raw(self, db, user_id):
        u = None
        try:
            u, *rest = db.collection('users').where("user_id","==",user_id).stream()
        finally:
            pass
        return u
    
    def get_user_raw_by_id(self, db, id):
        u = db.collection('users').document(id).get()
        return u

    def save_user(self, doc_id, email, last_login, user_id, profile_image, name, nickname):
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #TODO: Does this throw?
        insert_res = db.collection('users').document(doc_id).set({
            "email_address": email,
            "last_login": last_login,
            "user_id": user_id,
            "profile_image": profile_image,
            "name": name,
            "nickname": nickname,
            "badges": [
                default_badge
            ],
            "teams": []
        })
        return doc_id if insert_res is not None else None
    
    def upsert_user(self, user_id, last_login,  profile_image, name, nickname):

        update_res = None

        db = self.get_db()

        doc = self.get_user_raw(db, user_id)

        if doc is not None:

            update_res = db.collection("users").document(doc.id).update(
                {
                    "last_login": last_login,
                    "profile_image": profile_image,
                    "name": name,
                    "nickname": nickname
                })
            
        return user_id if update_res is not None else None
    
    def get_user_from_slack_id(self, user_id):
        
        db = self.get_db()  # this connects to our Firestore database
        slack_user_id = f"{SLACK_PREFIX}{user_id}"
        docs = db.collection('users').where("user_id", "==", slack_user_id).stream()
        user = None
        temp = None
        if docs is not None:
            try: 
                temp, *rest = docs
            finally:
                pass

        if temp is not None:
            user = User.deserialize(temp)

        return user

    def get_user_by_doc_id(self, id):
        db = self.get_db()  # this connects to our Firestore database
        return self.get_user_raw_by_id(db, id)

    def get_user_doc_reference(self, user_id):
        db = self.get_db()
        u = self.get_user_raw(db, user_id)
        return u.reference if u is not None else None
    
DatabaseInterface.register(FirestoreDatabaseInterface)
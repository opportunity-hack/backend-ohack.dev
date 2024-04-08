import firebase_admin
from firebase_admin import credentials, firestore
from common.utils import safe_get_env_var
from mockfirestore import MockFirestore
import json
from model.user import User
from model.hackathon import Hackathon
from db.interface import DatabaseInterface
import logging

mockfirestore = None

#TODO: Put in .env? Feels configurable. Or maybe something we would want to protect with a secret?
SLACK_PREFIX = "oauth2|slack|T1Q7936BH-"

# TODO: Select db interface based on env
in_memory = safe_get_env_var("IN_MEMORY_DATABASE") == 'True'

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

    #TODO: Delete. Same as get_user_from_slack_id
    def get_user(self, user_id):
        u = None
        db = self.get_db()
        temp = self.get_user_raw(db, user_id)
        if temp is not None:
            u = User.deserialize(temp.to_dict())
        return u
    
    def get_user_from_slack_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        user = None
        temp = self.get_user_raw(db, user_id)
        if temp is not None:
            user = User.deserialize(temp.to_dict())
        return user

    def get_user_raw(self, db, user_id):
        slack_user_id = f"{SLACK_PREFIX}{user_id}"
        u = None
        try:
            u, *rest = db.collection('users').where("user_id", "==", slack_user_id).stream()
        finally:
            pass
        return u
    
    def get_user_raw_by_id(self, db, id):
        u = db.collection('users').document(id).get()
        return u

    def insert_user(self, user:User):
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #TODO: Does this throw?
        insert_res = db.collection('users').document(user.id).set({
            "email_address": user.email_address,
            "last_login": user.last_login,
            "user_id": user.user_id,
            "profile_image": user.profile_image,
            "name": user.name,
            "nickname": user.nickname,
            "badges": [
                default_badge
            ],
            "teams": []
        })
        return user.id if insert_res is not None else None
    
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

    def get_user_by_doc_id(self, id):
        db = self.get_db()  # this connects to our Firestore database
        return self.get_user_raw_by_id(db, id)

    def get_user_doc_reference(self, user_id):
        db = self.get_db()
        u = self.get_user_raw(db, user_id)
        return u.reference if u is not None else None
    
    def get_user_profile_by_db_id(self, db_id):
        db = self.get_db()  # this connects to our Firestore database
        temp = self.get_user_raw_by_id(db, db_id)

        user = None

        if temp is not None:

            res = temp.to_dict()
            user = User.deserialize(res)

            if "hackathons" in res:
                #TODO: I think we use get_all here
                # https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.client.Client#google_cloud_firestore_v1_client_Client_get_all
                for h in res["hackathons"]:
                    rec = h.get().to_dict()

                    hackathon = Hackathon.deserialize(rec)
                    user.hackathons.append(hackathon)

                    #TODO: I think we use get_all here
                    # https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.client.Client#google_cloud_firestore_v1_client_Client_get_all
                    for n in rec["nonprofits"]:
                        
                        npo_doc = n.get() #TODO: Deal with lazy-loading in db layer
                        npo_id = npo_doc.id
                        npo = n.get().to_dict()
                        npo["id"] = npo_id
                                        
                        if npo and "problem_statements" in npo:
                            # This is duplicate date as we should already have this
                            del npo["problem_statements"]
                        hackathon.nonprofits.append(npo)

                    user.hackathons.append(hackathon)

            #TODO:
            # if "badges" in res:
            #     for h in res["badges"]:
            #         _badges.append(h.get().to_dict())

            

        return user

    def upsert_profile_metadata(self, user:User):
    
        db = self.get_db()  # this connects to our Firestore database
        data = user.serialize_profile_metadata()
        update_res = db.collection("users").document(user.id).set( data, merge=True)        
        logger.info(f"Update Result: {update_res}")
                
        return
    
DatabaseInterface.register(FirestoreDatabaseInterface)
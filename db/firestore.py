import firebase_admin
from firebase_admin import credentials, firestore
from common.utils import safe_get_env_var
from mockfirestore import MockFirestore
import json
from model.problem_statement import ProblemStatement
from model.user import User
from model.hackathon import Hackathon
from db.interface import DatabaseInterface
import logging
import uuid

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

    # ----------------------- Users --------------------------------------------

    def fetch_user_by_user_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        user = None
        temp = self.fetch_user_by_user_id_raw(db, user_id)
        if temp is not None:
            ref = temp.reference
            d = temp.to_dict()
            d['id'] = ref.id #Get the document id from the reference
            user = User.deserialize(d)
        return user

    def fetch_user_by_user_id_raw(self, db, user_id):
        #TODO: Why are we putting the slack prefix in the DB?
        if user_id.startswith(SLACK_PREFIX):
            slack_user_id = user_id
        else:
            slack_user_id = f"{SLACK_PREFIX}{user_id}"

        u = None
        try:
            u, *rest = db.collection('users').where("user_id", "==", slack_user_id).stream()
        except ValueError:
            pass
        return u
    
    def fetch_user_by_db_id_raw(self, db, id):
        u = db.collection('users').document(id).get()
        return u

    def insert_user(self, user:User):
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #Set user id
        user.id = uuid.uuid1().hex
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
    
    def update_user(self, user: User):

        update_res = None

        db = self.get_db()

        doc = self.fetch_user_by_user_id_raw(db, user.user_id)

        if doc is not None:

            update_res = db.collection("users").document(doc.id).update(
                {
                    "last_login": user.last_login,
                    "profile_image": user.profile_image,
                    "name": user.name,
                    "nickname": user.nickname
                })
            
        return user if update_res is not None else None

    def fetch_user_by_db_id(self, id):
        db = self.get_db()  # this connects to our Firestore database
        return self.fetch_user_by_db_id_raw(db, id)

    def get_user_doc_reference(self, user_id):
        db = self.get_db()
        u = self.fetch_user_by_user_id_raw (db, user_id)
        return u.reference if u is not None else None
    
    def get_user_profile_by_db_id(self, db_id):
        db = self.get_db()  # this connects to our Firestore database
        temp = self.fetch_user_by_db_id_raw(db, db_id)

        user = None

        if temp is not None:

            d = temp.to_dict()
            
            if d is not None:
                d['id'] = temp.id
                user = User.deserialize(d)

                if "hackathons" in d:
                    #TODO: I think we use get_all here
                    # https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.client.Client#google_cloud_firestore_v1_client_Client_get_all
                    for h in d["hackathons"]:
                        h_doc = h.get()
                        rec = h_doc.to_dict()
                        rec['id'] = h_doc.id

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
    

    def finish_deleting_user(self, db, user, user_id):
        if user is None:
            logger.error(f"**ERROR User {user_id} does not exist")
            raise Exception(f"User {user_id} does not exist")

        # Delete user from all teams
        if "teams" in user.to_dict():
            user_teams = user.to_dict()["teams"]
            for team in user_teams:
                team_users = team.get().to_dict()["users"]
                team_users.remove(user.reference)
                db.collection("teams").document(team.id).set({"users": team_users}, merge=True)

        # Delete user
        db.collection("users").document(user_id).delete()

    def delete_user_by_user_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        logger.info(f"Deleting user {user_id}")
        

        # Get user
        user = self.fetch_user_raw_by_user_id(db, user_id)
        self.finish_deleting_user(db, user, user_id)

        return User.deserialize(user.to_dict())

    def delete_user_by_db_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        logger.info(f"Deleting user {user_id}")

        # Get user
        user = self.fetch_user_raw_by_db_id(db, user_id)
        self.finish_deleting_user(db, user, user_id)

        return User.deserialize(user.to_dict())

        
    # ----------------------- Problem Statements --------------------------------------------
    
    def insert_problem_statement(self, problem_statement: ProblemStatement):
        db = self.get_db()

        # TODO: In this current form, you will overwrite any information that matches the same NPO name
        
        problem_statement.id = uuid.uuid1().hex
            
        collection = db.collection('problem_statements')

        insert_res = collection.document(problem_statement.id).set({
            "title": problem_statement.title,
            "description": problem_statement.description if 'description' in problem_statement else None,
            "first_thought_of": problem_statement.first_thought_of if 'first_thought_of' in problem_statement else None,
            "github": problem_statement.github if 'github' in problem_statement else None,
            # "references": TODO: What is this
            "status": problem_statement.status if 'status' in problem_statement else None        
        })

        logger.debug(f"Insert Result: {insert_res}")

        return problem_statement if insert_res is not None else None

DatabaseInterface.register(FirestoreDatabaseInterface)
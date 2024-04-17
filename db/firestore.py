import os
from datetime import datetime
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
import logging
from common.log import get_log_level

logger = logging.getLogger("ohack")
logger.setLevel(get_log_level())

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
        return user if insert_res is not None else None
    
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
        user = None
        temp = self.fetch_user_by_db_id_raw(db, id)

        if temp is not None:
            ref = temp.reference
            d = temp.to_dict()
            d['id'] = ref.id #Get the document id from the reference
            user = User.deserialize(d)

        return user

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

    def fetch_users(self):
        results = []
        db = self.get_db()
        docs = db.collection('users').stream()  # steam() gets all records
        if docs is None:
            pass
        else:
            for doc in docs:
                temp = doc.to_dict()
                temp['id'] = doc.reference.id
                if 'last_login' not in temp:
                    temp['last_login'] = ''
                
                if 'user_id' not in temp:
                    print(f'trash data skiping user {temp}')
                    continue

                results.append(User.deserialize(temp))
     
        return results

        
    # ----------------------- Problem Statements --------------------------------------------
    
    def fetch_problem_statements(self):
        results = []
        db = self.get_db()
        docs = db.collection('problem_statements').stream()  # steam() gets all records
        if docs is None:
            pass
        else:
            for doc in docs:
                # doc is an instance of google.cloud.firestore_v1.base_document.DocumentSnapshot
                # print(f'doc {doc}')
                temp = doc.to_dict()
                print(f'id type {type(doc.reference.id)}')
                temp['id'] = doc.reference.id
                results.append(ProblemStatement.deserialize(temp))
     
        return results

    def fetch_problem_statement(self, id):
        print(f'log level {safe_get_env_var("GLOBAL_LOG_LEVEL")}')
        print(f'log level {get_log_level()}')
        logger.debug(f'fetch_problem_statement :{id}')
        res = None
        db = self.get_db()
        try:
            raw = self.fetch_problem_statement_raw(db, id) # This is going to return a SimpleNamespace for imported rows.
            print(f'raw {raw}')
            print(f'raw.exists {raw.exists}')
            # print(f'raw.data {raw.data}')
            print(f'raw.reference {raw.reference}')
            print(f'raw.reference.id {raw.reference.id}')
            temp = raw.to_dict()
            print(f'temp {temp}')
            temp['id'] = raw.reference.id
            res = ProblemStatement.deserialize(temp) if temp is not None else None
        except KeyError as e:
            # A key error here means that ProblemStatement.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_problem_statement_by_id error: {e}')
        return res
    
    def fetch_problem_statement_raw(self, db, id):
        logger.debug(f'fetch_problem_statement_raw id:{id}')
        print(f"id {id}")
        p = db.collection('problem_statements').document(id).get()
        return p
    
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
    
    def update_problem_statement(self, problem_statement: ProblemStatement):
        db = self.get_db()

        # TODO: In this current form, you will overwrite any information that matches the same NPO name
            
        collection = db.collection('problem_statements')

        update_res = collection.document(problem_statement.id).set({
            "description": problem_statement.description if 'description' in problem_statement else None,
            "first_thought_of": problem_statement.first_thought_of if 'first_thought_of' in problem_statement else None,
            "github": problem_statement.github if 'github' in problem_statement else None,
            # "references": TODO: What is this
            "status": problem_statement.status if 'status' in problem_statement else None        
        })

        logger.debug(f"Insert Result: {update_res}")

        return problem_statement if update_res is not None else None
    
    def delete_problem_statement(self, db, problem_statement_id):
        # TODO: delete related entities
        p = self.fetch_problem_statement(problem_statement_id)
        
        if p is not None:
            # Delete problem statement
            db.collection("problem_statements").document(problem_statement_id).delete()

        return p
    
    def insert_helping(self, problem_statement_id, user: User, mentor_or_hacker):
        
        my_date = datetime.now()
        
        to_add = {
            "user": user.id,
            "slack_user": user.user_id,
            "type": mentor_or_hacker,
            "timestamp": my_date.isoformat()
        }

        db = self.get_db()

        problem_statement_doc = db.collection(
        'problem_statements').document(problem_statement_id)
    
        ps_dict = problem_statement_doc.get().to_dict()
        helping_list = []
        if "helping" in ps_dict:
            helping_list = ps_dict["helping"]
            logger.debug(f"Helping list: {helping_list}")

            helping_list.append(to_add)

        else:
            logger.debug(f"Start Helping list: {helping_list} * New list created for this problem")
            helping_list.append(to_add)


        logger.debug(f"End Helping list: {helping_list}")
        problem_result = problem_statement_doc.update({
            "helping": helping_list
        })

        return ProblemStatement.deserialize(ps_dict)
    
    # ----------------------- Hackathons ------------------------------------------

    def fetch_hackathons(self):
        hackathons = []
        db = self.get_db()  # this connects to our Firestore database
        docs = db.collection('hackathons').stream()

        for doc in docs:
            temp = doc.to_dict()
            temp["id"] = doc.id
            # print(f'temp {temp}')
            hackathons.append(Hackathon.deserialize(temp))

        return hackathons
    
    def insert_hackathon(self, h: Hackathon):
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #Set id
        h.id = uuid.uuid1().hex
        #TODO: Does this throw?

            #     {
    #     "donation_current": 0.0,
    #     "donation_goals": 0.0,
    #     "end_date": "2019-10-20",
    #     "id": "LSi9jQED7BWZw3DKaQAx",
    #     "image_url": "",
    #     "location": "Arizona",
    #     "start_date": "2019-10-19",
    #     "title": "",
    #     "type": ""
    # },

        insert_res = db.collection('hackathons').document(h.id).set({
            "donation_current": h.donation_current,
            "donation_goals": h.donation_goals,
            "title": h.title,
            "image_url": h.image_url,
            "location": h.location,
            "start_date": h.start_date,
            "end_date": h.end_date,
            "type": h.type
        })

        return h if insert_res is not None else None
       

        return h


DatabaseInterface.register(FirestoreDatabaseInterface)
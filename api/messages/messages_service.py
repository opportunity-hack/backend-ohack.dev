from common.utils import safe_get_env_var
from api.messages.message import Message
import json

import requests
import logging
import firebase_admin
from firebase_admin import credentials, firestore


logger = logging.getLogger("myapp")

auth0_domain = safe_get_env_var("AUTH0_DOMAIN")
auth0_client = safe_get_env_var("AUTH0_USER_MGMT_CLIENT_ID")
auth0_secret = safe_get_env_var("AUTH0_USER_MGMT_SECRET")


def get_public_message():
    logger.debug("Public")
    return Message(
        "aaThis is a public message."
    )


def get_protected_message():
    logger.debug("Protected")

    return Message(
        "This is a protected message."
    )


def get_admin_message():
    logger.debug("Admin")

    return Message(
        "This is an admin message."
    )


def get_single_npo(npo_id):
    
    logger.debug(f"get_npo start npo_id={npo_id}")
    db = firestore.client()      
    query = db.collection('nonprofits').where("id","==",npo_id).limit(1)
    doc = query.stream()
    
    if doc is None:
        logger.debug("get_npo end (no results)")
        return {}
    else:                        
        for ad in doc:
            d = ad.to_dict()
            print(d)        
            problem_statements = []
            if "problem_statements" in d:
                for ps in d["problem_statements"]:
                    problem_statements.append(ps.get().to_dict())

            logger.debug("get_npo end (with results)")
            return {
                "nonprofits":{
                "id": d["id"],
                "name": d["name"],
                "description": d["description"],
                "slack_channel": d["slack_channel"],
                "problem_statements": problem_statements
                }
            }
    return {}

def get_npo_list():
    logger.debug("NPO List")
    db = firestore.client()  
    docs = db.collection('nonprofits').stream() # steam() gets all records   
    if docs is None:
        return {[]}
    else:                
        results = []
        for doc in docs:
            d = doc.to_dict()    
            print(d)        
            problem_statements = []
            if "problem_statements" in d:
                for ps in d["problem_statements"]:
                    problem_statements.append(ps.get().to_dict())

            results.append(
                {
                    "id": d["id"],
                    "name": d["name"],
                    "description": d["description"],
                    "slack_channel": d["slack_channel"],
                    "problem_statements": problem_statements
                }
                    
            )
        return { "nonprofits": results }
    


def save_npo(json):
    db = firestore.client()  # this connects to our Firestore database
    logger.debug("NPO Save")    
    # TODO: In this current form, you will overwrite any information that matches the same NPO name

    name = json["name"]
    email = json["email"]
    npoName = json["npoName"]
    slack_channel = json["slack_channel"]
    website = json["website"]
    description = json["description"]
     
    collection = db.collection('nonprofits')
    
    insert_res = collection.document(npoName).set({
        "contact_email": [email], # TODO: Support more than one email
        "contact_people": [name], # TODO: Support more than one name
        "name": npoName,
        "slack_channel" :slack_channel,
        "website": website,
        "description":description
    })

    logger.debug(f"Insert Result: {insert_res}")

    return Message(
        "Saved NPO"
    )

def get_token():
    logger.debug("get_token start")

    url = f"https://{auth0_domain}/oauth/token"
    myobj = {
        "client_id": auth0_client,
        "client_secret": auth0_secret,
        "grant_type": "client_credentials",
        "audience": f"https://{auth0_domain}/api/v2/"
    }
    x = requests.post(url, data=myobj)
    x_j = x.json()
    logger.info("Got access token")
    logger.debug("get_token end")
    return x_j["access_token"]





# Ref: https://stackoverflow.com/questions/59138326/how-to-set-google-firebase-credentials-not-with-json-file-but-with-python-dict
# Instead of giving the code a json file, we use environment variables so we don't have to source control a secrets file
cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))


#We don't want this to be a file, we want to use env variables for security (we would have to check in this file)
#cred = credentials.Certificate("./api/messages/ohack-dev-firebase-adminsdk-hrr2l-933367ee29.json")
cred = credentials.Certificate(cert_env)
firebase_admin.initialize_app(credential=cred)


def save(user_id=None, email=None, last_login=None):
    print("Save Start")
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc

    if user_id is None or email is None or last_login is None:
        logger.error(f"Empty values provided for user_id: {user_id}, email: {email}, or last_login: {last_login}")
        return

    db = firestore.client()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(user_id)
    res = doc.get().to_dict()
    if res is None:
        insert_res = collection.document(user_id).set({
            "email_address": email,
            "last_login": last_login,
            "user_id": user_id,
            "badges":[
                "first_hackathon"
            ]
        })


        logger.debug(f"Insert Result: {insert_res}")
    else:
        # Found result already in DB, update
        update_res = collection.document(user_id).update({
            "last_login": last_login
        })
        logger.debug(f"Update Result: {update_res}")

    print(res)
    print("Save End")


def get_history(user_id):
    logger.debug("Get Hackathons Start")
    db = firestore.client()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(user_id)
    doc_get = doc.get()
    res = doc_get.to_dict()
    logger.debug(res)

    _problem_statements=[]
    if "problem_statements" in res:
        for h in res["problem_statements"]:
            _problem_statements.append(h.get().to_dict())

    _hackathons=[]
    if "hackathons" in res:
        for h in res["hackathons"]:
            rec = h.get().to_dict()
            nonprofits = []
            problem_statements = []

            for n in rec["nonprofits"]:
                npo_r = n.get().to_dict()
                
                if npo_r and "problem_statements" in npo_r:
                    # This is duplicate date as we should already have this
                    del npo_r["problem_statements"]
                nonprofits.append(npo_r)
            for ps in rec["problem_statements"]:
                problem_statements.append(ps.get().to_dict())

            _hackathons.append({
                "nonprofits": nonprofits,
                "problem_statements": problem_statements,
                "devpost_url": rec["devpost_url"],
                "location": rec["location"],
                "start_date": rec["start_date"]
            })

    _badges=[]
    if "badges" in res:
        for h in res["badges"]:
            _badges.append(h.get().to_dict())

    result = {
        "user_id": res["user_id"],
        "email_address" : res["email_address"],
        "badges" : _badges,
        "hackathons" : _hackathons,
        "problem_statements" : _problem_statements
    }

    logger.debug(f"RESULT\n{result}")
    return result




def get_profile_metadata(user_id):
    logger.debug("Profile Metadata")

    token = get_token()
    url = f"https://{auth0_domain}/api/v2/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    x = requests.get(url, headers=headers)
    x_j = x.json()

    logger.debug(x_j)

    email = x_j["email"]
    user_id = x_j["user_id"]
    last_login = x_j["last_login"]
    logger.debug(f"Auth0 Account Details:\nEmail: {email}\nUser ID: {user_id}\nLast Login:{last_login}")

    # Call firebase to see if account exists and save these details
    save(user_id=user_id, email=email, last_login=last_login)

    #response = f"{x_j}"
    response = get_history(user_id)
    logger.debug(response)


    return Message(response)

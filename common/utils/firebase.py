import firebase_admin
from firebase_admin import credentials, firestore
from . import safe_get_env_var
import json

cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))
cred = credentials.Certificate(cert_env)
firebase_admin.initialize_app(credential=cred)

def get_db():
    #mock_db = MockFirestore()
    return firestore.client()

def get_team_by_name(team_name):
    db = get_db()  # this connects to our Firestore database
    docs = db.collection('teams').where("name", "==", team_name).stream()

    for doc in docs:
        adict = doc.to_dict()
        return adict["slack_channel"]


def get_users_in_team_by_name(team_name):
    db = get_db()  # this connects to our Firestore database
    docs = db.collection('teams').where("name", "==", team_name).stream()

    user_list = []
    for doc in docs:
        adict = doc.to_dict()
        for user in adict["users"]:
            auser = user.get().to_dict()
            user_list.append(auser)
    return user_list

import firebase_admin
from firebase_admin import credentials, firestore
from . import safe_get_env_var
import json
from mockfirestore import MockFirestore

cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))
cred = credentials.Certificate(cert_env)
firebase_admin.initialize_app(credential=cred)

mockfirestore = MockFirestore() #Only used when testing 

# add logger
import logging
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)


def get_db():
    if safe_get_env_var("ENVIRONMENT") == "test":
        return mockfirestore
    
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

def get_user_by_user_id(user_id):
    SLACK_PREFIX = "oauth2|slack|T1Q7936BH-"
    slack_user_id = f"{SLACK_PREFIX}{user_id}"
    db = get_db()  # this connects to our Firestore database
    docs = db.collection('users').where("user_id", "==", slack_user_id).stream()

    for doc in docs:
        adict = doc.to_dict()
        adict["id"] = doc.id
        return adict


def add_certificate(user_id, certificate):
    db = get_db()  # this connects to our Firestore database
    logger.info(f"Adding certificate {certificate} to user {user_id}")

    # Get history from user    
    user = db.collection("users").document(user_id).get()

    if not user.exists:
        logger.error(f"**ERROR User {user_id} does not exist")
        raise Exception(f"User {user_id} does not exist")

    # Set this as empty if it doesn't exist   
    user_history = {}    

    if "history" in user.to_dict():
        user_history = user.to_dict()["history"]
    
    # handle when certificate does not exist
    if "certificates" not in user_history:
        user_history["certificates"] = []

    user_history["certificates"].append(certificate)

    # Update user
    db.collection("users").document(user_id).set({"history": user_history}, merge=True)    
   

def add_hearts_for_user(user_id, hearts, reason):
    db = get_db()  # this connects to our Firestore database
    logger.info(f"Adding {hearts} hearts to user {user_id} for reason {reason}")

    # Get history from user    
    user = db.collection("users").document(user_id).get()

    if not user.exists:
        logger.error(f"**ERROR User {user_id} does not exist")
        raise Exception(f"User {user_id} does not exist")
    
    user_history = user.to_dict()["history"]
    
    # 4 things in "how"
    if "how" not in user_history:
        user_history["how"] = {}
    if "code_reliability" not in user_history["how"]:
        user_history["how"]["code_reliability"] = 0
    if "customer_driven_innovation_and_design_thinking" not in user_history["how"]:
        user_history["how"]["customer_driven_innovation_and_design_thinking"] = 0
    if "iterations_of_code_pushed_to_production" not in user_history["how"]:
        user_history["how"]["iterations_of_code_pushed_to_production"] = 0
    if "standups_completed" not in user_history["how"]:
        user_history["how"]["standups_completed"] = 0    
    # 8 things in "what"
    if "what" not in user_history:
        user_history["what"] = {}                                
    if "code_quality" not in user_history["what"]:
        user_history["what"]["code_quality"] = 0
    if "design_architecture" not in user_history["what"]:
        user_history["what"]["design_architecture"] = 0
    if "documentation" not in user_history["what"]:
        user_history["what"]["documentation"] = 0
    if "observability" not in user_history["what"]:
        user_history["what"]["observability"] = 0
    if "productionalized_projects" not in user_history["what"]:
        user_history["what"]["productionalized_projects"] = 0
    if "requirements_gathering" not in user_history["what"]:
        user_history["what"]["requirements_gathering"] = 0
    if "unit_test_coverage" not in user_history["what"]:
        user_history["what"]["unit_test_coverage"] = 0
    if "unit_test_writing" not in user_history["what"]:
        user_history["what"]["unit_test_writing"] = 0
        
        

    # TODO: We should be using enums instead of static strings
    #  Enums allow us to centralize our strings and make it easier to refactor
    
    # 4 things in "how"
    if reason == "code_reliability":
        user_history["how"]["code_reliability"] += hearts
    elif reason == "customer_driven_innovation_and_design_thinking":
        user_history["how"]["customer_driven_innovation_and_design_thinking"] += hearts
    elif reason == "iterations_of_code_pushed_to_production":
        user_history["how"]["iterations_of_code_pushed_to_production"] += hearts
    elif reason == "standups_completed":
        user_history["how"]["standups_completed"] += hearts
    # 8 things in "what"
    elif reason == "code_quality":
        user_history["what"]["code_quality"] += hearts
    elif reason == "design_architecture":
        user_history["what"]["design_architecture"] += hearts
    elif reason == "documentation":
        user_history["what"]["documentation"] += hearts
    elif reason == "observability":
        user_history["what"]["observability"] += hearts
    elif reason == "productionalized_projects":
        user_history["what"]["productionalized_projects"] += hearts
    elif reason == "requirements_gathering":
        user_history["what"]["requirements_gathering"] += hearts
    elif reason == "unit_test_coverage":
        user_history["what"]["unit_test_coverage"] += hearts
    elif reason == "unit_test_writing":
        user_history["what"]["unit_test_writing"] += hearts
    else:
        raise Exception(f"Invalid reason: {reason}")                

    # Update user history
    db.collection("users").document(user_id).set({"history": user_history}, merge=True)
    
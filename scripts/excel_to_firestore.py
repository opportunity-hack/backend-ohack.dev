# Read data from XLS exported from Google Sheets and import it into FireStore DB




import uuid
import json
import os
from firebase_admin import credentials, firestore
import firebase_admin
cert_env = json.loads(os.getenv("FIREBASE_CERT_CONFIG"))
cred = credentials.Certificate(cert_env)
firebase_admin.initialize_app(credential=cred)

from numpy import NaN
import pandas as pd
from cryptography.fernet import Fernet


from dotenv import load_dotenv
load_dotenv()




enc_dec_key = os.getenv("ENC_DEC_KEY")


def normalize_string(astr):
    if astr == "" or astr == None or astr == "nan" or astr == NaN:
        return ""
    astr = str(astr)
    astr = astr.replace("-", "").replace("(", "").replace(")", "")

    if astr == "nan":
        return ""
    astr = astr.strip()
    return astr

def remove_http(astr):
    astr = normalize_string(astr)
    return astr.replace("http://", "").replace("https://", "")

def normalize_phone(astr):
    astr = normalize_string(astr)
    
    return astr

def encrypt(astr, kind="string"):
    if enc_dec_key == None:
        print("No ENC_DEC_KEY env variable found")
        return
    
    fernet = Fernet(enc_dec_key)    
    if kind == "phone":
        astr = normalize_phone(astr)
    else:
        astr = normalize_string(astr)

    encMessage = fernet.encrypt(astr.encode())
    encMessageStr = encMessage.decode()
    
    return encMessageStr


def decrypt(astr):
    if enc_dec_key == None:
        print("No ENC_DEC_KEY env variable found")
        return

    fernet = Fernet(enc_dec_key)
    decMessage = fernet.decrypt(astr.encode()).decode()

    return decMessage


def save_npo(email, npoName, website, description, contact_people):
    db = firestore.client()  # this connects to our Firestore database        

    doc_id = uuid.uuid1().hex

    collection = db.collection('nonprofits')

    insert_res = collection.document(doc_id).set({
        "contact_email": [email],  # TODO: Support more than one email
        "contact_people": [contact_people],  # TODO: Support more than one name
        "name": npoName,
        "slack_channel": "",
        "website": website,
        "description": description
    })

    
def insert_npo_into_db(row):
    email = encrypt(row["Username"])
    npoName = normalize_string(row[" Name of Organization"])
    website = remove_http(row["Website"])
    phone = encrypt(row["Contact phone number"], kind="phone")
    description = normalize_string(row["Please provide a brief summary or your organization's purpose and history."])
    contact_people = normalize_string(row[" Contact Person"])
    print(contact_people)
    save_npo(email, npoName, website, description, contact_people)

    #print(email, name, website, phone, description)
    


df = pd.read_csv("Nonprofit Tracking - Nonprofits.csv")
df.apply(insert_npo_into_db, axis=1)

from db.interface import DatabaseInterface
from model.user import User
import os

#https://github.com/ptmcg/littletable/blob/master/how_to_use_littletable.md

USERS_CSV_FILE_PATH = "../test/data/Test Users - Sheet1.csv"

USERS_EXCEL_FILE_PATH = "../test/data/users.xlsx"

import littletable as lt

users = None

if os.path.exists(USERS_EXCEL_FILE_PATH):
    users = lt.Table().excel_import(USERS_EXCEL_FILE_PATH)
else:
    users = lt.Table().csv_import(USERS_CSV_FILE_PATH)

def flush():
    users.excel_export(USERS_EXCEL_FILE_PATH)

class InMemoryDatabaseInterface(DatabaseInterface):
    def get_user(self, user_id):
        return users.where(user_id=user_id)
    
    def get_user_by_doc_id(self, id):
        return users.where(id=id)
    
    #TODO: Kill with fire. Leaky abstraction
    def get_user_doc_reference(self, user_id):
        return None
    
    def save_user(self, doc_id, email, last_login, user_id, profile_image, name, nickname):
        d = {'user_id': user_id, 
             'id': doc_id, 
             'email': email, 
             'last_login': last_login, 
             'profile_image': profile_image,
             'name': name,
             'nickname': nickname}
        users.insert(d)

    def upsert_user(self, user_id, last_login,  profile_image, name, nickname):
        d = users.where(user_id=user_id)

        d['last_login'] = last_login
        d['the_user.user_id'] = user_id
        d['profile_image'] = profile_image
        d['name'] = name
        d['nickname'] = nickname

DatabaseInterface.register(InMemoryDatabaseInterface)
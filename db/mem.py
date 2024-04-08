from db.interface import DatabaseInterface
from model.user import User
import os

#https://github.com/ptmcg/littletable/blob/master/how_to_use_littletable.md

USERS_CSV_FILE_PATH = "../test/data/Test Users - Sheet1.csv"

USERS_EXCEL_FILE_PATH = "../test/data/users.xlsx"

import littletable as lt

users = None

if os.path.exists(USERS_EXCEL_FILE_PATH):
    users = lt.Table().excel_import(USERS_EXCEL_FILE_PATH, transforms={'id': int})
else:
    users = lt.Table().csv_import(USERS_CSV_FILE_PATH, transforms={'id': int})

users.create_index('id', unique=True)
users.create_index('user_id', unique=True)

def get_next_user_id() -> int:
    return max([i for i in users.all.id]) + 1

def flush():
    users.excel_export(USERS_EXCEL_FILE_PATH)

class InMemoryDatabaseInterface(DatabaseInterface):
    def get_user(self, user_id):
        res = None
        try:
            res = users.by.user_id[user_id]
        except KeyError:
            pass
        return res
    
    def get_user_by_doc_id(self, id):
        res = None
        try:
            res = users.by.id[id]
        except KeyError:
            pass
        return res
    
    #TODO: Kill with fire. Leaky abstraction
    def get_user_doc_reference(self, user_id):
        return None
    
    def insert_user(self, user:User):
        user.id = get_next_user_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {'id': user.id, 
             'name': user.name,
             'email': user.email_address, 
             'user_id': user.user_id, 
             'last_login': user.last_login, 
             'profile_image': user.profile_image,
             'nickname': user.nickname}
        
        print(f'Inserting user\n: {d}')

        users.insert(d)

    def upsert_user(self, user_id, last_login,  profile_image, name, nickname):
        d = users.by.user_id[user_id]

        d['last_login'] = last_login
        d['the_user.user_id'] = user_id
        d['profile_image'] = profile_image
        d['name'] = name
        d['nickname'] = nickname

DatabaseInterface.register(InMemoryDatabaseInterface)
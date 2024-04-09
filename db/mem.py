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

    def fetch_user_by_user_id_raw(self, user_id):
        res = None
        try:
            res = users.by.user_id[user_id] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            print(f'fetch_user_by_user_id_raw error: {e}')
        return res

    def fetch_user_by_user_id(self, user_id):
        res = None
        try:
            temp = self.fetch_user_by_user_id_raw(user_id) # This is going to return a SimpleNamespace for imported rows.
            res = User.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            # A key error here means that User.deserialize was expecting a property in the data that wasn't there
            print(f'fetch_user_by_user_id error: {e}')
        return res
    
    def fetch_user_by_db_id_raw(self, id):
        res = None
        try:
            res = users.by.id[id] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            print(f'fetch_user_by_db_id error: {e}')
        return res

    def fetch_user_by_db_id(self, id):
        res = None
        try:
            temp = self.fetch_user_by_db_id_raw(id) # This is going to return a SimpleNamespace for imported rows.
            res = User.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            print(f'fetch_user_by_db_id error: {e}')
        return res
    
    #TODO: Kill with fire. Leaky abstraction
    def get_user_doc_reference(self, user_id):
        return None
    
    def insert_user(self, user:User):
        user.id = get_next_user_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {'id': user.id, 
             'name': user.name,
             'email_address': user.email_address, 
             'user_id': user.user_id, 
             'last_login': user.last_login, 
             'profile_image': user.profile_image,
             'nickname': user.nickname}
        
        print(f'Inserting user\n: {d}')

        users.insert(d)

        return User.deserialize(d)

    def update_user(self, user: User):
        d = users.by.id[user.id]

        d.last_login = user.last_login
        d.profile_image = user.profile_image
        d.name = user.name
        d.nickname = user.nickname
        return User.deserialize(vars(d))

    def delete_user_by_user_id(self, user_id):
        raw = self.fetch_user_by_user_id_raw(user_id)
        users.remove(raw)
        return User.deserialize(vars(raw))

    def delete_user_by_db_id(self, id):
        raw = self.fetch_user_by_db_id_raw(id)
        users.remove(raw)
        return User.deserialize(vars(raw))
        pass

DatabaseInterface.register(InMemoryDatabaseInterface)
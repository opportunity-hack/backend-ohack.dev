from db.interface import DatabaseInterface
from model.problem_statement import ProblemStatement
from model.user import User
import os
import littletable as lt #https://github.com/ptmcg/littletable/blob/master/how_to_use_littletable.md

USERS_CSV_FILE_PATH = "../test/data/OHack Test Data - Users.csv"
USERS_EXCEL_FILE_PATH = "../test/data/users.xlsx"

PROBLEM_STATEMENTS_CSV_FILE_PATH = "../test/data/OHack Test Data - Problem Statements.csv"
PROBLEM_STATEMENTS_EXCEL_FILE_PATH = "../test/data/problem_statements.xlsx"

class InMemoryDatabaseInterface(DatabaseInterface):

    users = None

    def __init__(self):
        super().__init__()
        self.init_users()
        self.init_problem_statements()

    # Users

    def fetch_user_by_user_id_raw(self, user_id):
        res = None
        try:
            res = self.users.by.user_id[user_id] # This is going to return a SimpleNamespace for imported rows.
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
            res = self.users.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            print(f'fetch_user_by_db_id_raw error: {e}')
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
        user.id = self.get_next_user_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {'id': user.id, 
             'name': user.name,
             'email_address': user.email_address, 
             'user_id': user.user_id, 
             'last_login': user.last_login, 
             'profile_image': user.profile_image,
             'nickname': user.nickname}
        
        print(f'Inserting user\n: {d}')

        self.users.insert(d)

        self.flush_users()

        return User.deserialize(d)

    def update_user(self, user: User):
        d = self.users.by.id[user.id]

        d.last_login = user.last_login
        d.profile_image = user.profile_image
        d.name = user.name
        d.nickname = user.nickname

        self.flush_users()

        return User.deserialize(vars(d))

    def delete_user_by_user_id(self, user_id):
        raw = self.fetch_user_by_user_id_raw(user_id)
        self.users.remove(raw)

        self.flush_users()

        return User.deserialize(vars(raw))

    def delete_user_by_db_id(self, id):
        raw = self.fetch_user_by_db_id_raw(id)
        self.users.remove(raw)

        self.flush_users()

        return User.deserialize(vars(raw))
    
    # Problem Statements
    def fetch_problem_statement_by_id(self, id):
        res = None
        try:
            temp = self.fetch_problem_statement_by_id_raw(id) # This is going to return a SimpleNamespace for imported rows.
            res = ProblemStatement.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            # A key error here means that ProblemStatement.deserialize was expecting a property in the data that wasn't there
            print(f'fetch_problem_statement_by_id error: {e}')
        return res
    
    def fetch_problem_statement_by_id_raw(self, id):
        res = None
        try:
            res = self.problem_statements.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            print(f'fetch_problem_statement_by_id_raw error: {e}')
        return res


    # Intialization

    def init_users(self):
        if os.path.exists(USERS_EXCEL_FILE_PATH):
            self.users = lt.Table().excel_import(USERS_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.users = lt.Table().csv_import(USERS_CSV_FILE_PATH, transforms={'id': int})

        self.users.create_index('id', unique=True)
        self.users.create_index('user_id', unique=True)

    def get_next_user_id(self) -> int:
        return max([i for i in self.users.all.id]) + 1

    def flush_users(self):
        self.users.excel_export(USERS_EXCEL_FILE_PATH)

    def init_problem_statements(self):
        if os.path.exists(PROBLEM_STATEMENTS_EXCEL_FILE_PATH):
            self.problem_statements = lt.Table().excel_import(PROBLEM_STATEMENTS_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.problem_statements = lt.Table().csv_import(PROBLEM_STATEMENTS_CSV_FILE_PATH, transforms={'id': int})

        self.problem_statements.create_index('id', unique=True)

    def get_next_problem_statement_id(self) -> int:
        return max([i for i in self.problem_statements.all.id]) + 1

    def flush_problem_statements(self):
        self.problem_statements.excel_export(PROBLEM_STATEMENTS_EXCEL_FILE_PATH)

DatabaseInterface.register(InMemoryDatabaseInterface)


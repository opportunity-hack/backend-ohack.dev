from db.interface import DatabaseInterface
from model.donation import CurrentDonations, DonationGoals
from model.hackathon import Hackathon
from model.nonprofit import Nonprofit
from model.problem_statement import Helping, ProblemStatement
from model.user import User
import os
import littletable as lt #https://github.com/ptmcg/littletable/blob/master/how_to_use_littletable.md
from datetime import datetime
import logging
from common.log import get_log_level

logger = logging.getLogger("ohack")
logger.setLevel(get_log_level())

# WARNING, if the column isn't defined in the CSV, it won't be saved in the xlsx
USERS_CSV_FILE_PATH = "../test/data/OHack Test Data - Users.csv"
USERS_EXCEL_FILE_PATH = "../test/data/users.xlsx"

PROBLEM_STATEMENTS_CSV_FILE_PATH = "../test/data/OHack Test Data - Problem Statements.csv"
PROBLEM_STATEMENTS_EXCEL_FILE_PATH = "../test/data/problem_statements.xlsx"

PROBLEM_STATEMENT_HELPING_CSV_FILE_PATH = "../test/data/OHack Test Data - Problem Statement Helping.csv"
PROBLEM_STATEMENT_HELPING_EXCEL_FILE_PATH = "../test/data/problem_statement_helping.xlsx"

HACKATHON_CSV_FILE_PATH = "../test/data/OHack Test Data - Nonprofits.csv"
HACKATHON_EXCEL_FILE_PATH = "../test/data/nonprofits.xlsx"

NONPROFIT_CSV_FILE_PATH = "../test/data/OHack Test Data - Nonprofits.csv"
NONPROFIT_EXCEL_FILE_PATH = "../test/data/nonprofits.xlsx"

CURRENT_DONATIONS_CSV_FILE_PATH = "../test/data/OHack Test Data - Current Donations.csv"
CURRENT_DONATIONS_EXCEL_FILE_PATH = "../test/data/current_donations.xlsx"

DONATION_GOALS_CSV_FILE_PATH = "../test/data/OHack Test Data - Donation Goals.csv"
DONATION_GOALS_EXCEL_FILE_PATH = "../test/data/donation_goals.xlsx"

HACKATHON_CURRENT_DONATIONS_CSV_FILE_PATH = "../test/data/OHack Test Data - Hackathon Current Donations.csv"
HACKATHON_CURRENT_DONATIONS_EXCEL_FILE_PATH = "../test/data/hackathon_current_donations.xlsx"

HACKATHON_DONATION_GOALS_CSV_FILE_PATH = "../test/data/OHack Test Data - Hackathon Donation Goals.csv"
HACKATHON_DONATION_GOALS_EXCEL_FILE_PATH = "../test/data/hackathon_donation_goals.xlsx"

PROBLEM_STATEMENT_HACKATHONS_CSV_FILE_PATH = "../test/data/OHack Test Data - Problem Statement Hackathons.csv"
PROBLEM_STATEMENT_HACKATHONS_EXCEL_FILE_PATH = "../test/data/problem_statement_hackathons.xlsx"


class InMemoryDatabaseInterface(DatabaseInterface):

    users = None
    problem_statements = None
    problem_statement_helping = None
    hackathons = None
    current_donations = None
    donation_goals = None
    hackathon_current_donations = None
    hackathon_donation_goals = None
    problem_statement_hackathons = None
    nonprofits = None

    def __init__(self):
        super().__init__()
        self.init_users()
        self.init_problem_statements()
        self.init_problem_statement_helping()
        self.init_hackathons()
        self.init_hackathon_current_donations()
        self.init_hackathon_donation_goals()
        self.init_current_donations()
        self.init_donation_goals()
        self.init_problem_statement_hackathons()
        self.init_nonprofits()

    # ----------------------- Users -------------------------------------------- #

    def fetch_user_by_user_id_raw(self, user_id):
        res = None
        try:
            res = self.users.by.user_id[user_id] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_user_by_user_id_raw error: {e}')
        return res

    def fetch_user_by_user_id(self, user_id):
        res = None
        try:
            temp = self.fetch_user_by_user_id_raw(user_id) # This is going to return a SimpleNamespace for imported rows.
            res = User.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            # A key error here means that User.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_user_by_user_id error: {e}')
        return res
    
    def fetch_user_by_db_id_raw(self, id):
        res = None
        try:
            res = self.users.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_user_by_db_id_raw error: {e}')
        return res

    def fetch_user_by_db_id(self, id):
        res = None
        try:
            temp = self.fetch_user_by_db_id_raw(id) # This is going to return a SimpleNamespace for imported rows.
            res = User.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            logger.debug(f'fetch_user_by_db_id error: {e}')
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
        
        logger.debug(f'Inserting user\n: {d}')

        self.users.insert(d)

        self.flush_users()

        return user

    def update_user(self, user: User):
        # TODO: Maybe, call self.problem_statements.add_field to add fields that are on the class but not the fetched object: https://pythonhosted.org/littletable/littletable.Table-class.html#add_field

        d = self.users.by.id[int(user.id)]
        d.id = int(user.id)

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
    
    def fetch_users(self):
        res = []

        for p in self.users:
            res.append(User.deserialize(vars(p)))

        return res
    
    # ----------------------- Problem Statements --------------------------------------------
    
    def fetch_problem_statement(self, id):
        res = None
        try:
            temp = self.fetch_problem_statement_raw(id) # This is going to return a SimpleNamespace for imported rows.
            temp.id = id
            res = ProblemStatement.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            # A key error here means that ProblemStatement.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_problem_statement_by_id error: {e}')
        return res
    
    def fetch_problem_statement_raw(self, id):
        res = None
        try:
            res = self.problem_statements.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_problem_statement_by_id_raw error: {e}')
        return res
    
    def fetch_problem_statements(self):
        res = []

        for p in self.problem_statements:
            res.append(ProblemStatement.deserialize(vars(p)))

        return res

    def insert_problem_statement(self, problem_statement: ProblemStatement):
        problem_statement.id = self.get_next_problem_statement_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {'id': problem_statement.id, 
             'title': problem_statement.title,
             'description': problem_statement.description, 
             'first_thought_of': problem_statement.first_thought_of, 
             'github': problem_statement.github, 
             'profile_image': problem_statement.status}
        
        logger.debug(f'Inserting problem statement\n: {d}')

        self.problem_statements.insert(d)

        self.flush_problem_statements()

        return problem_statement
    
    def update_problem_statement(self, problem_statement: ProblemStatement):

        # TODO: Maybe, call self.problem_statements.add_field to add fields that are on the class but not the fetched object: https://pythonhosted.org/littletable/littletable.Table-class.html#add_field

        d = self.problem_statements.by.id[int(problem_statement.id)]
        d.id = int(problem_statement.id)

        d.title = problem_statement.title
        d.description = problem_statement.description
        d.first_thought_of = problem_statement.first_thought_of
        d.github = problem_statement.github
        d.status = problem_statement.status

        self.flush_problem_statements()

        return ProblemStatement.deserialize(vars(d))

    def delete_problem_statement(self, problem_statement_id):
        # TODO: delete related entities
        temp = self.fetch_problem_statement_raw(problem_statement_id)
        p: ProblemStatement | None = None
        if temp is not None:
            # Delete problem statement
            self.problem_statements.remove(temp)
            temp.id = problem_statement_id 
            p = ProblemStatement.deserialize(vars(temp))

            self.flush_problem_statements()

        return p
    
    def fetch_helping(self, problem_statement_id):
        all_helping = []

        res = self.problem_statement_helping.where(lambda j: j.problem_statement_id == int(problem_statement_id))

        for helping in res:
            h = Helping.deserialize(vars(helping))
            h.user = self.fetch_user_by_db_id(h.user_db_id)
            all_helping.append(h)

        return all_helping

    def delete_helping(self, problem_statement_id, user: User):
        p = self.fetch_problem_statement_raw(problem_statement_id)

        res = self.problem_statement_helping.where(lambda j: j.problem_statement_id == int(problem_statement_id) and j.user_db_id == user.id)

        link = None
        try:
            link, *rest = res
        except Exception as e:
            pass

        if link is not None:
            self.problem_statement_helping.remove(link)

        problem_statement = ProblemStatement.deserialize(vars(p))

        problem_statement.helping = self.fetch_helping(problem_statement_id)

        self.flush_problem_statement_helping()

        return problem_statement

    def insert_helping(self, problem_statement_id, user: User, mentor_or_hacker, helping_date):
        p = self.fetch_problem_statement_raw(problem_statement_id)

        res = self.problem_statement_helping.where(lambda j: j.problem_statement_id == int(problem_statement_id) and j.user_db_id == user.id)

        link = None
        try:
            link, *rest = res
        except Exception as e:
            pass

        if link is None:
            self.problem_statement_helping.insert({
                'user_db_id': int(user.id), 
                'problem_statement_id': int(problem_statement_id),
                'mentor_or_hacker': mentor_or_hacker,
                'timestamp': helping_date})

        problem_statement = ProblemStatement.deserialize(vars(p))

        problem_statement.helping = self.fetch_helping(problem_statement_id)

        self.flush_problem_statement_helping()

        return problem_statement
    
    def insert_problem_statement_hackathon(self, problem_statement: ProblemStatement, hackathon: Hackathon):

        self.problem_statement_hackathons.insert({
                'problem_statement_id': int(problem_statement.id),
                'hackathon_id': int(hackathon.id)})
        
        self.flush_problem_statement_hackathons()

        return self.fetch_problem_statement(problem_statement.id)
    
    def update_problem_statement_hackathons(self, problem_statement: ProblemStatement, hackathons):

        existing_joins = self.problem_statement_hackathons.where(lambda j: j.problem_statement_id == problem_statement.id)

        for e in existing_joins:
            match = next((x for x in hackathons if x.id == e.hackathon_id), None)
            if match is None:
                print(f'removing {e}')
                self.problem_statement_hackathons.remove(e)

        for h in hackathons:
            match = next((x for x in existing_joins if x.hackathon_id == h.id), None)
            if match is None:
                to_insert = {
                    'problem_statement_id': int(problem_statement.id),
                    'hackathon_id': int(h.id)
                }
                print(f"inserting {to_insert}")
                self.problem_statement_hackathons.insert(to_insert)

        self.flush_problem_statement_hackathons()

        return self.fetch_problem_statement(problem_statement.id)

    # ----------------------- Hackathons ------------------------------------------
    
    def fetch_hackathons(self):
        res = []

        for p in self.hackathons:
            h = Hackathon.deserialize(vars(p))

            if h is not None:
                h.donation_goals = self.fetch_donation_goals_by_hackathon_id(h.id)
                h.donation_current = self.fetch_current_donations_by_hackathon_id(h.id)
                
                res.append(h)

        return res

    def fetch_hackathon(self, id):
        res = None
        try:
            temp = self.fetch_hackathon_raw(id) # This is going to return a SimpleNamespace for imported rows.
            temp.id = id
            res = Hackathon.deserialize(vars(temp)) if temp is not None else None
        
            if res is not None:
                res.donation_goals = self.fetch_donation_goals_by_hackathon_id(id)
                res.donation_current = self.fetch_current_donations_by_hackathon_id(id)
                pass

        except KeyError as e:
            # A key error here means that Hackathon.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_hackathon error: {e}')
        return res
    
    def fetch_hackathon_raw(self, id):
        res = None
        try:
            res = self.hackathons.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_hackathon_raw error: {e}')
        return res
    
    def fetch_donation_goals_by_hackathon_id(self, id):
        res = None
        try:
            temp = self.fetch_donation_goals_by_hackathon_id_raw(id) # This is going to return a SimpleNamespace for imported rows.
            
            if temp is not None:
                temp.id = id
                res = DonationGoals.deserialize(vars(temp))

        except KeyError as e:
            # A key error here means that DonationGoals.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_donation_goals_by_hackathon_id error: {e}')
        return res

    def fetch_donation_goals_by_hackathon_id_raw(self, hackathon_id):
        res = None
        try:
            res, *rest = self.hackathon_donation_goals.join(
                self.donation_goals, 
                donation_goals_id="id"
                ).where(lambda g: g.hackathon_id == int(hackathon_id)) # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_donation_goals_by_hackathon_id_raw error: {e}')
        except Exception as e1:
            # print(f'ex : {e1}')
            pass # This means that there weren't enough values to unpack, thus the result set was empty
        return res

    def fetch_current_donations_by_hackathon_id(self, id):
        res = None
        try:
            temp = self.fetch_current_donations_by_hackathon_id_raw(id) # This is going to return a SimpleNamespace for imported rows.
            
            if temp is not None:
                temp.id = id
                res = CurrentDonations.deserialize(vars(temp))

        except KeyError as e:
            # A key error here means that CurrentDonations.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_current_donations_by_hackathon_id error: {e}')
        return res

    def fetch_current_donations_by_hackathon_id_raw(self, hackathon_id):
        res = None
        try:
            res, *rest =  self.hackathon_current_donations.join(
                self.current_donations,
                current_donations_id="id"
                ).where(lambda g: g.hackathon_id == int(hackathon_id))
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_current_donations_by_hackathon_id_raw error: {e}')
        except Exception as e1:
            print(f'ex : {e1}')
            pass # this means the result set was empty
        return res

    
    def insert_hackathon(self, h:Hackathon):
        h.id = self.get_next_hackathon_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {
            "id": h.id,
            "title": h.title,
            "location": h.location,
            "start_date": h.start_date,
            "end_date": h.end_date,
            "image_url": h.image_url,
        }
        
        logger.debug(f'Inserting hackathon\n: {h}')

        self.hackathons.insert(h)

        self.flush_hackathons()

        return h

    #--------------------------------------- NPOs ------------------------------ #
    def fetch_npos(self):
        res = []

        for p in self.nonprofits:
            res.append(Nonprofit.deserialize(vars(p)))

        return res
    
    def fetch_npo_raw(self, id):
        res = None
        try:
            res = self.nonprofits.by.id[int(id)] # This is going to return a SimpleNamespace for imported rows.
        except KeyError as e:
            # A key error here means that littletable could not convert the loaded row into a SimpleNamespace because the row was missing a property
            logger.debug(f'fetch_npo_raw error: {e}')
        return res
    
    def fetch_npo(self, id):
        res = None
        try:
            temp = self.fetch_npo_raw(id) # This is going to return a SimpleNamespace for imported rows.
            res = Nonprofit.deserialize(vars(temp)) if temp is not None else None
        except KeyError as e:
            # A key error here means that User.deserialize was expecting a property in the data that wasn't there
            logger.debug(f'fetch_npo error: {e}')
        return res

    def insert_nonprofit(self, npo: Nonprofit):

        npo.id = self.get_next_nonprofit_id()

        # Fields on here need to show up in exactly the column order in the CSV
        d = {
            "id": npo.id,
            "name": npo.name,
            "slack_channel": npo.slack_channel,
            "website": npo.website,
            "description": npo.description,
            "need": npo.need
        }
        
        logger.debug(f'Inserting nonprofit\n: {d}')

        self.nonprofits.insert(d)

        self.flush_nonprofits()

        return npo

    def update_nonprofit(self, nonprofit: Nonprofit):

        # TODO: Maybe, call self.problem_statements.add_field to add fields that are on the class but not the fetched object: https://pythonhosted.org/littletable/littletable.Table-class.html#add_field

        d = self.nonprofits.by.id[int(nonprofit.id)]
        d.id = int(nonprofit.id)

        d.name = nonprofit.name
        d.slack_channel = nonprofit.slack_channel
        d.website = nonprofit.website
        d.description = nonprofit.description
        d.need = nonprofit.need

        logger.debug(f'Updating nonprofit\n: {d}')

        self.flush_nonprofits()

        return Nonprofit.deserialize(vars(d))

    #--------------------------------------- Intialization ------------------------------ #

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

    def init_problem_statement_helping(self):
        if os.path.exists(PROBLEM_STATEMENT_HELPING_EXCEL_FILE_PATH):
            self.problem_statement_helping = lt.Table().excel_import(PROBLEM_STATEMENT_HELPING_EXCEL_FILE_PATH, transforms={'user_db_id': int, 'problem_statement_id': int})
        else:
            self.problem_statement_helping = lt.Table().csv_import(PROBLEM_STATEMENT_HELPING_CSV_FILE_PATH, transforms={'user_db_id': int, 'problem_statement_id': int})

    def flush_problem_statement_helping(self):
        self.problem_statement_helping.excel_export(PROBLEM_STATEMENT_HELPING_EXCEL_FILE_PATH)
    

    def init_hackathons(self):
        if os.path.exists(HACKATHON_EXCEL_FILE_PATH):
            self.hackathons = lt.Table().excel_import(HACKATHON_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.hackathons = lt.Table().csv_import(HACKATHON_CSV_FILE_PATH, transforms={'id': int})

        self.hackathons.create_index('id', unique=True)

    def get_next_hackathon_id(self) -> int:
        return max([i for i in self.hackathons.all.id]) + 1
    
    def flush_hackathons(self):
        self.hackathons.excel_export(HACKATHON_EXCEL_FILE_PATH)

    def init_hackathon_current_donations(self):
        if os.path.exists(HACKATHON_CURRENT_DONATIONS_EXCEL_FILE_PATH):
            self.hackathon_current_donations = lt.Table().excel_import(HACKATHON_CURRENT_DONATIONS_EXCEL_FILE_PATH, transforms={'hackathon_id': int, 'current_donations_id': int} )
        else:
            self.hackathon_current_donations = lt.Table().csv_import(HACKATHON_CURRENT_DONATIONS_CSV_FILE_PATH, transforms={'hackathon_id': int, 'current_donations_id': int})

    def flush_hackathon_current_donations(self):
        self.hackathon_current_donations.excel_export(HACKATHON_CURRENT_DONATIONS_EXCEL_FILE_PATH)

    def init_hackathon_donation_goals(self):
        if os.path.exists(HACKATHON_DONATION_GOALS_EXCEL_FILE_PATH):
            self.hackathon_donation_goals = lt.Table().excel_import(HACKATHON_DONATION_GOALS_EXCEL_FILE_PATH, transforms={'hackathon_id': int, 'donation_goals_id': int})
        else:
            self.hackathon_donation_goals = lt.Table().csv_import(HACKATHON_DONATION_GOALS_CSV_FILE_PATH, transforms={'hackathon_id': int, 'donation_goals_id': int})

    def flush_hackathon_donation_goals(self):
        self.hackathon_donation_goals.excel_export(HACKATHON_DONATION_GOALS_EXCEL_FILE_PATH)

    def init_current_donations(self):
        if os.path.exists(CURRENT_DONATIONS_EXCEL_FILE_PATH):
            self.current_donations = lt.Table().excel_import(CURRENT_DONATIONS_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.current_donations = lt.Table().csv_import(CURRENT_DONATIONS_CSV_FILE_PATH, transforms={'id': int})
    
    def flush_current_donations(self):
        self.current_donations.excel_export(CURRENT_DONATIONS_EXCEL_FILE_PATH)

    def get_next_current_donations_id(self) -> int:
        return max([i for i in self.current_donations.all.id]) + 1

    def init_donation_goals(self):
        if os.path.exists(DONATION_GOALS_EXCEL_FILE_PATH):
            self.donation_goals = lt.Table().excel_import(DONATION_GOALS_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.donation_goals = lt.Table().csv_import(DONATION_GOALS_CSV_FILE_PATH, transforms={'id': int})

    def flush_donation_goals(self):
        self.donation_goals.excel_export(DONATION_GOALS_EXCEL_FILE_PATH)

    def get_next_donation_goals_id(self) -> int:
        return max([i for i in self.donation_goals.all.id]) + 1
    

    def init_problem_statement_hackathons(self):
        if os.path.exists(PROBLEM_STATEMENT_HACKATHONS_EXCEL_FILE_PATH):
            self.problem_statement_hackathons = lt.Table().excel_import(PROBLEM_STATEMENT_HACKATHONS_EXCEL_FILE_PATH, transforms={'hackathon_id': int, 'problem_statement_id': int} )
        else:
            self.problem_statement_hackathons = lt.Table().csv_import(PROBLEM_STATEMENT_HACKATHONS_CSV_FILE_PATH, transforms={'hackathon_id': int, 'problem_statement_id': int})

    def flush_problem_statement_hackathons(self):
        self.problem_statement_hackathons.excel_export(PROBLEM_STATEMENT_HACKATHONS_EXCEL_FILE_PATH)


    def init_nonprofits(self):
        if os.path.exists(NONPROFIT_EXCEL_FILE_PATH):
            self.nonprofits = lt.Table().excel_import(NONPROFIT_EXCEL_FILE_PATH, transforms={'id': int})
        else:
            self.nonprofits = lt.Table().csv_import(NONPROFIT_CSV_FILE_PATH, transforms={'id': int})

        self.nonprofits.create_index('id', unique=True)

    def get_next_nonprofit_id(self) -> int:
        return max([i for i in self.nonprofits.all.id]) + 1
    
    def flush_nonprofits(self):
        self.nonprofits.excel_export(NONPROFIT_EXCEL_FILE_PATH)

DatabaseInterface.register(InMemoryDatabaseInterface)


from common.utils import safe_get_env_var
from model.hackathon import Hackathon
from model.nonprofit import Nonprofit
from model.problem_statement import ProblemStatement
from model.user import User
from model.judge_assignment import JudgeAssignment
from model.judge_score import JudgeScore
from model.judge_panel import JudgePanel

from db.interface import DatabaseInterface

db:DatabaseInterface = None

# TODO: Select db interface based on env
in_memory = safe_get_env_var("IN_MEMORY_DATABASE") == 'True'

if in_memory: 
    print("Using in memory database")
    from db.mem import InMemoryDatabaseInterface
    db = InMemoryDatabaseInterface()
else:
    from db.firestore import FirestoreDatabaseInterface
    db = FirestoreDatabaseInterface()

#Users
def get_db():
    return db.get_db()


def fetch_user_by_user_id(user_id):
    u = db.fetch_user_by_user_id(user_id)
    return u

def fetch_user_by_db_id(id):
    u = db.fetch_user_by_db_id(id)
    return u

def upsert_profile_metadata(user: User):
    return db.upsert_profile_metadata(user)

def update_user(user:User):
    return db.update_user(user)

def insert_user(user:User):
    print('Inserting user')
    return db.insert_user(user)

def get_user_profile_by_db_id(id):
    return db.get_user_profile_by_db_id(id)

def delete_user_by_user_id(user_id):
    return db.delete_user_by_user_id(user_id)

def delete_user_by_db_id(id):
    return db.delete_user_by_db_id(id)

def fetch_users():
    return db.fetch_users()

# Problem Statements
def fetch_problem_statement(id):
    return db.fetch_problem_statement(id)

def fetch_problem_statements():
    return db.fetch_problem_statements()

def insert_problem_statement(problem_statement: ProblemStatement):
    return db.insert_problem_statement(problem_statement)

def update_problem_statement(problem_statement: ProblemStatement):
    return db.update_problem_statement(problem_statement)

def delete_problem_statement(id):
    return db.delete_problem_statement(id)

def insert_helping(problem_statement_id, user: User, mentor_or_hacker, helping_date):
    return db.insert_helping(problem_statement_id, user, mentor_or_hacker, helping_date)

def delete_helping(problem_statement_id, user: User):
    return db.delete_helping(problem_statement_id, user)

# Hackathons

def fetch_hackathon(id):
    return db.fetch_hackathon(id)

def fetch_hackathons():
    return db.fetch_hackathons()

def insert_hackathon(h : Hackathon):
    return db.insert_hackathon(h)

'''
Link problem statement and hackathon
'''
def insert_problem_statement_hackathon(problem_statement: ProblemStatement, hackathon: Hackathon):
    return db.insert_problem_statement_hackathon(problem_statement, hackathon)

def update_problem_statement_hackathons(problem_statement: ProblemStatement, hackathons):
    return db.update_problem_statement_hackathons(problem_statement, hackathons)

# Nonprofits
def fetch_npos():
    return db.fetch_npos()

def fetch_npo(id):
    return db.fetch_npo(id)

def insert_nonprofit(npo: Nonprofit):
    return db.insert_nonprofit(npo)

def update_nonprofit(nonprofit: Nonprofit):
    return db.update_nonprofit(nonprofit)

def delete_nonprofit(nonprofit_id):
    return db.delete_nonprofit(nonprofit_id)

#TODO: Kill with fire. Leaky abstraction
def get_user_doc_reference(user_id):
    return db.get_user_doc_reference(user_id)

# Judge Assignments
def fetch_judge_assignments_by_judge_id(judge_id):
    return db.fetch_judge_assignments_by_judge_id(judge_id)

def fetch_judge_assignments_by_event_and_judge(event_id, judge_id):
    return db.fetch_judge_assignments_by_event_and_judge(event_id, judge_id)

# Get assignment by panel_id, event_id, judge_id, round, and team_id
def fetch_judge_assignment(panel_id, event_id, judge_id, round_name, team_id):
    return db.fetch_judge_assignment(panel_id, event_id, judge_id, round_name, team_id)

def insert_judge_assignment(assignment: JudgeAssignment):
    return db.insert_judge_assignment(assignment)

def update_judge_assignment(assignment: JudgeAssignment):
    return db.update_judge_assignment(assignment)

def delete_judge_assignment(assignment_id):
    return db.delete_judge_assignment(assignment_id)

# Judge Scores
def fetch_judge_score(judge_id, team_id, event_id, round_name, is_draft=False):
    return db.fetch_judge_score(judge_id, team_id, event_id, round_name, is_draft)

def fetch_judge_scores_by_judge_and_event(judge_id, event_id):
    return db.fetch_judge_scores_by_judge_and_event(judge_id, event_id)

def insert_judge_score(score: JudgeScore):
    return db.insert_judge_score(score)

def update_judge_score(score: JudgeScore):
    return db.update_judge_score(score)

def upsert_judge_score(score: JudgeScore):
    return db.upsert_judge_score(score)

# Judge Panels
def fetch_judge_panels_by_event(event_id):
    return db.fetch_judge_panels_by_event(event_id)

def fetch_judge_panel(panel_id):
    return db.fetch_judge_panel(panel_id)

def fetch_judge_assignments_by_panel_id(panel_id):
    return db.fetch_judge_assignments_by_panel_id(panel_id)

def insert_judge_panel(panel: JudgePanel):
    return db.insert_judge_panel(panel)

def update_judge_panel(panel: JudgePanel):
    return db.update_judge_panel(panel)

def delete_judge_panel(panel_id):
    return db.delete_judge_panel(panel_id)

def get_volunteer_from_db_by_user_id_volunteer_type_and_event_id(user_id, volunteer_type, event_id):
    return db.get_volunteer_from_db_by_user_id_volunteer_type_and_event_id(user_id, volunteer_type, event_id)
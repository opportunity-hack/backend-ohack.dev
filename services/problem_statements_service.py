from datetime import datetime
import os
from ratelimit import limits
import requests
from common.utils.slack import send_slack_audit
from model.problem_statement import ProblemStatement
from model.user import User
from db.db import delete_problem_statement, fetch_problem_statement, insert_problem_statement, update_problem_statement
import logging
import pytz
from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey
import uuid

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

#TODO consts file?
ONE_MINUTE = 1*60

@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement(d):
    logger.debug("Problem Statement Save")

    p = ProblemStatement() # Don't use ProblemStatement.deserialize here. We don't have an id yet.
    p.update(d)

    p = insert_problem_statement(p)

    send_slack_audit(action="save_problem_statement",
                     message="Saving", payload=d)

    return p

@limits(calls=50, period=ONE_MINUTE)
def update_problem_statement_fields(d):
    
    problem_statement = None
    if 'id' in d and d['id'] is not None:
        problem_statement = fetch_problem_statement(d['id'])
    
    if problem_statement is not None:
        problem_statement.update(d)
        problem_statement.id = d['id']
        return update_problem_statement(problem_statement)
    else:
        return None
    
def get_problem_statement(id):
    return fetch_problem_statement(id)

def remove_problem_statement(id):
    return delete_problem_statement(id)
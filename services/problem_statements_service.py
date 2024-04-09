from datetime import datetime
import os
from ratelimit import limits
import requests
from common.utils.slack import send_slack_audit
from model.problem_statement import ProblemStatement
from model.user import User
from db.db import delete_user_by_db_id, delete_user_by_user_id, fetch_problem_statement_by_id, fetch_user_by_user_id, fetch_user_by_db_id, insert_problem_statement, insert_user, update_user, get_user_profile_by_db_id, upsert_profile_metadata
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

    insert_problem_statement(ProblemStatement.deserialize(d))

    send_slack_audit(action="save_problem_statement",
                     message="Saving", payload=d)
    
def get_problem_statement(id):
    return fetch_problem_statement_by_id(id)
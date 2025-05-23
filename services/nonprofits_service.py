from datetime import datetime
import os
from ratelimit import limits
import requests
from db.db import delete_nonprofit, fetch_npo, fetch_npos, insert_nonprofit, update_nonprofit
from model.nonprofit import Nonprofit
import pytz
from cachetools import cached, LRUCache, TTLCache
from cachetools.keys import hashkey
from common.log import get_logger, info, debug, warning, error, exception

logger = get_logger("nonprofits_service")

#TODO consts file?
ONE_MINUTE = 1*60

@limits(calls=20, period=ONE_MINUTE)
def get_npos():
    debug(logger, "Get NPOs start")
    
    npos = fetch_npos()
           
    # log result
    debug(logger, "Found NPO results", count=len(npos))
    return npos

def get_npo(id):
    npo = fetch_npo(id)
    return npo

@limits(calls=50, period=ONE_MINUTE)
def save_npo(d):
    debug(logger, "Save NPO", nonprofit=d)

    n = Nonprofit() # Don't use ProblemStatement.deserialize here. We don't have an id yet.
    n.update(d)

    n = insert_nonprofit(n)

    # send_slack_audit(action="save_npo",
    #                 message="Saving", payload=d)

    return n

@limits(calls=50, period=ONE_MINUTE)
def update_npo(d):
    debug(logger, "Update NPO", nonprofit=d)

    n: Nonprofit | None = None

    if 'id' in d and d['id'] is not None:
        n = fetch_npo(d['id'])

    if n is not None:
        n.update(d)

        # send_slack_audit(action="update_npo",
        #                 message="Update", payload=d)

        return update_nonprofit(n)

    else:
        return None
    
def delete_npo(id): 
    n: Nonprofit | None = fetch_npo(id)

    if n is not None:
        n = delete_nonprofit(id)

    return n
    

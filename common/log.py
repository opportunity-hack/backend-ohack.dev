import os
import logging

logger = logging.getLogger("ohack")

log_level = logging.INFO

if 'GLOBAL_LOG_LEVEL' in os.environ:
    if os.environ['GLOBAL_LOG_LEVEL'] == 'debug':
        log_level = logging.DEBUG

def get_log_level():
    return log_level
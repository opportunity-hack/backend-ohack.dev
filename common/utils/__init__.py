from os import environ

# add logger
import logging
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)

def safe_get_env_var(key):
    try:
        return environ[key]
    except KeyError:
        logger.warning(f"Missing {key} environment variable. Setting default to CHANGEMEPLS")
        return "CHANGEMEPLS"
        # ^^ Do this so any ENVs not set in production won't crash the server
        #     
        #raise NameError(f"Missing {key} environment variable.")

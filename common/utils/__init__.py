from os import environ


def safe_get_env_var(key):
    try:
        return environ[key]
    except KeyError:
        print(f"****\n*****\nMissing {key} environment variable. Setting default to CHANGEMEPLS")
        return "CHANGEMEPLS"
        # ^^ Do this so any ENVs not set in production won't crash the server
        #     
        #raise NameError(f"Missing {key} environment variable.")

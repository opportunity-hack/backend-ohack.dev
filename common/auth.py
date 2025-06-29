from common.utils import safe_get_env_var

PROPEL_AUTH_URL = safe_get_env_var("PROPEL_AUTH_URL")
PROPEL_AUTH_KEY = safe_get_env_var("PROPEL_AUTH_KEY")

from propelauth_flask import init_auth, current_user

auth = init_auth(
    auth_url=PROPEL_AUTH_URL,
    api_key=PROPEL_AUTH_KEY
)

auth_user = current_user

def getOrgId(req):
    # Get the org_id from the req
    return req.headers.get("X-Org-Id")
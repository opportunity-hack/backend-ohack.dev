from api.newsletters.newsletter_service import(address, get_subscription_list,add_to_subscription_list,check_subscription_list,remove_from_subscription_list)
from api.newsletters.smtp import(send_newsletters)
import json
import logging
from flask import (
    Blueprint,
)
from api.security.guards import (
    authorization_guard,
    permissions_guard,
    admin_messages_permissions
)


bp_name = 'api-newsletter'
bp_url_prefix = '/api/newsletter'
bp = Blueprint(bp_name, __name__, url_prefix=bp_url_prefix)

logger = logging.getLogger("myapp")


@bp.route("/")
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter():
    return get_subscription_list()


@bp.route("/<user_id>")
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def check_sub(user_id):
    return check_subscription_list(user_id=user_id)

@bp.route("/<subscribe>/<user_id>", methods=["GET"])
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def newsletter_signup(subscribe, user_id):
    if subscribe == "subscribe":
        return add_to_subscription_list(user_id)
    elif subscribe == "verify":
        return check_subscription_list(user_id)
    else:
        return remove_from_subscription_list(user_id)

dicti ={
    "subject": "This is my fancy subject",
    "body" : """
    
    # Portfolio

This portffolio was created with [Angular CLI](https://github.com/angular/angular-cli) version 14.1.0.
Uses ```saas``` 

<details>

<summary>Running the project</summary>

## Development server

Run `ng serve` for a dev server. Navigate to `http://localhost:4200/`. The application will automatically reload if you change any of the source files.

## Code scaffolding

Run `ng generate component component-name` to generate a new component. You can also use `ng generate directive|pipe|service|class|guard|interface|enum|module`.

## Build

Run `ng build` to build the project. The build artifacts will be stored in the `dist/` directory.

## Running unit tests

Run `ng test` to execute the unit tests via [Karma](https://karma-runner.github.io).

## Running end-to-end tests

Run `ng e2e` to execute the end-to-end tests via a platform of your choice. To use this command, you need to first add a package that implements end-to-end testing capabilities.

## Further help

To get more help on the Angular CLI use `ng help` or go check out the [Angular CLI Overview and Command Reference](https://angular.io/cli) page.

</details>
    """,
    "is_html": False
}
jsonObj = json.dumps(dicti)

@bp.route("/send_newsletter")
# @authorization_guard
# @permissions_guard([admin_messages_permissions.read])
def send_newsletter():
    addresses = get_subscription_list()["active"]
    logger.debug(addresses)
    data   = json.loads(jsonObj)
    try:
        send_newsletters(addresses=addresses,message=data["body"],subject=data["subject"],is_html=data["is_html"])
    except  Exception as e:
        logger.debug(f"get_profile_metadata {e}")
        return "some error"
    return "True"
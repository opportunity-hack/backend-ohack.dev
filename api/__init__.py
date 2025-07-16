##########################################
# External Modules
##########################################

from flask import Flask
from flask_cors import CORS
from flask_talisman import Talisman
import logging.config
from readme_metrics import MetricsApiConfig
from readme_metrics.flask_readme import ReadMeMetrics
from common.utils import safe_get_env_var
import os

from common.utils import safe_get_env_var
import os

def grouping_function(request):
  env = safe_get_env_var("FLASK_ENV")
  if True:
        return {
        # User's API Key
        "api_key": f"{env}-readme-api-key",
        # Username to show in ReadMe's dashboard
        "label": env,
        # User's email address
        "email": f"{env}@example.com",
        }
  else:
      return None
  

dict_config = {
    'version': 1,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] {%(pathname)s:%(funcName)s:%(lineno)d} %(levelname)s - %(message)s',
        }
    },
    'handlers': {
        'default': {
        'level': 'DEBUG',
        'formatter': 'default',
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': "test.log",
        'maxBytes': 5000000,
        'backupCount': 10
    },
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'default',
    },
    },
    'loggers': {
        'myapp': {
            'handlers': ["console"],
            'level': 'DEBUG',
        },
    }
}


logger = logging.getLogger("myapp")
logging.config.dictConfig(dict_config)

print("Starting Flask")

def create_app():
    ##########################################
    # Environment Variables
    ##########################################
    client_origin_url = os.getenv("CLIENT_ORIGIN_URL")
    logger.info("Client Origin URL: " + client_origin_url)
    

    ##########################################
    # Flask App Instance
    ##########################################

    app = Flask(__name__, instance_relative_config=True)
    logger.info("Started Flask")



    ##########################################
    # HTTP Security Headers
    ##########################################

    csp = {
        'default-src': ['\'self\'', '\'http://127.0.0.1:6060\''],
        'frame-ancestors': ['\'none\'']
    }

    Talisman(
        app,
        force_https=False,
        frame_options='DENY',
        content_security_policy=csp,
        referrer_policy='no-referrer',
        x_xss_protection=False,
        x_content_type_options=True
    )

    @app.after_request
    def add_headers(response):
        client_origin_url = safe_get_env_var("CLIENT_ORIGIN_URL")                
        if "," in client_origin_url:
            client_origin_url = client_origin_url.split(",")

        if client_origin_url == "*":
            logger.debug(
                "Using wildcard for Access-Control-Allow-Origin - pretty dangerous, just for development purposes only")
            response.headers['Access-Control-Allow-Origin'] = "*"

        response.headers['X-XSS-Protection'] = '0'
        response.headers['Cache-Control'] = 'no-store, max-age=0, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    ##########################################
    # CORS
    ##########################################

    if "," in client_origin_url:
        client_origin_url = client_origin_url.split(",")

    if client_origin_url == "*":
        logger.debug(
            "Using wildcard for CORS client_origin_url - pretty dangerous, just for development purposes only")
        CORS(app)
    else:
        logger.debug(
            f"Using {client_origin_url} for CORS client_origin_url")
        CORS(
            app,
            resources={r"/api/*": {"origins": client_origin_url}},
            allow_headers=["Authorization", "Content-Type", "X-Org-Id"],
            methods=["GET", "POST", "PATCH", "DELETE"],
            max_age=86400
        )
    # Define ReadMe's Metrics middleware
    metrics_extension = ReadMeMetrics(
        MetricsApiConfig(
            api_key=safe_get_env_var("README_API_KEY"),
            grouping_function=grouping_function            
            )
    )

    metrics_extension.init_app(app)


    ##########################################
    # Blueprint Registration
    ##########################################

    from api import exception_views
    from api.messages import messages_views
    from api.newsletters import newsletter_views
    # Leaving this disabled for now - team can fix this based on fixes for above module import
    #from api.newsletters import subscription_views
    from api.certificates import certificate_views
    from api.users import users_views
    from api.hearts import hearts_views
    from api.problemstatements import problem_statement_views
    from api.volunteers import volunteers_views
    from api.validate import validate_views
    from api.teams import teams_views
    from api.contact import contact_views
    from api.leaderboard import leaderboard_views
    from api.github import github_views
    from api.slack import slack_views
    from api.llm import llm_views

    app.register_blueprint(messages_views.bp)
    app.register_blueprint(exception_views.bp)
    app.register_blueprint(newsletter_views.bp)
    app.register_blueprint(certificate_views.bp)
    #app.register_blueprint(subscription_views.bp)
    app.register_blueprint(users_views.bp)
    app.register_blueprint(hearts_views.bp)
    app.register_blueprint(problem_statement_views.bp)
    app.register_blueprint(volunteers_views.bp)
    app.register_blueprint(validate_views.bp)
    app.register_blueprint(teams_views.bp)
    app.register_blueprint(contact_views.bp)
    app.register_blueprint(leaderboard_views.bp)
    app.register_blueprint(github_views.bp)
    app.register_blueprint(slack_views.bp)
    app.register_blueprint(llm_views.bp)

    return app

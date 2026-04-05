##########################################
# External Modules
##########################################

from flask import Flask
from flask_cors import CORS
from flask_talisman import Talisman
import logging.config
import sentry_sdk
from common.utils import safe_get_env_var
import os

try:
    import colorlog
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False

sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        environment=os.getenv("FLASK_ENV", "production"),
    )

# Determine if colored logging should be used
use_colored_logs = (
    HAS_COLORLOG and
    os.environ.get('USE_COLORED_LOGS', 'true').lower() == 'true'
)

# Build formatters dictionary
formatters = {
    'default': {
        'format': '[%(asctime)s] {%(pathname)s:%(funcName)s:%(lineno)d} %(levelname)s - %(message)s',
    }
}

if use_colored_logs:
    formatters['colored'] = {
        '()': 'colorlog.ColoredFormatter',
        'format': '%(log_color)s[%(asctime)s] %(levelname)s%(reset)s - %(message)s',
        'datefmt': '%Y-%m-%d %H:%M:%S',
        'log_colors': {
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    }

dict_config = {
    'version': 1,
    'formatters': formatters,
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
            'formatter': 'colored' if use_colored_logs else 'default',
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
    from api.judging import judging_views
    from api.llm import llm_views
    from api.store import store_views

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
    app.register_blueprint(judging_views.bp)
    app.register_blueprint(store_views.bp)

    return app

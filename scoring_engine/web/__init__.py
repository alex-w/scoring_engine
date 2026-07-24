import logging
import os

from flask import Flask, jsonify, request
from flask_login import LoginManager
from flask_wtf.csrf import CSRFError

from scoring_engine.cache import agent_cache, cache
from scoring_engine.config import config
from scoring_engine.db import db
from scoring_engine.version import version_info
from scoring_engine.web.csrf import csrf

SECRET_KEY = os.urandom(128)


def create_app():
    app = Flask(__name__)

    app.config.update(DEBUG=config.debug)
    app.config.update(UPLOAD_FOLDER=config.upload_folder)
    app.secret_key = SECRET_KEY

    # Session/remember-me cookie hardening.
    #   HttpOnly  - the session cookie is never readable from JavaScript.
    #   SameSite  - "Lax" still allows top-level GET navigation into the app
    #               (e.g. following a link to the scoreboard) but keeps the
    #               cookie off cross-site POSTs.
    #   Secure    - driven by config.  The shipped docker deployment puts nginx
    #               in front (port 80 301-redirects to 443), so Secure is
    #               correct there, but a plain-HTTP dev run must be able to turn
    #               it off or the browser will silently drop the cookie.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.session_cookie_secure
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_SECURE"] = config.session_cookie_secure

    # CSRF tokens are bound to the session, so let the session lifetime govern
    # instead of expiring tokens after an hour.  Admin pages are routinely left
    # open for the length of a competition; a time limit would turn every
    # long-lived tab into a stream of 400s.
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # Static file caching: 1 hour in debug mode, 1 week in production
    # Browsers will cache CSS/JS/images and not re-request until max-age expires
    if config.debug:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 3600  # 1 hour
    else:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 604800  # 1 week

    # Configure Flask-SQLAlchemy
    app.config["SQLALCHEMY_DATABASE_URI"] = config.db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,  # Verify connections before using them
        "pool_recycle": 3600,  # Recycle connections after 1 hour
    }

    # Initialize Flask-SQLAlchemy with the app
    db.init_app(app)

    if not config.debug:
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

    from scoring_engine.web.views import (
        about,
        admin,
        api,
        auth,
        flags,
        injects,
        notifications,
        overview,
        profile,
        scoreboard,
        services,
        stats,
        welcome,
        announcements,
    )

    cache.init_app(app)
    agent_cache.init_app(app)

    # Enable CSRF protection on every state-changing request.  The browser
    # front end supplies the token either as a hidden ``csrf_token`` form field
    # or as an ``X-CSRFToken`` header (see ``templates/base.html``).
    #
    # /api/agent/checkin is deliberately exempt: black team agents post an
    # AES-GCM sealed payload with no session cookie at all, so there is no
    # cookie for an attacker to ride and no way for the agent to obtain a
    # token.  The exemption is declared with @csrf.exempt in
    # scoring_engine/web/views/api/agent.py, next to the endpoint itself.
    #
    # WTF_CSRF_SSL_STRICT is left at its default (on), which additionally
    # requires a same-origin Referer header once the request is HTTPS.  If a
    # deployment ever fronts this with something that strips Referer, that is
    # the knob to turn off -- not the CSRF protection itself.
    csrf.init_app(app)

    # Initialize login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"
    login_manager.session_protection = "strong"

    # Register the user_loader function after initializing login_manager
    from scoring_engine.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    app.register_blueprint(welcome.mod)
    app.register_blueprint(services.mod)
    app.register_blueprint(stats.mod)
    app.register_blueprint(scoreboard.mod)
    app.register_blueprint(profile.mod)
    app.register_blueprint(overview.mod)
    app.register_blueprint(notifications.mod)
    app.register_blueprint(injects.mod)
    app.register_blueprint(auth.mod)
    app.register_blueprint(flags.mod)
    app.register_blueprint(api.mod)
    app.register_blueprint(admin.mod)
    app.register_blueprint(about.mod)
    app.register_blueprint(announcements.mod)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """Return a JSON body for the API, plain text elsewhere.

        Almost every state-changing route in this app is an XHR against
        /api/..., and those callers parse JSON.  Returning the stock HTML error
        page there produces a confusing "unexpected token <" in the console.
        """
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "error": "CSRF validation failed", "reason": error.description}), 400
        return error.description, 400

    @app.context_processor
    def inject_version():
        return {'version_info': version_info}

    return app

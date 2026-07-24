import logging
import secrets

from flask import Flask
from flask_login import LoginManager

from scoring_engine.cache import agent_cache, cache
from scoring_engine.config import config
from scoring_engine.db import db
from scoring_engine.logger import logger
from scoring_engine.version import version_info

# Number of bytes of entropy used for the throwaway key generated when no
# secret_key has been configured.  token_hex returns twice this many chars.
GENERATED_SECRET_KEY_BYTES = 64

# How an operator should produce a key.  We print the *command*, never a live
# key: logs are widely readable and routinely shipped to aggregators, so a
# generated secret emitted to stdout should be considered burned the moment it
# is printed.  Generating it locally keeps it out of the log stream entirely.
SECRET_KEY_GENERATION_HINT = 'Generate one with: python -c "import secrets; print(secrets.token_hex(64))"'

SECRET_KEY_LOCATION_HINT = (
    "Then set SCORINGENGINE_SECRET_KEY in your .env file (docker) or 'secret_key' in "
    "engine.conf (manual install). Do not paste it into a shell command or a log."
)

MISSING_SECRET_KEY_WARNING = (
    "No secret_key is configured, so a random Flask session key was generated for this "
    "process. Sessions will NOT survive a restart (every user is logged out) and will NOT "
    "work across more than one web process or container, which blocks horizontal scaling. "
    "Set a long, random, stable value via the SCORINGENGINE_SECRET_KEY environment "
    "variable or 'secret_key' in engine.conf. " + SECRET_KEY_GENERATION_HINT
)

# Shown by bin/setup, which runs before the web app and is the natural place to
# catch this while there is still time to fix it.
MISSING_SECRET_KEY_SETUP_WARNING = (
    "No secret_key is configured. Until one is set, every web restart logs all users out "
    "and sessions cannot be shared between web processes/containers. " + SECRET_KEY_GENERATION_HINT
)


def secret_key_is_configured():
    """Return whether a usable ``secret_key`` is present in the configuration.

    Used by ``bin/setup`` so the check and the web app agree on what counts as
    configured (a whitespace-only value does not).
    """
    return bool((getattr(config, "secret_key", "") or "").strip())


def get_secret_key():
    """Return the Flask session signing key.

    The value comes from the normal configuration precedence implemented in
    :mod:`scoring_engine.config_loader` -- ``SCORINGENGINE_SECRET_KEY`` in the
    environment, then ``secret_key`` in ``engine.conf``.

    When nothing is configured the application still boots: a random key is
    generated and a loud warning is logged.  No hardcoded fallback key is
    shipped on purpose -- a fixed default would let anyone forge session
    cookies for any deployment that never overrode it.
    """
    if secret_key_is_configured():
        return config.secret_key.strip()

    logger.warning(MISSING_SECRET_KEY_WARNING)
    return secrets.token_hex(GENERATED_SECRET_KEY_BYTES)


def create_app():
    app = Flask(__name__)

    app.config.update(DEBUG=config.debug)
    app.config.update(UPLOAD_FOLDER=config.upload_folder)
    app.secret_key = get_secret_key()

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
        health,
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
    app.register_blueprint(health.mod)

    @app.context_processor
    def inject_version():
        return {'version_info': version_info}

    return app

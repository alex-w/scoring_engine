"""
Shared pytest fixtures for scoring_engine tests.

Phase 1: Shared app + fast table cleanup instead of drop/create.
  - Session-scoped ``app`` and ``_db_tables`` create the Flask app and DB
    tables once per xdist worker.
  - Function-scoped ``db_session`` (autouse) deletes all rows from every table
    after each test and re-inserts the default settings.  This is much faster
    than drop_all / create_all because DDL (CREATE TABLE, DROP TABLE) is the
    expensive part with SQLite.
  - ``UnitTest.setup_method()`` detects the active context and becomes a no-op.
"""

import pytest

from scoring_engine.db import db
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.models.user import User

# ---------------------------------------------------------------------------
# Phase 1 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    """Create the Flask application once per xdist worker."""
    from scoring_engine.web import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture(scope="session")
def _db_tables(app):
    """Create all tables once, drop at end of session."""
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def _insert_default_settings():
    """Insert the 22 default Setting rows every test expects."""
    settings = [
        ("about_page_content", "example content value"),
        ("welcome_page_content", "example welcome content <br>here"),
        ("target_round_time", 60),
        ("worker_refresh_time", 30),
        ("engine_paused", False),
        ("pause_duration", 30),
        ("blue_team_update_hostname", True),
        ("blue_team_update_port", True),
        ("blue_team_update_account_usernames", True),
        ("blue_team_update_account_passwords", True),
        ("blue_team_view_check_output", True),
        ("anonymize_team_names", False),
        ("overview_show_round_info", True),
        ("sla_enabled", False),
        ("sla_penalty_threshold", "5"),
        ("sla_penalty_percent", "10"),
        ("sla_penalty_max_percent", "50"),
        ("sla_penalty_mode", "additive"),
        ("sla_allow_negative", False),
        ("dynamic_scoring_enabled", False),
        ("dynamic_scoring_early_rounds", "10"),
        ("dynamic_scoring_early_multiplier", "2.0"),
        ("dynamic_scoring_late_start_round", "50"),
        ("dynamic_scoring_late_multiplier", "0.5"),
        ("inject_scores_visible", False),
    ]
    for name, value in settings:
        db.session.add(Setting(name=name, value=value))
    db.session.commit()


@pytest.fixture(autouse=True)
def db_session(app, _db_tables):
    """Provide a clean DB for every test.

    Inserts default settings before the test, then deletes all rows from
    every table after the test completes.  Much cheaper than drop_all /
    create_all because there's no DDL overhead.
    """
    with app.app_context():
        _insert_default_settings()
        yield db.session
        # Clean up: delete all rows from every table, respecting FK order.
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture(autouse=True)
def _materialize_scores_on_read(db_session):
    """Keep round_score in sync for the score-read helpers, in tests.

    In production the engine materializes round_score at round close (see
    scoring_engine.scores.materialize_round), so by the time anything reads a score
    the rows exist. Unit tests, however, create Check rows directly without running
    the engine. Rather than sprinkle materialize calls through every test that
    builds checks, we wrap the three round_score read helpers so they refresh
    round_score from the current checks first. materialize_all_rounds is idempotent
    and cheap on test-sized data.

    This affects only the read path's data freshness -- the write path itself
    (materialize_round at round close, failed-round cleanup, rollback) is exercised
    directly by tests/scoring_engine/test_scores.py and the engine tests.

    Patches manually (save/restore) rather than via pytest's ``monkeypatch``: some
    engine tests install a flaky ``db.session.commit`` through their own
    ``monkeypatch``, and depending on it here would reorder that fixture's teardown
    to after the db_session cleanup commit, tripping the flaky commit at teardown.
    """
    import scoring_engine.scores as scores

    names = ("team_service_score", "team_service_scores", "team_dynamic_service_score")
    originals = {name: getattr(scores, name) for name in names}

    def make_wrapper(orig):
        def wrapper(session, *args, **kwargs):
            scores.materialize_all_rounds(session)
            return orig(session, *args, **kwargs)

        return wrapper

    for name, original in originals.items():
        setattr(scores, name, make_wrapper(original))
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(scores, name, original)


# ---------------------------------------------------------------------------
# Phase 3: Shared auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client(app, db_session):
    """Flask test client with CSRF disabled."""
    return app.test_client()


@pytest.fixture()
def three_teams(db_session):
    """Create White/Blue/Red teams each with one user.

    Returns a dict with keys: white_team, blue_team, red_team,
    white_user, blue_user, red_user.
    """
    white_team = Team(name="White Team", color="White")
    blue_team = Team(name="Blue Team", color="Blue")
    red_team = Team(name="Red Team", color="Red")
    db.session.add_all([white_team, blue_team, red_team])
    db.session.flush()

    white_user = User(username="whiteuser", password="testpass", team=white_team)
    blue_user = User(username="blueuser", password="testpass", team=blue_team)
    red_user = User(username="reduser", password="testpass", team=red_team)
    db.session.add_all([white_user, blue_user, red_user])
    db.session.commit()

    return {
        "white_team": white_team,
        "blue_team": blue_team,
        "red_team": red_team,
        "white_user": white_user,
        "blue_user": blue_user,
        "red_user": red_user,
    }


def _login(client, username, password="testpass"):
    """Helper: POST to /login with the given credentials."""
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture()
def white_login(test_client, three_teams):
    """Log in as the white-team user. Returns (client, teams_dict)."""
    _login(test_client, "whiteuser")
    return test_client, three_teams


@pytest.fixture()
def blue_login(test_client, three_teams):
    """Log in as the blue-team user. Returns (client, teams_dict)."""
    _login(test_client, "blueuser")
    return test_client, three_teams


@pytest.fixture()
def red_login(test_client, three_teams):
    """Log in as the red-team user. Returns (client, teams_dict)."""
    _login(test_client, "reduser")
    return test_client, three_teams

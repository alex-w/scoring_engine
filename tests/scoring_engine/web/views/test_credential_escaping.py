"""Regression tests: credentials and team names are stored verbatim, never HTML-escaped.

Historically ``/api/profile/update_password``, ``/api/admin/update_password``,
``/api/admin/add_user`` and ``/api/admin/add_team`` ran ``html.escape()`` over the
submitted username / password / team name before hashing or storing them.
``/login`` compares the raw submitted string and the agent check-in endpoint hashes
the raw team name, so any value containing ``& < > " '`` was mangled on write: the
account was locked out immediately and the team's agents could no longer check in.

Escaping belongs at render time instead. What these tests cover:

* the write side -- the stored bytes equal the submitted bytes, and the round trip
  back through ``/login`` succeeds (``TestPasswordsAreNotEscapedOnWrite``,
  ``TestTeamNamesAreNotEscapedOnWrite``);
* the render side -- the *bytes* of the rendered HTTP responses never contain the
  raw ``<script>`` / ``<img onerror=...>`` / quote payloads, only their escaped
  forms (``TestUsernamesRenderEscaped``, ``TestTeamNamesRenderEscaped``,
  ``TestInjectCommentsRenderEscaped``).

The render-side assertions are what make the claim "removing the write-time escape
did not introduce stored XSS" true.  Server-rendered pages are checked directly on
``resp.data``. Pages that build markup in JavaScript from a JSON API are checked in
two halves, because a Flask test client cannot execute the JS: the API payload is
asserted to carry the value verbatim (proving nothing escapes on the server), and
the template source is asserted to route that exact field through
``ScoringEngineUtils.escapeHtml`` (proving the client escapes it), with a companion
assertion that no *raw* interpolation of the field survives anywhere in the file.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from scoring_engine.db import db
from scoring_engine.models.inject import Inject, InjectComment, Template
from scoring_engine.models.team import Team
from scoring_engine.models.user import User

# Every character html.escape() would rewrite.
TRICKY_PASSWORD = "p<a>&\"b'c"

# Payloads that must never reach a browser unescaped, mapped to the exact substring
# a browser needs verbatim for the injection to fire. Checking the primitive as well
# as the whole payload catches *partial* escaping (e.g. only ">" rewritten), which a
# plain "payload not in body" assertion would miss. Every page legitimately contains
# "<script>", so the primitives are chosen to be unique to the payload.
XSS_SCRIPT = "<script>alert('xss')</script>"
XSS_IMG = "<img src=x onerror=alert('xss')>"
XSS_QUOTES = "\"'><svg onload=alert('xss')>"
XSS_PRIMITIVES = {
    XSS_SCRIPT: "<script>alert(",
    XSS_IMG: "<img src=x onerror=",
    XSS_QUOTES: "<svg onload=",
}
XSS_PAYLOADS = tuple(XSS_PRIMITIVES)


# Both escapers in play produce different numeric references for the quotes
# (markupsafe emits &#34;/&#39;, ScoringEngineUtils.escapeHtml emits &quot;/&#39;).
# Normalising them lets one expectation cover both.
_QUOTE_ENTITIES = {"&#34;": '"', "&quot;": '"', "&#39;": "'", "&#x27;": "'"}


def _normalise_quotes(body):
    for entity, char in _QUOTE_ENTITIES.items():
        body = body.replace(entity, char)
    return body


def assert_inert(body, payload):
    """Assert ``payload`` is present in ``body`` but only in escaped form.

    ``body`` is the decoded bytes of an HTTP response. A browser only executes the
    payload if the literal ``<tag`` sequences survive, so assert on those exact
    bytes rather than merely "no exception was raised".
    """
    assert payload not in body, f"unescaped payload rendered verbatim: {payload!r}"
    primitive = XSS_PRIMITIVES[payload]
    assert primitive not in body, f"partially escaped payload: {primitive!r} reached the response body"
    escaped = payload.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    assert escaped in _normalise_quotes(body), f"escaped form of {payload!r} missing from response"


def template_source(app, name):
    return app.jinja_env.loader.get_source(app.jinja_env, name)[0]


def assert_no_raw_interpolation(source, expression):
    """Assert ``expression`` is never concatenated into an HTML string unescaped.

    Catches ``'...' + value.text + '...'`` style interpolation, which is exactly
    the pattern that made auto-generated inject comments a stored-XSS sink.
    """
    raw = re.compile(r"\+\s*" + re.escape(expression) + r"\s*[+;]")
    assert not raw.search(source), f"raw (unescaped) interpolation of {expression!r} found in template"


class TestPasswordsAreNotEscapedOnWrite:
    """Passwords set through the web UI must survive round-tripping to /login."""

    @pytest.fixture(autouse=True)
    def setup(self, test_client, three_teams):
        self.client = test_client
        self.teams = three_teams
        self.blue_user = three_teams["blue_user"]
        self.blue_team = three_teams["blue_team"]

    def login(self, username, password="testpass"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def logout(self):
        return self.client.get("/logout", follow_redirects=True)

    def test_profile_update_password_with_html_chars_allows_login(self):
        self.login("blueuser")
        resp = self.client.post(
            "/api/profile/update_password",
            data={
                "user_id": str(self.blue_user.id),
                "currentpassword": "testpass",
                "password": TRICKY_PASSWORD,
                "confirmedpassword": TRICKY_PASSWORD,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(self.blue_user)
        # The raw password must verify, and the escaped form must NOT.
        assert self.blue_user.check_password(TRICKY_PASSWORD)
        assert not self.blue_user.check_password("p&lt;a&gt;&amp;&quot;b&#x27;c")

        self.logout()
        resp = self.login("blueuser", TRICKY_PASSWORD)
        assert resp.status_code == 200
        assert b"Invalid username or password" not in resp.data

    def test_admin_update_password_with_html_chars_allows_login(self):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/update_password",
            data={"user_id": self.blue_user.id, "password": TRICKY_PASSWORD},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(self.blue_user)
        assert self.blue_user.check_password(TRICKY_PASSWORD)
        assert not self.blue_user.check_password("p&lt;a&gt;&amp;&quot;b&#x27;c")

        self.logout()
        resp = self.login("blueuser", TRICKY_PASSWORD)
        assert resp.status_code == 200
        assert b"Invalid username or password" not in resp.data

    def test_admin_add_user_password_with_html_chars_allows_login(self):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_user",
            data={
                "username": "trickypass",
                "password": TRICKY_PASSWORD,
                "team_id": self.blue_team.id,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        user = db.session.query(User).filter_by(username="trickypass").one()
        assert user.check_password(TRICKY_PASSWORD)

        self.logout()
        resp = self.login("trickypass", TRICKY_PASSWORD)
        assert resp.status_code == 200
        assert b"Invalid username or password" not in resp.data

    def test_admin_add_user_apostrophe_username_allows_login(self):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_user",
            data={"username": "o'brien", "password": "testpass", "team_id": self.blue_team.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Stored verbatim, not as o&#x27;brien
        user = db.session.query(User).filter_by(username="o'brien").one()
        assert user.username == "o'brien"
        assert db.session.query(User).filter_by(username="o&#x27;brien").first() is None

        self.logout()
        resp = self.login("o'brien", "testpass")
        assert resp.status_code == 200
        assert b"Invalid username or password" not in resp.data

    def test_admin_created_user_matches_yaml_provisioned_user(self):
        """competition.py / bin/setup store credentials raw; the admin API must match."""
        # This is exactly what Competition.populate_db() does for YAML users.
        db.session.add(User(username="via_yaml", password=TRICKY_PASSWORD, team=self.blue_team))
        db.session.commit()

        self.login("whiteuser")
        self.client.post(
            "/api/admin/add_user",
            data={
                "username": "via_admin",
                "password": TRICKY_PASSWORD,
                "team_id": self.blue_team.id,
            },
            follow_redirects=True,
        )
        self.logout()

        for username in ("via_admin", "via_yaml"):
            user = db.session.query(User).filter_by(username=username).one()
            assert user.check_password(TRICKY_PASSWORD)
            resp = self.login(username, TRICKY_PASSWORD)
            assert resp.status_code == 200
            assert b"Invalid username or password" not in resp.data
            self.logout()


class TestTeamNamesAreNotEscapedOnWrite:
    """Team names created through the admin API must match YAML-provisioned ones.

    ``/api/agent/checkin`` derives its AES key from ``sha256(team_name + psk)`` using
    the name the agent was configured with, then looks the team up by exact name --
    so an HTML-escaped stored name silently breaks every agent on that team.
    """

    TRICKY_TEAM = "Team <1> & \"Co\""

    @pytest.fixture(autouse=True)
    def setup(self, test_client, three_teams):
        self.client = test_client
        self.teams = three_teams

    def login(self, username, password="testpass"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def test_add_team_stores_name_verbatim(self):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_team",
            data={"name": self.TRICKY_TEAM, "color": "Blue"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        team = db.session.query(Team).filter_by(name=self.TRICKY_TEAM).one()
        assert team.name == self.TRICKY_TEAM
        # The escaped spelling must not exist -- that was the old behaviour.
        assert db.session.query(Team).filter_by(name="Team &lt;1&gt; &amp; &quot;Co&quot;").first() is None

    def test_add_team_matches_yaml_provisioned_team(self):
        """competition.py stores team names raw; the admin API must agree byte for byte."""
        db.session.add(Team(name="Squad <1>", color="Blue"))
        db.session.commit()

        self.login("whiteuser")
        self.client.post(
            "/api/admin/add_team",
            data={"name": "Crew <1>", "color": "Blue"},
            follow_redirects=True,
        )

        yaml_team = db.session.query(Team).filter_by(name="Squad <1>").one()
        admin_team = db.session.query(Team).filter_by(name="Crew <1>").one()
        assert yaml_team.name[len("Squad") :] == admin_team.name[len("Crew") :] == " <1>"

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_add_team_stores_xss_payload_verbatim(self, payload):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_team",
            data={"name": payload, "color": "Blue"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert db.session.query(Team).filter_by(name=payload).one().name == payload


class TestUsernamesRenderEscaped:
    """Removing the write-time escape must not turn usernames into stored XSS."""

    @pytest.fixture(autouse=True)
    def setup(self, app, test_client, three_teams):
        self.app = app
        self.client = test_client
        self.teams = three_teams
        self.blue_team = three_teams["blue_team"]

    def login(self, username, password="testpass"):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def logout(self):
        return self.client.get("/logout", follow_redirects=True)

    def add_user(self, username):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_user",
            data={"username": username, "password": "testpass", "team_id": self.blue_team.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        self.logout()
        return db.session.query(User).filter_by(username=username).one()

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_username_renders_inert_on_profile_page(self, payload):
        """/profile is server-rendered, so assert directly on the response bytes."""
        user = self.add_user(payload)
        assert user.username == payload  # stored verbatim

        self.login(payload)
        resp = self.client.get("/profile")
        assert resp.status_code == 200
        assert_inert(resp.data.decode(), payload)

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_manage_page_api_returns_username_verbatim(self, payload):
        """/api/admin/get_teams must not escape -- admin/manage.html escapes on render."""
        self.add_user(payload)
        self.login("whiteuser")
        resp = self.client.get("/api/admin/get_teams")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        usernames = [name for team in body["data"] for name in team["users"]]
        assert payload in usernames, "the API must hand back the stored bytes, not an escaped copy"
        assert "&lt;" not in resp.data.decode(), "the API must not pre-escape (that double-escapes)"

    def test_manage_page_escapes_every_username_field(self):
        """The manage page builds user rows as an HTML string in JS -- it must escape."""
        source = template_source(self.app, "admin/manage.html")
        assert "escapeHtml(key)" in source
        assert "escapeHtml(data.users[key][0])" in source
        assert "escapeHtml(data.users[key][1])" in source
        for expression in ("key", "data.users[key][0]", "data.users[key][1]"):
            assert_no_raw_interpolation(source, expression)

    def test_shared_escape_helpers_are_exported(self):
        resp = self.client.get("/static/vendor/js/utils.js")
        assert resp.status_code == 200
        body = resp.data.decode()
        for name in ("escapeHtml", "escapeJsString", "dtEscape"):
            assert "function " + name in body
            assert name + ": " + name in body

    def test_escape_helper_covers_every_dangerous_character(self):
        """The JS helper must rewrite all five characters html.escape() would."""
        body = self.client.get("/static/vendor/js/utils.js").data.decode()
        helper = body[body.index("function escapeHtml") : body.index("function escapeJsString")]
        for pattern, replacement in (
            ("/&/g", "&amp;"),
            ("/</g", "&lt;"),
            ("/>/g", "&gt;"),
            ('/"/g', "&quot;"),
            ("/'/g", "&#39;"),
        ):
            assert pattern in helper, "escapeHtml does not handle " + pattern
            assert replacement in helper


class TestTeamNamesRenderEscaped:
    """Team names are stored verbatim now, so every render site must escape them."""

    @pytest.fixture(autouse=True)
    def setup(self, app, test_client, three_teams):
        self.app = app
        self.client = test_client
        self.teams = three_teams

    @pytest.mark.parametrize(
        "template,expression",
        [
            ("inject.html", "value.team"),
            ("admin/inject.html", "value.team"),
            ("admin/sla.html", "team.team_name"),
            ("admin/announcements.html", "team.name"),
            ("overview.html", "col.title"),
            ("scoreboard.html", "params[0].name"),
        ],
    )
    def test_team_name_sinks_escape(self, template, expression):
        source = template_source(self.app, template)
        assert "escapeHtml(" + expression + ")" in source, "missing escape for " + expression
        assert_no_raw_interpolation(source, expression)

    @pytest.mark.parametrize("template", ["admin/manage.html", "flags.html", "stats.html"])
    def test_datatables_team_columns_use_escaping_renderer(self, template):
        """DataTables writes cells with innerHTML, so team columns need dtEscape()."""
        source = template_source(self.app, template)
        assert "ScoringEngineUtils.dtEscape()" in source
        # The un-rendered column definitions must be gone.
        assert '{ "data": "name" }' not in source
        assert "{ data: 'team' }" not in source

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_admin_manage_page_serves_team_name_inertly(self, payload):
        """The Add-User dropdown is server-rendered from the team list."""
        db.session.add(Team(name=payload, color="Blue"))
        db.session.commit()

        self.client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            follow_redirects=True,
        )
        resp = self.client.get("/admin/manage")
        assert resp.status_code == 200
        assert_inert(resp.data.decode(), payload)

    @pytest.mark.parametrize(
        "template,expression",
        [
            ("services.html", "data.service_name"),
            ("service.html", "key"),
            ("overview.html", "value"),
            ("stats.html", "svc"),
        ],
    )
    def test_service_name_sinks_escape(self, template, expression):
        """Service names come from the competition YAML and are stored verbatim too."""
        source = template_source(self.app, template)
        assert "escapeHtml(" + expression + ")" in source, "missing escape for " + expression

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_get_teams_api_returns_team_name_verbatim(self, payload):
        db.session.add(Team(name=payload, color="Blue"))
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            follow_redirects=True,
        )
        resp = self.client.get("/api/admin/get_teams")
        assert resp.status_code == 200
        assert payload in [team["name"] for team in json.loads(resp.data)["data"]]


class TestInjectCommentsRenderEscaped:
    """Auto-generated inject comments embed the (verbatim) username in their body.

    The fix belongs at the sink, not the source: comment bodies are also free text
    typed by blue team, so the render site has to escape regardless of where the
    username came from. Escaping at the source instead would double-escape once the
    sink escapes, and would leave the audit trail recording a username
    ("Inject submitted by &lt;script&gt;...") that no user actually has.
    """

    @pytest.fixture(autouse=True)
    def setup(self, app, test_client, three_teams):
        self.app = app
        self.client = test_client
        self.teams = three_teams

    def make_inject(self, team):
        now = datetime.now(timezone.utc)
        template = Template(
            title="Test Inject",
            scenario="scenario",
            deliverable="deliverable",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        inject = Inject(team, template)
        db.session.add(template)
        db.session.add(inject)
        db.session.commit()
        return inject

    @pytest.mark.parametrize("template", ["inject.html", "admin/inject.html"])
    def test_comment_body_is_escaped(self, template):
        """This is the gap the write-time fix left open: value.text went in raw."""
        source = template_source(self.app, template)
        assert "escapeHtml(value.text)" in source
        assert_no_raw_interpolation(source, "value.text")

    @pytest.mark.parametrize("template", ["inject.html", "admin/inject.html"])
    def test_comment_author_and_team_are_escaped(self, template):
        source = template_source(self.app, template)
        for expression in ("value.user", "value.team", "value.added"):
            assert "escapeHtml(" + expression + ")" in source
            assert_no_raw_interpolation(source, expression)

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_auto_generated_comment_carries_username_verbatim(self, payload):
        """Submitting an inject writes 'Inject submitted by <username>.' as the body."""
        blue_team = self.teams["blue_team"]
        db.session.add(User(username=payload, password="testpass", team=blue_team))
        db.session.commit()
        inject = self.make_inject(blue_team)

        self.client.post(
            "/login",
            data={"username": payload, "password": "testpass"},
            follow_redirects=True,
        )
        resp = self.client.post("/api/inject/" + str(inject.id) + "/submit")
        assert resp.status_code == 200

        comment = db.session.query(InjectComment).filter_by(inject_id=inject.id).one()
        # Stored verbatim -- the audit trail records the real username...
        assert comment.content == "Inject submitted by " + payload + "."

        # ...and the API hands it back verbatim, as JSON data rather than markup.
        resp = self.client.get("/api/inject/" + str(inject.id) + "/comments")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["data"][0]["text"] == "Inject submitted by " + payload + "."
        assert body["data"][0]["user"] == payload

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_blue_team_comment_text_round_trips_verbatim(self, payload):
        blue_team = self.teams["blue_team"]
        inject = self.make_inject(blue_team)
        self.client.post(
            "/login",
            data={"username": "blueuser", "password": "testpass"},
            follow_redirects=True,
        )
        resp = self.client.post(
            "/api/inject/" + str(inject.id) + "/comment",
            data=json.dumps({"comment": payload}),
            content_type="application/json",
        )
        assert resp.status_code == 200

        resp = self.client.get("/api/inject/" + str(inject.id) + "/comments")
        assert json.loads(resp.data)["data"][0]["text"] == payload

    @pytest.mark.parametrize("template", ["inject.html", "admin/inject.html"])
    def test_no_field_on_the_inject_payloads_is_interpolated_raw(self, template):
        """Sweep of every user/white-team controlled field the inject pages render."""
        source = template_source(self.app, template)
        for expression in (
            "value.text",
            "value.user",
            "value.team",
            "value.added",
            "value.name",
            "value.title",
            "item.title",
            "item.description",
            "r.category",
            "filename",
        ):
            assert_no_raw_interpolation(source, expression)

"""Regression tests: credentials must be stored verbatim, never HTML-escaped.

Historically ``/api/profile/update_password``, ``/api/admin/update_password`` and
``/api/admin/add_user`` ran ``html.escape()`` over the submitted username and
password before hashing/storing them.  ``/login`` compares the raw submitted
string, so any credential containing ``& < > " '`` was mangled on write and the
account was locked out immediately.

Escaping belongs at render time -- Jinja autoescapes, and the few places that build
markup as a JS string go through ``ScoringEngineUtils.escapeHtml`` -- so these tests
also assert that removing the write-time escape did not introduce stored XSS.
"""

import pytest

from scoring_engine.db import db
from scoring_engine.models.user import User

# Every character html.escape() would rewrite.
TRICKY_PASSWORD = "p<a>&\"b'c"


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


class TestUsernamesRenderEscaped:
    """Removing the write-time escape must not turn usernames into stored XSS."""

    XSS_USERNAME = "<script>alert(1)</script>"

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

    def test_username_with_markup_renders_escaped(self):
        self.login("whiteuser")
        resp = self.client.post(
            "/api/admin/add_user",
            data={"username": self.XSS_USERNAME, "password": "testpass", "team_id": self.blue_team.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        # Stored verbatim...
        user = db.session.query(User).filter_by(username=self.XSS_USERNAME).one()
        assert user.username == self.XSS_USERNAME

        # ...but Jinja autoescaping neutralises it on every page that renders it.
        self.logout()
        resp = self.login(self.XSS_USERNAME, "testpass")
        assert resp.status_code == 200

        resp = self.client.get("/profile")
        assert resp.status_code == 200
        assert self.XSS_USERNAME.encode() not in resp.data
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in resp.data

    def test_admin_manage_page_escapes_usernames_in_js(self):
        """The manage page builds user rows as an HTML string in JS -- it must escape."""
        self.login("whiteuser")
        resp = self.client.get("/admin/manage")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "ScoringEngineUtils.escapeHtml" in body
        assert "escapeHtml(key)" in body

    def test_shared_escape_helper_is_exported(self):
        """ScoringEngineUtils.escapeHtml backs every JS-built username render site."""
        resp = self.client.get("/static/vendor/js/utils.js")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "function escapeHtml" in body
        assert "escapeHtml: escapeHtml" in body

    @pytest.mark.parametrize("template", ["inject.html", "admin/inject.html"])
    def test_inject_comment_templates_escape_usernames(self, template):
        """Inject comments interpolate the commenter's username into an HTML string."""
        source = self.app.jinja_env.loader.get_source(self.app.jinja_env, template)[0]
        assert "ScoringEngineUtils.escapeHtml(value.user)" in source
        assert "+ value.user +" not in source

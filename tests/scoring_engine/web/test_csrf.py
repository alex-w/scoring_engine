"""CSRF protection and session cookie hardening.

The rest of the suite runs with ``WTF_CSRF_ENABLED = False`` (see
``tests/scoring_engine/conftest.py``) so that existing tests can POST without
plumbing a token through every call.  These tests deliberately flip it back on
for the duration of a single test so that the protection itself is exercised.
"""

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from scoring_engine.config import config
from scoring_engine.db import db
from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform, Solve
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.web.csrf import csrf
from scoring_engine.web.views.api.agent import BtaPayloadEncryption

CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "scoring_engine",
    "web",
    "templates",
)


def _all_templates():
    for dirpath, _, filenames in os.walk(TEMPLATE_DIR):
        for filename in sorted(filenames):
            if filename.endswith(".html"):
                path = os.path.join(dirpath, filename)
                with open(path) as handle:
                    yield os.path.relpath(path, TEMPLATE_DIR), handle.read()


def _token_from(response):
    """Pull the CSRF token out of the meta tag base.html renders."""
    match = CSRF_META_RE.search(response.data.decode())
    assert match is not None, "base.html did not render a csrf-token meta tag"
    return match.group(1)


@pytest.fixture()
def csrf_enabled(app):
    """Turn real CSRF protection back on for the duration of one test."""
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield app
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture()
def csrf_client(csrf_enabled, three_teams):
    """A CSRF-protected test client, logged in as the white team user.

    Returns ``(client, token)`` where ``token`` is a CSRF token valid for the
    client's session.  Logging in already exercises the form-field path.
    """
    client = csrf_enabled.test_client()
    login_page = client.get("/login")
    client.post(
        "/login",
        data={
            "username": "whiteuser",
            "password": "testpass",
            "csrf_token": _token_from(login_page),
        },
        follow_redirects=True,
    )
    # Grab a fresh token bound to the post-login session.
    return client, _token_from(client.get("/admin/manage"))


class TestCSRFConfiguration:
    def test_csrf_extension_is_initialized(self, app):
        """CSRFProtect must actually be wired into the app."""
        assert app.extensions.get("csrf") is csrf

    def test_csrf_protects_state_changing_methods(self, app):
        assert app.config["WTF_CSRF_METHODS"] == {"POST", "PUT", "PATCH", "DELETE"}

    def test_csrf_accepts_the_header_the_front_end_sends(self, app):
        assert "X-CSRFToken" in app.config["WTF_CSRF_HEADERS"]

    def test_csrf_tokens_do_not_time_out(self, app):
        """Admin tabs stay open all competition; expiry would 400 them."""
        assert app.config["WTF_CSRF_TIME_LIMIT"] is None

    def test_conftest_still_disables_csrf_for_the_shared_client(self, app):
        """Regression guard for the rest of the suite."""
        assert app.config["WTF_CSRF_ENABLED"] is False


class TestSessionCookieHardening:
    def test_session_cookie_is_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_is_samesite_lax(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_session_cookie_secure_follows_config(self, app):
        assert app.config["SESSION_COOKIE_SECURE"] == config.session_cookie_secure

    def test_remember_cookie_is_httponly(self, app):
        assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True

    def test_remember_cookie_is_samesite_lax(self, app):
        assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"

    def test_remember_cookie_secure_follows_config(self, app):
        assert app.config["REMEMBER_COOKIE_SECURE"] == config.session_cookie_secure

    def test_set_cookie_header_carries_the_flags(self, test_client, three_teams):
        test_client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
        )
        cookies = test_client.get("/scoreboard").headers.getlist("Set-Cookie")
        session_cookies = [c for c in cookies if c.startswith("session=")]
        assert session_cookies, "no session cookie was set"
        for cookie in session_cookies:
            assert "HttpOnly" in cookie
            assert "SameSite=Lax" in cookie


class TestCSRFEnforcement:
    def test_post_without_token_is_rejected(self, csrf_client):
        client, _ = csrf_client
        resp = client.post("/api/admin/add_team", data={"name": "Nope", "color": "Blue"})
        assert resp.status_code == 400
        assert db.session.query(Team).filter_by(name="Nope").first() is None

    def test_post_with_form_token_succeeds(self, csrf_client):
        client, token = csrf_client
        resp = client.post(
            "/api/admin/add_team",
            data={"name": "Green Team", "color": "Blue", "csrf_token": token},
        )
        assert resp.status_code == 302  # redirect back to /admin/manage on success
        assert db.session.query(Team).filter_by(name="Green Team").first() is not None

    def test_post_with_header_token_succeeds(self, csrf_client):
        """The X-CSRFToken path used by every jQuery call in the front end."""
        client, token = csrf_client
        resp = client.post("/api/admin/toggle_engine", headers={"X-CSRFToken": token})
        assert resp.status_code == 200
        assert resp.json["status"] == "Success"

    def test_post_with_bogus_token_is_rejected(self, csrf_client):
        client, _ = csrf_client
        resp = client.post("/api/admin/toggle_engine", headers={"X-CSRFToken": "not-a-real-token"})
        assert resp.status_code == 400

    def test_delete_without_token_is_rejected(self, csrf_client):
        """DELETE is state changing too - announcements/templates use it."""
        client, _ = csrf_client
        resp = client.delete("/api/admin/announcements/1")
        assert resp.status_code == 400

    def test_put_without_token_is_rejected(self, csrf_client):
        client, _ = csrf_client
        resp = client.put("/api/admin/welcome/config", json={})
        assert resp.status_code == 400

    def test_api_csrf_failure_returns_json(self, csrf_client):
        """XHR callers parse the body as JSON; an HTML error page confuses them."""
        client, _ = csrf_client
        resp = client.post("/api/admin/toggle_engine")
        assert resp.status_code == 400
        assert resp.is_json
        assert resp.json["status"] == "error"

    def test_login_without_token_is_rejected(self, csrf_enabled, three_teams):
        resp = csrf_enabled.test_client().post("/login", data={"username": "whiteuser", "password": "testpass"})
        assert resp.status_code == 400

    def test_login_with_token_succeeds(self, csrf_client):
        """The csrf_client fixture logs in with a token; prove it worked."""
        client, _ = csrf_client
        assert client.get("/admin/manage").status_code == 200


class TestCSRFExemptions:
    """Non-browser endpoints must stay exempt or their clients break."""

    def test_agent_checkin_is_exempt(self):
        assert "scoring_engine.web.views.api.agent.agent_checkin_post" in csrf._exempt_views

    def test_agent_checkin_works_without_a_token(self, csrf_enabled):
        """Black team agents send an AES-GCM payload and no session cookie."""
        psk = "testpsk123"
        db.session.add(Setting(name="agent_psk", value=psk))
        db.session.add(Setting(name="agent_show_flag_early_mins", value="5"))
        db.session.add(Setting(name="agent_checkin_interval_sec", value="60"))
        db.session.add(Team(name="Blue Team 1", color="Blue"))
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.windows,
            perm=Perm.user,
            data={"path": "C:\\flag.txt", "content": "flag{test}"},
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()

        crypter = BtaPayloadEncryption(psk, "Blue Team 1")
        payload = {"team": "Blue Team 1", "host": "10.0.0.5", "plat": "win", "flags": [flag.id]}
        resp = csrf_enabled.test_client().post(
            "/api/agent/checkin?t=Blue Team 1",
            data=crypter.dumps(payload),
            content_type="application/octet-stream",
        )

        assert resp.status_code == 200
        assert db.session.query(Solve).count() == 1

    def test_no_other_view_is_exempt(self):
        """A new exemption should be a deliberate, reviewed decision."""
        assert csrf._exempt_views == {"scoring_engine.web.views.api.agent.agent_checkin_post"}
        assert csrf._exempt_blueprints == set()


class TestTemplatesCarryTokens:
    """Every page the front end posts from must be able to produce a token."""

    def test_base_template_renders_the_meta_tag(self, test_client):
        resp = test_client.get("/scoreboard")
        assert b'<meta name="csrf-token"' in resp.data

    def test_base_template_installs_the_ajax_hook(self, test_client):
        resp = test_client.get("/scoreboard")
        assert b"ajaxSend" in resp.data
        assert b"X-CSRFToken" in resp.data

    @pytest.mark.parametrize(
        "path,expected_forms",
        [
            ("/admin/settings", 3),
            ("/admin/sla", 11),
            ("/admin/manage", 3),
            ("/profile", 1),
        ],
    )
    def test_post_forms_include_a_hidden_token(self, white_login, path, expected_forms):
        client, _ = white_login
        body = client.get(path).data.decode()
        assert body.count('<form method="POST"') == expected_forms
        assert body.count('name="csrf_token"') >= expected_forms

    def test_login_form_includes_a_token(self, csrf_enabled):
        """login.html renders form.csrf_token, which only emits when enabled."""
        assert b'name="csrf_token"' in csrf_enabled.test_client().get("/login").data

    def test_every_post_form_carries_a_token(self):
        """Scan the templates so a new POST form cannot skip the hidden field."""
        missing = []
        for name, source in _all_templates():
            lines = source.split("\n")
            for index, line in enumerate(lines):
                if '<form method="POST"' not in line:
                    continue
                # The token must be inside the form, which in practice means
                # within the next couple of lines of the opening tag.
                end = index + 3
                window = "\n".join(lines[index:end])
                if 'name="csrf_token"' not in window and "form.csrf_token" not in window:
                    missing.append(f"{name}:{index + 1}")
        assert missing == [], f"POST forms without a CSRF token: {missing}"

    def test_no_template_uses_a_request_path_the_jquery_hook_misses(self):
        """fetch()/raw XHR bypass $(document).ajaxSend and must be handled.

        If one is ever added it needs its own X-CSRFToken header; failing here
        is the reminder to do that rather than ship a broken button.
        """
        offenders = []
        for name, source in _all_templates():
            for pattern in ("fetch(", "new XMLHttpRequest"):
                if pattern in source and "X-CSRFToken" not in source:
                    offenders.append(f"{name} ({pattern})")
        assert offenders == [], f"uncovered request APIs in templates: {offenders}"

    def test_every_dropzone_sets_the_header(self):
        for name, source in _all_templates():
            lines = source.split("\n")
            for index, line in enumerate(lines):
                if "new Dropzone(" in line:
                    end = index + 10
                    block = "\n".join(lines[index:end])
                    assert "X-CSRFToken" in block, f"{name}:{index + 1} Dropzone without CSRF header"

    def test_dropzone_upload_sends_the_header(self, app):
        """Dropzone uses its own XHR and is not covered by the jQuery hook.

        Asserted against the template source rather than a rendered page so the
        test does not need a full inject fixture just to reach the uploader.
        """
        source = app.jinja_env.loader.get_source(app.jinja_env, "inject.html")[0]
        assert "'X-CSRFToken': window.CSRF_TOKEN" in source

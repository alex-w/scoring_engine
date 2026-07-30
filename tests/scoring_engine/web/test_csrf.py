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
from scoring_engine.web.csrf import csrf, form_retry_target, is_browser_form_submission
from scoring_engine.web.views.api.agent import BtaPayloadEncryption

CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
CSRF_FIELD_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')
POST_FORM_RE = re.compile(r"""<form\b[^>]*\bmethod\s*=\s*["']post["']""", re.IGNORECASE)

#: What a browser actually sends when it submits a native <form method="POST">:
#: no X-Requested-With, and an Accept that ranks HTML above everything else.
BROWSER_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

#: What jQuery $.ajax() sends. jQuery adds X-Requested-With to every
#: same-origin request, so this is the shape of every scripted call in the app.
XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

#: Lines after a call site that count as "this call's options object".
CALL_SITE_WINDOW = 12

#: Lines before a call site to scan too, so an annotation comment sitting
#: immediately above the call counts as part of it.
CALL_SITE_LOOKBACK = 2

#: Escape hatch for the call-site scanner below: a read-only fetch()/XHR needs
#: no token, but it has to say so out loud rather than be guessed at.
SAFE_CALL_MARKERS = (
    "csrf-safe",
    "method: 'GET'",
    'method: "GET"',
    "method: 'HEAD'",
    '.open("GET"',
    ".open('GET'",
)

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


def uncovered_request_call_sites(name, source, window=CALL_SITE_WINDOW):
    """Report every ``fetch()``/``new XMLHttpRequest`` call site with no token.

    Scoped to the **call site**, not the file.  A file-wide
    ``"X-CSRFToken" in source`` test passes the moment a template contains one
    protected call anywhere -- which is exactly the situation in
    ``inject.html``, where the Dropzone uploader already sets the header, so a
    new unprotected ``fetch()`` added below it would sail straight through.
    Checking a window of lines around each call site closes that hole.

    Returned entries are ``"<template>:<line>"``.
    """
    offenders = []
    lines = source.split("\n")
    for index, line in enumerate(lines):
        if "fetch(" not in line and "new XMLHttpRequest" not in line:
            continue
        block = "\n".join(lines[max(0, index - CALL_SITE_LOOKBACK) : index + window])
        if "X-CSRFToken" in block:
            continue
        if any(marker in block for marker in SAFE_CALL_MARKERS):
            continue
        offenders.append(f"{name}:{index + 1}")
    return offenders


def _token_from(response):
    """Pull a usable CSRF token out of a rendered page.

    Prefers the ``csrf-token`` meta tag base.html renders for signed-in
    visitors, and falls back to a hidden ``csrf_token`` form field -- which is
    all an anonymous page such as /login has, by design.
    """
    body = response.data.decode()
    match = CSRF_META_RE.search(body) or CSRF_FIELD_RE.search(body)
    assert match is not None, "no CSRF token (meta tag or hidden field) in the rendered page"
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

    def test_jquery_shaped_request_gets_json(self, csrf_client):
        """The exact header set jQuery puts on a same-origin $.ajax() call."""
        client, _ = csrf_client
        resp = client.post("/api/admin/toggle_engine", headers=XHR_HEADERS)
        assert resp.status_code == 400
        assert resp.is_json
        assert resp.json["error"] == "CSRF validation failed"

    def test_json_body_request_gets_json(self, csrf_client):
        """A fetch() posting JSON has no X-Requested-With but is still an API caller."""
        client, _ = csrf_client
        resp = client.put("/api/admin/welcome/config", json={}, headers=BROWSER_HEADERS)
        assert resp.status_code == 400
        assert resp.is_json

    def test_login_with_token_succeeds(self, csrf_client):
        """The csrf_client fixture logs in with a token; prove it worked."""
        client, _ = csrf_client
        assert client.get("/admin/manage").status_code == 200


class TestStaleTokenOnNativeForms:
    """A stale token on a real <form> must not be a dead end.

    Before CSRF protection existed, posting /login with an expired token simply
    re-rendered the sign-in form: one extra click and you were in.  A tab left
    open on the scoreboard for the length of a competition makes that the
    common case, so the recovery path has to stay.
    """

    def test_login_without_token_re_renders_the_form(self, csrf_enabled, three_teams):
        client = csrf_enabled.test_client()
        resp = client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/login"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/login")

    def test_login_without_token_flashes_an_explanation(self, csrf_enabled, three_teams):
        client = csrf_enabled.test_client()
        resp = client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/login"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Please try again" in resp.data
        assert b'name="csrf_token"' in resp.data

    def test_login_is_still_possible_after_a_stale_token(self, csrf_enabled, three_teams):
        """The whole point: the user must be able to just log in again."""
        client = csrf_enabled.test_client()
        retry_page = client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/login"},
            follow_redirects=True,
        )
        resp = client.post(
            "/login",
            data={"username": "whiteuser", "password": "testpass", "csrf_token": _token_from(retry_page)},
            headers=BROWSER_HEADERS,
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert client.get("/admin/manage").status_code == 200

    def test_login_without_token_from_an_xhr_still_gets_json(self, csrf_enabled, three_teams):
        """Only browsers get the redirect; scripted callers keep their 400."""
        resp = csrf_enabled.test_client().post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            headers=XHR_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.is_json

    def test_admin_form_bounces_back_to_its_page(self, csrf_client):
        """admin/manage.html posts a native form at /api/admin/add_team."""
        client, _ = csrf_client
        resp = client.post(
            "/api/admin/add_team",
            data={"name": "Nope", "color": "Blue"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/admin/manage"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/manage")
        assert db.session.query(Team).filter_by(name="Nope").first() is None

    def test_admin_form_retry_page_carries_a_fresh_token(self, csrf_client):
        client, _ = csrf_client
        resp = client.post(
            "/api/admin/add_team",
            data={"name": "Nope", "color": "Blue"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/admin/manage"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Please try again" in resp.data
        # The re-rendered page must be immediately usable.
        retry = client.post(
            "/api/admin/add_team",
            data={"name": "Green Team", "color": "Blue", "csrf_token": _token_from(resp)},
            headers=BROWSER_HEADERS,
        )
        assert retry.status_code == 302
        assert db.session.query(Team).filter_by(name="Green Team").first() is not None

    def test_profile_form_bounces_back_to_its_page(self, csrf_enabled, three_teams):
        """profile.html posts a native form at /api/profile/update_password."""
        client = csrf_enabled.test_client()
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"username": "blueuser", "password": "testpass", "csrf_token": _token_from(login_page)},
            follow_redirects=True,
        )
        resp = client.post(
            "/api/profile/update_password",
            data={"currentpassword": "testpass", "newpassword": "x", "confirmpassword": "x"},
            headers={**BROWSER_HEADERS, "Referer": "http://localhost/profile"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/profile")

    def test_a_foreign_referer_is_not_followed(self, csrf_client):
        """The retry target is attacker-influenced; it must stay same-origin."""
        client, _ = csrf_client
        resp = client.post(
            "/api/admin/add_team",
            data={"name": "Nope", "color": "Blue"},
            headers={**BROWSER_HEADERS, "Referer": "https://evil.example/pwn"},
        )
        assert resp.status_code == 302
        assert "evil.example" not in resp.headers["Location"]

    def test_a_missing_referer_falls_back_to_login_when_anonymous(self, csrf_enabled, three_teams):
        resp = csrf_enabled.test_client().post(
            "/login",
            data={"username": "whiteuser", "password": "testpass"},
            headers=BROWSER_HEADERS,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/login")


class TestRequestKindDetection:
    """Unit coverage for the JSON-vs-HTML discriminator itself."""

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            # A real browser posting a real form.
            ({"data": {"a": "b"}, "headers": BROWSER_HEADERS}, True),
            # jQuery / Dropzone: X-Requested-With wins even with an HTML Accept.
            ({"data": {"a": "b"}, "headers": {**BROWSER_HEADERS, **XHR_HEADERS}}, False),
            # A JSON body is never a form submission.
            ({"json": {"a": "b"}, "headers": BROWSER_HEADERS}, False),
            # Ambiguous callers (curl's Accept: */*, or none at all) default to JSON.
            ({"data": {"a": "b"}, "headers": {"Accept": "*/*"}}, False),
            ({"data": {"a": "b"}}, False),
        ],
    )
    def test_browser_form_detection(self, app, kwargs, expected):
        with app.test_request_context("/api/admin/add_team", method="POST", **kwargs):
            assert is_browser_form_submission() is expected

    @pytest.mark.parametrize(
        "referrer,expected",
        [
            ("http://localhost/admin/manage", "/admin/manage"),
            ("http://localhost/login?next=%2Fadmin", "/login?next=%2Fadmin"),
            ("/admin/manage", "/admin/manage"),
            ("https://evil.example/pwn", "/fallback"),
            ("javascript:alert(1)", "/fallback"),
            ("//evil.example/pwn", "/fallback"),
            (None, "/fallback"),
        ],
    )
    def test_retry_target_stays_same_origin(self, app, referrer, expected):
        headers = {"Referer": referrer} if referrer else {}
        with app.test_request_context("/api/admin/add_team", method="POST", headers=headers):
            assert form_retry_target("/fallback") == expected


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

    def test_base_template_renders_the_meta_tag_when_signed_in(self, white_login):
        client, _ = white_login
        assert b'<meta name="csrf-token"' in client.get("/scoreboard").data

    @pytest.mark.parametrize("path", ["/", "/scoreboard", "/overview", "/about", "/announcements"])
    def test_base_template_withholds_the_meta_tag_from_anonymous_visitors(self, test_client, path):
        """Minting a token would give every anonymous spectator a session.

        See TestAnonymousPagesStayStateless for why that matters.  None of
        these pages issue anything but GETs, so none of them need a token.
        """
        assert b'<meta name="csrf-token"' not in test_client.get(path).data

    def test_base_template_installs_the_ajax_hook(self, white_login):
        client, _ = white_login
        resp = client.get("/scoreboard")
        assert b"ajaxSend" in resp.data
        assert b"X-CSRFToken" in resp.data

    def test_ajax_hook_tolerates_a_missing_token(self, test_client):
        """Anonymous pages still install the hook; it must not throw."""
        body = test_client.get("/scoreboard").data.decode()
        assert "meta ? meta.getAttribute('content') : ''" in body
        assert "if (window.CSRF_TOKEN &&" in body

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
        """Scan the templates so a new POST form cannot skip the hidden field.

        Matched with a regex rather than the literal ``<form method="POST"``:
        a form written ``<form action="..." method="post">`` is just as
        state changing and must not slip past the tripwire on attribute order
        or letter case.
        """
        missing = []
        for name, source in _all_templates():
            lines = source.split("\n")
            for index, line in enumerate(lines):
                if not POST_FORM_RE.search(line):
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

        If one is ever added it needs its own X-CSRFToken header (or an
        explicit safe-method marker); failing here is the reminder to do that
        rather than ship a broken button.
        """
        offenders = []
        for name, source in _all_templates():
            offenders.extend(uncovered_request_call_sites(name, source))
        assert offenders == [], f"uncovered request APIs in templates: {offenders}"

    def test_the_scanner_is_scoped_to_the_call_site_not_the_file(self):
        """Regression guard for the guard itself.

        inject.html already contains an X-CSRFToken (Dropzone), so a file-wide
        substring check would wave through anything added after it -- and
        inject.html is the template most likely to grow a new call.
        """
        source = "\n".join(
            [
                "new Dropzone('#file-upload', {",
                "  headers: { 'X-CSRFToken': window.CSRF_TOKEN },",
                "});",
                "",
                "// ... 40 lines later, someone adds:",
                "fetch('/api/inject/1/status', { method: 'POST', body: payload });",
            ]
        )
        assert uncovered_request_call_sites("inject.html", source) == ["inject.html:6"]

    def test_the_scanner_accepts_a_protected_call(self):
        source = "fetch('/api/x', {\n  method: 'POST',\n  headers: { 'X-CSRFToken': window.CSRF_TOKEN },\n});"
        assert uncovered_request_call_sites("x.html", source) == []

    def test_the_scanner_accepts_an_explicitly_safe_call(self):
        source = "// csrf-safe: read only\nfetch('/api/scoreboard/data');"
        assert uncovered_request_call_sites("x.html", source) == []


class TestAnonymousPagesStayStateless:
    """Anonymous visitors must not be handed a session.

    A non-empty session for an anonymous visitor trips Flask-Login's "strong"
    session protection on the *next* request, which hangs a
    remember-token-deleting Set-Cookie on every response from then on.  The
    public scoreboard is the highest-traffic page in the app and the one most
    worth caching, so it has to stay cookie-free.
    """

    @pytest.mark.parametrize("path", ["/", "/scoreboard", "/overview", "/about", "/announcements"])
    def test_no_session_cookie_is_issued(self, test_client, path):
        resp = test_client.get(path)
        assert resp.status_code == 200
        session_cookies = [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("session=")]
        assert session_cookies == [], f"{path} started a session for an anonymous visitor"

    def test_repeat_visits_do_not_clear_the_remember_cookie(self, test_client):
        test_client.get("/scoreboard")
        resp = test_client.get("/scoreboard")
        assert "remember_token" not in "".join(resp.headers.getlist("Set-Cookie"))

    def test_signed_in_pages_still_get_a_token(self, white_login):
        """The fix must not cost the authenticated pages their token."""
        client, _ = white_login
        assert _token_from(client.get("/admin/manage"))

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

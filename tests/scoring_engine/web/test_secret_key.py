"""Tests for the Flask session signing key.

The key must come from configuration so that sessions survive a web restart and
work across multiple web processes/containers. When nothing is configured the
app must still boot, with a loud warning, and must never fall back to a
hardcoded key.

Note: ``tests/conftest.py`` swaps ``scoring_engine.config`` for a MockConfig
whose ``config`` property builds a fresh ConfigLoader on every access, so each
importing module holds its own instance. These tests therefore patch
``scoring_engine.web.config`` -- the exact object ``get_secret_key`` reads.
"""

import logging
import os
import re

import pytest

import scoring_engine.web as web

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SETUP_SCRIPT = os.path.join(REPO_ROOT, "bin", "setup")


@pytest.fixture(autouse=True)
def _ensure_scoring_engine_log_propagates():
    """Keep the ``scoring_engine`` logger propagating so ``caplog`` can see it.

    ``scoring_engine/engine/execute_command.py`` sets ``logger.propagate = False``
    as a process-wide side effect (to suppress duplicate celery log lines). Under
    pytest-xdist that leaks into whatever test runs next in the same worker, and
    the caplog-based tests here only capture records that propagate to the root
    handler -- so without this they fail depending on test distribution. Restore
    the default for the duration of each test in this module.
    """
    logger = logging.getLogger("scoring_engine")
    saved = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = saved


# Anything that looks like a generated key: a long unbroken run of hex.
LOOKS_LIKE_A_KEY = re.compile(r"[0-9a-f]{32,}")


@pytest.fixture
def set_secret_key(monkeypatch):
    """Set secret_key on the config object scoring_engine.web actually uses."""

    def _set(value):
        monkeypatch.setattr(web.config, "secret_key", value, raising=False)

    return _set


class TestGetSecretKey:
    def test_configured_value_is_used(self, set_secret_key):
        set_secret_key("a-very-stable-secret")
        assert web.get_secret_key() == "a-very-stable-secret"

    def test_configured_value_is_stable_across_calls(self, set_secret_key):
        """Two processes reading the same config must agree on the key."""
        set_secret_key("a-very-stable-secret")
        assert web.get_secret_key() == web.get_secret_key()

    def test_configured_value_does_not_warn(self, set_secret_key, caplog):
        set_secret_key("a-very-stable-secret")
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            web.get_secret_key()
        assert "secret_key" not in caplog.text

    def test_surrounding_whitespace_is_stripped(self, set_secret_key):
        set_secret_key("  padded-secret\n")
        assert web.get_secret_key() == "padded-secret"

    def test_whitespace_only_value_counts_as_unset(self, set_secret_key, caplog):
        set_secret_key("   ")
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            key = web.get_secret_key()
        assert key.strip() == key
        assert "No secret_key is configured" in caplog.text

    def test_missing_value_still_boots_and_warns(self, set_secret_key, caplog):
        set_secret_key("")
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            key = web.get_secret_key()

        # Still usable: a real key was produced, so the app can boot.
        assert key
        assert len(key) >= 32

        # And the operator is told exactly what breaks.
        assert "No secret_key is configured" in caplog.text
        assert "SCORINGENGINE_SECRET_KEY" in caplog.text
        assert "restart" in caplog.text

    def test_missing_value_generates_a_different_key_each_time(self, set_secret_key):
        """Confirms the fallback really is ephemeral (hence the warning)."""
        set_secret_key("")
        assert web.get_secret_key() != web.get_secret_key()

    def test_unset_attribute_is_tolerated(self, monkeypatch, caplog):
        """A stale config object without the option must not crash the app."""
        monkeypatch.delattr(web.config, "secret_key", raising=False)
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            key = web.get_secret_key()
        assert key
        assert "No secret_key is configured" in caplog.text

    def test_no_hardcoded_default_key(self, set_secret_key, caplog):
        """The unset path must generate randomness, not return a shipped constant."""
        set_secret_key(None)
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            keys = {web.get_secret_key() for _ in range(5)}
        assert len(keys) == 5


class TestCreateAppSecretKey:
    def test_app_uses_configured_secret_key(self, set_secret_key):
        set_secret_key("configured-app-secret")
        app = web.create_app()
        assert app.secret_key == "configured-app-secret"

    def test_two_apps_share_a_configured_key(self, set_secret_key):
        """This is what makes more than one web process/container possible."""
        set_secret_key("configured-app-secret")
        assert web.create_app().secret_key == web.create_app().secret_key

    def test_app_boots_without_configured_secret_key(self, set_secret_key, caplog):
        set_secret_key("")
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            app = web.create_app()
        assert app.secret_key
        assert "No secret_key is configured" in caplog.text


class TestSecretKeyIsConfigured:
    """bin/setup and the web app must agree on what 'configured' means."""

    @pytest.mark.parametrize("value", ["a-very-stable-secret", "  padded-secret\n"])
    def test_true_for_usable_values(self, set_secret_key, value):
        set_secret_key(value)
        assert web.secret_key_is_configured() is True

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_false_for_unusable_values(self, set_secret_key, value):
        set_secret_key(value)
        assert web.secret_key_is_configured() is False

    def test_false_when_option_is_absent(self, monkeypatch):
        monkeypatch.delattr(web.config, "secret_key", raising=False)
        assert web.secret_key_is_configured() is False


class TestWarningsNeverCarryASecret:
    """Log output is widely readable, so it must never contain a live key.

    Container logs are visible to anyone who can run ``docker logs`` and are
    routinely forwarded to an aggregator. A key printed there is compromised on
    arrival, so we emit the command to generate one instead.
    """

    @pytest.mark.parametrize(
        "message",
        [web.MISSING_SECRET_KEY_WARNING, web.MISSING_SECRET_KEY_SETUP_WARNING],
    )
    def test_message_tells_the_operator_how_to_generate_a_key(self, message):
        assert "secrets.token_hex(64)" in message

    @pytest.mark.parametrize(
        "message",
        [web.MISSING_SECRET_KEY_WARNING, web.MISSING_SECRET_KEY_SETUP_WARNING],
    )
    def test_message_is_constant_and_contains_no_key(self, message):
        assert LOOKS_LIKE_A_KEY.search(message) is None

    def test_runtime_warning_does_not_leak_the_generated_key(self, set_secret_key, caplog):
        """The throwaway key the web app falls back to must not be logged."""
        set_secret_key("")
        with caplog.at_level(logging.WARNING, logger="scoring_engine"):
            key = web.get_secret_key()
        assert key not in caplog.text
        assert LOOKS_LIKE_A_KEY.search(caplog.text) is None

    def test_configured_key_is_never_logged(self, set_secret_key, caplog):
        set_secret_key("0123456789abcdef0123456789abcdef0123456789abcdef")
        with caplog.at_level(logging.DEBUG, logger="scoring_engine"):
            web.create_app()
        assert "0123456789abcdef" not in caplog.text


class TestSetupScriptDoesNotPrintASecret:
    """bin/setup warns about a missing key; it must not generate and print one."""

    def _source(self):
        with open(SETUP_SCRIPT) as fh:
            return fh.read()

    def test_does_not_generate_a_key(self):
        source = self._source()
        for generator in ("token_hex", "token_urlsafe", "token_bytes", "urandom"):
            assert generator not in source, f"bin/setup must not generate a secret ({generator})"

    def test_uses_the_shared_warning(self):
        source = self._source()
        assert "MISSING_SECRET_KEY_SETUP_WARNING" in source
        assert "secret_key_is_configured" in source

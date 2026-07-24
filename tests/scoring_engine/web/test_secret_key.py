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

import pytest

import scoring_engine.web as web


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

import os

from scoring_engine.config_loader import ConfigLoader

TESTS_CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.conf.inc")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigLoader(object):
    def setup_method(self):
        self.config = ConfigLoader(location="../tests/scoring_engine/engine.conf.inc")

    def test_debug(self):
        assert self.config.debug is False

    def test_checks_location(self):
        assert self.config.checks_location == "scoring_engine/checks"

    def test_target_round_time(self):
        assert self.config.target_round_time == 180

    def test_agent_psk(self):
        assert self.config.agent_psk == "TheCakeIsALie"

    def test_agent_show_flag_early_mins(self):
        assert self.config.agent_show_flag_early_mins == 5

    def test_worker_refresh_time(self):
        assert self.config.worker_refresh_time == 30

    def test_blue_team_update_hostname(self):
        assert self.config.blue_team_update_hostname is True

    def test_blue_team_update_port(self):
        assert self.config.blue_team_update_port is True

    def test_blue_team_update_account_usernames(self):
        assert self.config.blue_team_update_account_usernames is True

    def test_blue_team_update_account_passwords(self):
        assert self.config.blue_team_update_account_passwords is True

    def test_blue_team_view_check_output(self):
        assert self.config.blue_team_view_check_output is True

    def test_db_uri(self):
        # Under xdist each worker gets a unique DB path via env var override
        worker_id = os.environ.get("PYTEST_XDIST_WORKER")
        if worker_id is not None:
            expected = f"sqlite:////tmp/test_engine_{worker_id}.db?check_same_thread=False"
        else:
            expected = "sqlite:////tmp/test_engine.db?check_same_thread=False"
        assert self.config.db_uri == expected

    def test_timezone(self):
        assert self.config.timezone == "US/Eastern"

    def test_redis_host(self):
        assert self.config.redis_host == "127.0.0.1"

    def test_redis_port(self):
        assert self.config.redis_port == 6379

    def test_redis_password(self):
        assert self.config.redis_password == "testpass"

    def test_secret_key(self):
        assert self.config.secret_key == "testsecretkey"

    def test_parse_sources_default(self):
        assert self.config.parse_sources("testname", "abcdefg") == "abcdefg"

    def test_parse_sources_int(self):
        assert self.config.parse_sources("testname", 1234, "int") == 1234

    def test_parse_sources_bool(self):
        assert self.config.parse_sources("testname", False, "bool") is False

    def test_worker_num_concurrent_tasks(self):
        assert self.config.worker_num_concurrent_tasks == 4

    def test_worker_queue(self):
        assert self.config.worker_queue == "main"

    def test_parse_sources_int_environment(self):
        os.environ["SCORINGENGINE_ROUND_SLEEP_TIME"] = "1"
        assert self.config.parse_sources("round_sleep_time", "1234", "int") == 1

    def test_parse_sources_bool_environment(self):
        os.environ["SCORINGENGINE_DEBUG"] = "False"
        assert self.config.parse_sources("debug", True, "bool") is False

    def test_parse_sources_str_environment(self):
        os.environ["SCORINGENGINE_REDIS_HOST"] = "127.0.0.1"
        assert self.config.parse_sources("redis_host", "1.2.3.4") == "127.0.0.1"


class TestSecretKeyConfig(object):
    """secret_key follows the standard env var -> config file -> default chain."""

    def _conf_without_secret_key(self, tmp_path):
        """Copy the test config, dropping the secret_key line."""
        with open(TESTS_CONF) as fh:
            lines = [line for line in fh if not line.startswith("secret_key")]
        target = tmp_path / "no_secret_key.inc"
        target.write_text("".join(lines))
        # ConfigLoader joins the location onto its own directory, but an
        # absolute path wins, so this loads exactly the file we just wrote.
        return str(target)

    def test_read_from_config_file(self, monkeypatch):
        monkeypatch.delenv("SCORINGENGINE_SECRET_KEY", raising=False)
        config = ConfigLoader(location=TESTS_CONF)
        assert config.secret_key == "testsecretkey"

    def test_environment_variable_overrides_file(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_SECRET_KEY", "from-the-environment")
        config = ConfigLoader(location=TESTS_CONF)
        assert config.secret_key == "from-the-environment"

    def test_defaults_to_empty_when_absent(self, monkeypatch, tmp_path):
        """An absent secret_key must not raise and must not get a shipped default."""
        monkeypatch.delenv("SCORINGENGINE_SECRET_KEY", raising=False)
        config = ConfigLoader(location=self._conf_without_secret_key(tmp_path))
        assert config.secret_key == ""

    def test_no_hardcoded_key_in_example_config(self, monkeypatch):
        """engine.conf.inc must never ship an actual key value."""
        monkeypatch.delenv("SCORINGENGINE_SECRET_KEY", raising=False)
        config = ConfigLoader(location=os.path.join(REPO_ROOT, "engine.conf.inc"))
        assert config.secret_key == ""


class TestEmptyEnvironmentVariables(object):
    """A present-but-empty env var must not override the config file.

    Environment files get created by copying an example, and ``docker compose``
    forwards a bare ``SCORINGENGINE_FOO=`` line into the container as an empty
    string. Treating that as an override silently blanked out correctly
    configured values (strings) or crashed at startup (ints/floats).
    """

    def _conf(self, tmp_path, body):
        target = tmp_path / "engine.conf.inc"
        target.write_text(body)
        return str(target)

    def test_empty_env_does_not_override_string(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_SECRET_KEY", "")
        assert ConfigLoader(location=TESTS_CONF).secret_key == "testsecretkey"

    def test_whitespace_only_env_does_not_override_string(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_AGENT_PSK", "   \t\n")
        assert ConfigLoader(location=TESTS_CONF).agent_psk == "TheCakeIsALie"

    def test_non_empty_env_still_overrides_string(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_AGENT_PSK", "from-the-environment")
        assert ConfigLoader(location=TESTS_CONF).agent_psk == "from-the-environment"

    def test_empty_env_does_not_override_int(self, monkeypatch):
        """Previously this raised ValueError('') and the app would not boot."""
        monkeypatch.setenv("SCORINGENGINE_REDIS_PORT", "")
        assert ConfigLoader(location=TESTS_CONF).redis_port == 6379

    def test_non_empty_env_still_overrides_int(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_REDIS_PORT", "6380")
        assert ConfigLoader(location=TESTS_CONF).redis_port == 6380

    def test_empty_env_does_not_override_float(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_DYNAMIC_SCORING_EARLY_MULTIPLIER", "")
        assert ConfigLoader(location=TESTS_CONF).dynamic_scoring_early_multiplier == 2.0

    def test_empty_env_does_not_flip_bool_to_false(self, monkeypatch):
        """An empty value used to read as False and silently disable a feature."""
        monkeypatch.setenv("SCORINGENGINE_BLUE_TEAM_UPDATE_HOSTNAME", "")
        assert ConfigLoader(location=TESTS_CONF).blue_team_update_hostname is True

    def test_non_empty_env_still_overrides_bool(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_BLUE_TEAM_UPDATE_HOSTNAME", "false")
        assert ConfigLoader(location=TESTS_CONF).blue_team_update_hostname is False

    def test_padded_bool_env_is_honored(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_BLUE_TEAM_UPDATE_HOSTNAME", " false ")
        assert ConfigLoader(location=TESTS_CONF).blue_team_update_hostname is False

    def test_intentionally_blank_config_value_is_preserved(self, monkeypatch, tmp_path):
        """redis_password ships blank in engine.conf.inc; that must still work."""
        monkeypatch.delenv("SCORINGENGINE_REDIS_PASSWORD", raising=False)
        body = open(TESTS_CONF).read().replace("redis_password = testpass", "redis_password =")
        assert ConfigLoader(location=self._conf(tmp_path, body)).redis_password == ""

    def test_blank_config_value_with_empty_env_stays_blank(self, monkeypatch, tmp_path):
        """The common 'no redis password anywhere' case is unaffected."""
        monkeypatch.setenv("SCORINGENGINE_REDIS_PASSWORD", "")
        body = open(TESTS_CONF).read().replace("redis_password = testpass", "redis_password =")
        assert ConfigLoader(location=self._conf(tmp_path, body)).redis_password == ""

    def test_env_can_still_set_a_blank_config_value(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SCORINGENGINE_REDIS_PASSWORD", "hunter2")
        body = open(TESTS_CONF).read().replace("redis_password = testpass", "redis_password =")
        assert ConfigLoader(location=self._conf(tmp_path, body)).redis_password == "hunter2"

    def test_parse_sources_treats_empty_env_as_unset(self, monkeypatch):
        monkeypatch.setenv("SCORINGENGINE_TESTNAME", "")
        loader = ConfigLoader(location=TESTS_CONF)
        assert loader.parse_sources("testname", "fallback") == "fallback"
        assert loader.parse_sources("testname", 1234, "int") == 1234
        assert loader.parse_sources("testname", 1.5, "float") == 1.5
        assert loader.parse_sources("testname", True, "bool") is True


class TestEnvExampleFile(object):
    """.env.example must not ship assignments that blank out engine.conf."""

    def _lines(self):
        with open(os.path.join(REPO_ROOT, ".env.example")) as fh:
            return [line.strip() for line in fh]

    def test_no_active_empty_assignments(self):
        empty = [
            line
            for line in self._lines()
            if line and not line.startswith("#") and "=" in line and not line.split("=", 1)[1].strip()
        ]
        assert empty == [], f"empty assignments in .env.example would override engine.conf: {empty}"

    def test_secret_key_line_is_commented_out(self):
        active = [line for line in self._lines() if line.startswith("SCORINGENGINE_SECRET_KEY")]
        assert active == []
        assert any(line.startswith("#SCORINGENGINE_SECRET_KEY") for line in self._lines())

    def test_no_real_secret_key_is_shipped(self):
        """.env.example must document the key, never contain one."""
        for line in self._lines():
            value = line.split("=", 1)[1].strip() if "SCORINGENGINE_SECRET_KEY" in line and "=" in line else ""
            assert len(value) < 32


def test_default_uses_example_config():
    """Ensure ConfigLoader falls back to the bundled example config.

    In environments where ``engine.conf`` is not present (like CI), the
    loader should automatically read ``engine.conf.inc`` so that sensible
    defaults are available and tests can execute.
    """
    # Temporarily clear xdist env override so we test the real file default
    saved = os.environ.pop("SCORINGENGINE_DB_URI", None)
    try:
        cfg = ConfigLoader()  # no explicit path provided
        # A value from engine.conf.inc confirms the fallback worked
        assert cfg.db_uri == "sqlite:////tmp/engine.db?check_same_thread=False"
    finally:
        if saved is not None:
            os.environ["SCORINGENGINE_DB_URI"] = saved

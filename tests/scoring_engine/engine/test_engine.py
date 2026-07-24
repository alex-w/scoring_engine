import itertools
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from scoring_engine.checks.agent import AgentCheck
from scoring_engine.checks.dns import DNSCheck
from scoring_engine.checks.elasticsearch import ElasticsearchCheck
from scoring_engine.checks.ftp import FTPCheck
from scoring_engine.checks.http import HTTPCheck
from scoring_engine.checks.https import HTTPSCheck
from scoring_engine.checks.icmp import ICMPCheck
from scoring_engine.checks.imap import IMAPCheck
from scoring_engine.checks.imaps import IMAPSCheck
from scoring_engine.checks.ldap import LDAPCheck
from scoring_engine.checks.mssql import MSSQLCheck
from scoring_engine.checks.mysql import MYSQLCheck
from scoring_engine.checks.nfs import NFSCheck
from scoring_engine.checks.openvpn import OpenVPNCheck
from scoring_engine.checks.pop3 import POP3Check
from scoring_engine.checks.pop3s import POP3SCheck
from scoring_engine.checks.postgresql import POSTGRESQLCheck
from scoring_engine.checks.rdp import RDPCheck
from scoring_engine.checks.smb import SMBCheck
from scoring_engine.checks.smtp import SMTPCheck
from scoring_engine.checks.smtps import SMTPSCheck
from scoring_engine.checks.ssh import SSHCheck
from scoring_engine.checks.telnet import TelnetCheck
from scoring_engine.checks.vnc import VNCCheck
from scoring_engine.checks.webapp_nginxdefaultpage import WebappNginxdefaultpageCheck
from scoring_engine.checks.webapp_scoringengine import WebappScoringengineCheck
from scoring_engine.checks.winrm import WinRMCheck
from scoring_engine.checks.wordpress import WordpressCheck
from scoring_engine.db import db
from scoring_engine.engine.basic_check import CHECK_SUCCESS_TEXT, CHECK_TIMED_OUT_TEXT
from scoring_engine.engine.engine import Engine
from scoring_engine.models.check import Check
from scoring_engine.models.environment import Environment
from scoring_engine.models.kb import KB
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team


class TestEngine:
    @pytest.fixture(autouse=True)
    def setup(self, db_session):
        target_round_time_obj = Setting.get_setting("target_round_time")
        target_round_time_obj.value = 0
        db.session.add(target_round_time_obj)
        worker_refresh_time_obj = Setting.get_setting("worker_refresh_time")
        worker_refresh_time_obj.value = 0
        db.session.add(worker_refresh_time_obj)

        db.session.commit()

    def test_init(self):
        engine = Engine()
        expected_checks = [
            AgentCheck,
            ICMPCheck,
            SSHCheck,
            DNSCheck,
            FTPCheck,
            HTTPCheck,
            HTTPSCheck,
            MYSQLCheck,
            MSSQLCheck,
            POSTGRESQLCheck,
            POP3Check,
            POP3SCheck,
            IMAPCheck,
            IMAPSCheck,
            SMTPCheck,
            SMTPSCheck,
            VNCCheck,
            ElasticsearchCheck,
            LDAPCheck,
            SMBCheck,
            RDPCheck,
            WordpressCheck,
            NFSCheck,
            OpenVPNCheck,
            WebappScoringengineCheck,
            WebappNginxdefaultpageCheck,
            TelnetCheck,
            WinRMCheck,
        ]
        assert {cls.__name__ for cls in engine.checks} == {
            cls.__name__ for cls in expected_checks
        }, "Mismatch in check names"

    def test_total_rounds_init(self):
        engine = Engine(total_rounds=100)
        assert engine.total_rounds == 100

    def test_shutdown(self):
        engine = Engine()
        assert engine.last_round is False
        engine.shutdown()
        assert engine.last_round is True

    def test_run_one_round(self):
        engine = Engine(total_rounds=1)
        assert engine.rounds_run == 0
        engine.run()
        assert engine.rounds_run == 1
        assert engine.current_round == 1

    def test_run_ten_rounds(self):
        engine = Engine(total_rounds=10)
        assert engine.current_round == 0
        assert engine.rounds_run == 0
        engine.run()
        assert engine.rounds_run == 10
        assert engine.current_round == 10

    def test_run_hundred_rounds(self):
        engine = Engine(total_rounds=100)
        assert engine.current_round == 0
        assert engine.rounds_run == 0
        engine.run()
        assert engine.rounds_run == 100
        assert engine.current_round == 100

    def test_check_name_to_obj_positive(self):
        engine = Engine()
        check_obj = engine.check_name_to_obj("ICMP IPv4 Check")
        from scoring_engine.checks.icmp import ICMPCheck

        check_obj == ICMPCheck

    def test_check_name_to_obj_negative(self):
        engine = Engine()
        check_obj = engine.check_name_to_obj("Garbage Check")
        assert check_obj is None

    def test_is_last_round_unlimited(self):
        engine = Engine()
        assert engine.is_last_round() is False

    def test_is_last_round_true(self):
        engine = Engine()
        engine.last_round = True
        assert engine.is_last_round() is True

    def test_is_last_round_restricted(self):
        engine = Engine(total_rounds=1)
        engine.rounds_run = 1
        assert engine.is_last_round() is True

    @patch("scoring_engine.engine.engine.execute_command")
    def test_jitter_applies_countdown(self, mock_execute_command):
        """When task_jitter_max_delay > 0, apply_async gets a countdown > 0."""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)
        service = Service(
            name="ICMP Service",
            team=team,
            check_name="ICMPCheck",
            host="127.0.0.1",
        )
        db.session.add(service)
        env = Environment(service=service, matching_content="*")
        db.session.add(env)
        db.session.commit()

        # Fake a completed async result so the engine doesn't wait forever
        mock_result = MagicMock()
        mock_result.id = "fake-task-id"
        mock_result.state = "SUCCESS"
        mock_result.result = {
            "environment_id": env.id,
            "errored_out": False,
            "output": "*",
            "command": "echo test",
        }
        mock_execute_command.apply_async.return_value = mock_result
        mock_execute_command.AsyncResult.return_value = mock_result

        engine = Engine(total_rounds=1)
        engine.config.task_jitter_max_delay = 30
        engine.run()

        call_kwargs = mock_execute_command.apply_async.call_args
        assert "countdown" in call_kwargs.kwargs
        assert 0 <= call_kwargs.kwargs["countdown"] <= 30

    @patch("scoring_engine.engine.engine.execute_command")
    def test_jitter_disabled_passes_zero_countdown(self, mock_execute_command):
        """When task_jitter_max_delay == 0 (default), countdown is 0."""
        team = Team(name="Blue Team 1", color="Blue")
        db.session.add(team)
        service = Service(
            name="ICMP Service",
            team=team,
            check_name="ICMPCheck",
            host="127.0.0.1",
        )
        db.session.add(service)
        env = Environment(service=service, matching_content="*")
        db.session.add(env)
        db.session.commit()

        mock_result = MagicMock()
        mock_result.id = "fake-task-id"
        mock_result.state = "SUCCESS"
        mock_result.result = {
            "environment_id": env.id,
            "errored_out": False,
            "output": "*",
            "command": "echo test",
        }
        mock_execute_command.apply_async.return_value = mock_result
        mock_execute_command.AsyncResult.return_value = mock_result

        engine = Engine(total_rounds=1)
        engine.config.task_jitter_max_delay = 0
        engine.run()

        call_kwargs = mock_execute_command.apply_async.call_args
        assert call_kwargs.kwargs["countdown"] == 0

    @patch("scoring_engine.engine.engine.execute_command")
    def test_many_services_many_rounds_performance(self, mock_execute_command):
        """Stress test: 50 services across 10 teams for 10 rounds (500 checks).

        Verifies the engine handles large-scale competitions without N+1
        query degradation or session bloat between rounds.
        """
        num_teams = 10
        services_per_team = 5
        num_rounds = 10

        teams = []
        envs = []
        for t in range(num_teams):
            team = Team(name=f"Blue Team {t + 1}", color="Blue")
            db.session.add(team)
            teams.append(team)
        db.session.flush()

        for team in teams:
            for s in range(services_per_team):
                service = Service(
                    name=f"Service{s}",
                    team=team,
                    check_name="ICMPCheck",
                    host=f"10.0.{team.id}.{s + 1}",
                )
                db.session.add(service)
                db.session.flush()
                env = Environment(service=service, matching_content="*")
                db.session.add(env)
                envs.append(env)
        db.session.commit()

        # Build a lookup so each apply_async call returns a unique mock result
        # keyed by environment ID
        env_results = {}
        task_counter = [0]

        def fake_apply_async(args=None, queue=None, countdown=0):
            job = args[0]
            task_counter[0] += 1
            task_id = f"task-{task_counter[0]}"
            mock_result = MagicMock()
            mock_result.id = task_id
            mock_result.state = "SUCCESS"
            mock_result.result = {
                "environment_id": job["environment_id"],
                "errored_out": False,
                "output": "*",
                "command": "echo test",
            }
            env_results[task_id] = mock_result
            return mock_result

        mock_execute_command.apply_async.side_effect = fake_apply_async
        mock_execute_command.AsyncResult.side_effect = lambda tid: env_results.get(tid, MagicMock(state="SUCCESS", result=None))

        engine = Engine(total_rounds=num_rounds)
        engine.run()

        assert engine.rounds_run == num_rounds
        assert engine.current_round == num_rounds

        # Verify all checks were created
        from scoring_engine.models.check import Check
        from scoring_engine.models.round import Round

        total_services = num_teams * services_per_team
        assert db.session.query(Round).count() == num_rounds
        assert db.session.query(Check).count() == total_services * num_rounds

    # todo figure out how to test the remaining functionality of engine
    # where we're waiting for the worker queues to finish and everything


TASK_KEY_PREFIX = "celery-task-meta-"


class FakeResultBackend:
    """Minimal stand-in for a Celery key/value result backend.

    Stores already-decoded meta dicts keyed by task id and records every
    ``mget`` call so tests can assert on the number of round-trips.
    """

    def __init__(self, metas=None):
        self.store = dict(metas or {})
        self.mget_calls = []

    def get_key_for_task(self, task_id):
        return TASK_KEY_PREFIX + task_id

    def mget(self, keys):
        keys = list(keys)
        self.mget_calls.append(keys)
        prefix_len = len(TASK_KEY_PREFIX)
        return [self.store.get(key[prefix_len:]) for key in keys]

    def decode_result(self, payload):
        return payload


def _success_meta(environment_id, output="*", command="echo test"):
    return {
        "status": "SUCCESS",
        "result": {
            "environment_id": environment_id,
            "errored_out": False,
            "output": output,
            "command": command,
        },
    }


def _make_service(team_name="Blue Team 1", service_name="ICMP Service"):
    team = db.session.query(Team).filter(Team.name == team_name).first()
    if team is None:
        team = Team(name=team_name, color="Blue")
        db.session.add(team)
    service = Service(name=service_name, team=team, check_name="ICMPCheck", host="127.0.0.1")
    db.session.add(service)
    env = Environment(service=service, matching_content="*")
    db.session.add(env)
    db.session.commit()
    return env


class TestBatchedResultFetching:
    """The engine must ask the result backend about many tasks at once."""

    @pytest.fixture(autouse=True)
    def setup(self, db_session):
        for name in ("target_round_time", "worker_refresh_time"):
            setting = Setting.get_setting(name)
            setting.value = 0
            db.session.add(setting)
        db.session.commit()

    @patch("scoring_engine.engine.engine.execute_command")
    def test_fetch_task_metas_uses_one_backend_call(self, mock_execute_command):
        backend = FakeResultBackend(
            {
                "t1": _success_meta(7),
                "t2": {"status": "FAILURE", "result": "kaboom"},
            }
        )
        mock_execute_command.backend = backend

        engine = Engine()
        metas = engine._fetch_task_metas(["t1", "t2", "t3"])

        assert len(backend.mget_calls) == 1
        assert backend.mget_calls[0] == [
            TASK_KEY_PREFIX + "t1",
            TASK_KEY_PREFIX + "t2",
            TASK_KEY_PREFIX + "t3",
        ]
        assert metas["t1"]["status"] == "SUCCESS"
        assert metas["t1"]["result"]["environment_id"] == 7
        # Only SUCCESS tasks carry a result payload, same as before
        assert metas["t2"] == {"status": "FAILURE", "result": None}
        # A task the backend has never heard of is PENDING
        assert metas["t3"] == {"status": "PENDING", "result": None}
        mock_execute_command.AsyncResult.assert_not_called()

    @patch("scoring_engine.engine.engine.execute_command")
    def test_fetch_task_metas_chunks_large_batches(self, mock_execute_command):
        backend = FakeResultBackend()
        mock_execute_command.backend = backend

        engine = Engine()
        task_ids = ["task-{0}".format(i) for i in range(1200)]
        metas = engine._fetch_task_metas(task_ids)

        assert [len(call) for call in backend.mget_calls] == [500, 500, 200]
        assert len(metas) == 1200
        assert all(meta["status"] == "PENDING" for meta in metas.values())

    @patch("scoring_engine.engine.engine.execute_command")
    def test_fetch_task_metas_deduplicates_ids(self, mock_execute_command):
        backend = FakeResultBackend({"t1": _success_meta(1)})
        mock_execute_command.backend = backend

        engine = Engine()
        metas = engine._fetch_task_metas(["t1", "t1", "t2"])

        assert backend.mget_calls[0] == [TASK_KEY_PREFIX + "t1", TASK_KEY_PREFIX + "t2"]
        assert set(metas) == {"t1", "t2"}

    @patch("scoring_engine.engine.engine.execute_command")
    def test_undecodable_payload_is_treated_as_failed(self, mock_execute_command):
        class BrokenBackend(FakeResultBackend):
            def decode_result(self, payload):
                raise ValueError("not deserializable")

        mock_execute_command.backend = BrokenBackend({"t1": "garbage"})

        engine = Engine()
        metas = engine._fetch_task_metas(["t1"])

        # Not PENDING: a payload we cannot read will never become readable, so
        # the engine must not sit and wait for it.
        assert metas["t1"] == {"status": "FAILURE", "result": None}

    @patch("scoring_engine.engine.engine.execute_command")
    def test_all_pending_tasks_batches_across_teams(self, mock_execute_command):
        backend = FakeResultBackend(
            {
                "t1": _success_meta(1),
                "t3": {"status": "REVOKED", "result": None},
            }
        )
        mock_execute_command.backend = backend

        engine = Engine()
        tasks = {"Team1": ["t1", "t2"], "Team2": ["t3", "t4"]}
        completed = set()
        metas = {}

        pending = engine.all_pending_tasks(tasks, completed, metas)

        assert pending == ["t2", "t4"]
        assert completed == {"t1", "t3"}
        assert metas["t1"]["result"]["environment_id"] == 1
        assert metas["t3"]["status"] == "REVOKED"
        # 4 tasks across 2 teams, still a single backend round-trip
        assert len(backend.mget_calls) == 1

    @patch("scoring_engine.engine.engine.execute_command")
    def test_all_pending_tasks_only_asks_about_outstanding_tasks(self, mock_execute_command):
        backend = FakeResultBackend({"t1": _success_meta(1)})
        mock_execute_command.backend = backend

        engine = Engine()
        tasks = {"Team1": ["t1", "t2"], "Team2": ["t3"]}
        completed = set()
        metas = {}

        assert engine.all_pending_tasks(tasks, completed, metas) == ["t2", "t3"]

        backend.store["t2"] = _success_meta(2)
        assert engine.all_pending_tasks(tasks, completed, metas) == ["t3"]

        # The second poll skipped the already completed t1
        assert backend.mget_calls[1] == [TASK_KEY_PREFIX + "t2", TASK_KEY_PREFIX + "t3"]
        assert completed == {"t1", "t2"}

    @patch("scoring_engine.engine.engine.execute_command")
    def test_non_terminal_states_count_as_done_but_are_not_cached(self, mock_execute_command):
        """STARTED/RETRY are 'not PENDING' (as before) but their meta can still change."""
        backend = FakeResultBackend({"t1": {"status": "STARTED", "result": None}})
        mock_execute_command.backend = backend

        engine = Engine()
        completed = set()
        metas = {}

        assert engine.all_pending_tasks({"Team1": ["t1"]}, completed, metas) == []
        assert completed == {"t1"}
        assert "t1" not in metas

    @patch("scoring_engine.engine.engine.execute_command")
    def test_falls_back_to_async_result_when_backend_cannot_mget(self, mock_execute_command):
        class NoMgetBackend(FakeResultBackend):
            def mget(self, keys):
                raise NotImplementedError("Does not support get_many")

        mock_execute_command.backend = NoMgetBackend()
        mock_execute_command.AsyncResult.side_effect = lambda tid: MagicMock(
            state="SUCCESS", result={"task_id": tid}
        )

        engine = Engine()
        metas = engine._fetch_task_metas(["t1", "t2"])

        assert engine._batch_result_fetch is False
        assert metas["t1"] == {"status": "SUCCESS", "result": {"task_id": "t1"}}
        assert mock_execute_command.AsyncResult.call_count == 2

        # The unsupported backend is remembered - no repeat attempts
        engine._fetch_task_metas(["t3"])
        assert mock_execute_command.AsyncResult.call_count == 3

    @patch("scoring_engine.engine.engine.execute_command")
    def test_backend_errors_still_propagate(self, mock_execute_command):
        class BrokenBackend(FakeResultBackend):
            def mget(self, keys):
                raise ConnectionError("redis is gone")

        mock_execute_command.backend = BrokenBackend()

        engine = Engine()
        with pytest.raises(ConnectionError):
            engine._fetch_task_metas(["t1"])
        assert engine._batch_result_fetch is True

    @patch("scoring_engine.engine.engine.execute_command")
    def test_round_scores_results_without_per_task_lookups(self, mock_execute_command):
        envs = [_make_service(service_name="Service{0}".format(i)) for i in range(3)]
        backend = FakeResultBackend()
        mock_execute_command.backend = backend

        counter = itertools.count()

        def fake_apply_async(args=None, queue=None, countdown=0):
            job = args[0]
            task_id = "task-{0}".format(next(counter))
            backend.store[task_id] = _success_meta(job["environment_id"])
            return MagicMock(id=task_id)

        mock_execute_command.apply_async.side_effect = fake_apply_async

        engine = Engine(total_rounds=1)
        engine.run()

        assert mock_execute_command.AsyncResult.call_count == 0
        # A single poll saw everything finished, and the result processing
        # reused those metas instead of asking again.
        assert len(backend.mget_calls) == 1
        assert len(backend.mget_calls[0]) == len(envs)

        checks = db.session.query(Check).all()
        assert len(checks) == len(envs)
        assert all(check.result is True for check in checks)
        assert all(check.reason == CHECK_SUCCESS_TEXT for check in checks)

    @patch("scoring_engine.engine.engine.execute_command")
    def test_stuck_tasks_are_revoked_in_one_call_and_marked_timed_out(self, mock_execute_command):
        envs = [_make_service(service_name="Service{0}".format(i)) for i in range(3)]
        backend = FakeResultBackend()  # nothing ever completes
        mock_execute_command.backend = backend

        task_ids = []

        def fake_apply_async(args=None, queue=None, countdown=0):
            task_id = "stuck-{0}".format(len(task_ids))
            task_ids.append(task_id)
            return MagicMock(id=task_id)

        mock_execute_command.apply_async.side_effect = fake_apply_async

        engine = Engine(total_rounds=1)
        # target_round_time is 0 in this fixture, so dropping the floor makes
        # the hard ceiling trip on the first pass through the wait loop.
        engine.ROUND_WAIT_FLOOR = 0
        engine.run()

        mock_execute_command.app.control.revoke.assert_called_once()
        revoke_args, revoke_kwargs = mock_execute_command.app.control.revoke.call_args
        assert sorted(revoke_args[0]) == sorted(task_ids)
        assert revoke_kwargs["terminate"] is True

        checks = db.session.query(Check).all()
        assert len(checks) == len(envs)
        assert all(check.result is False for check in checks)
        assert all(check.reason == CHECK_TIMED_OUT_TEXT for check in checks)
        # The round still completed and was recorded
        assert db.session.query(Round).count() == 1


class TestFailedRoundRecovery:
    """A failed round must be rolled back, not kill the engine process."""

    @pytest.fixture(autouse=True)
    def setup(self, db_session):
        for name in ("target_round_time", "worker_refresh_time"):
            setting = Setting.get_setting(name)
            setting.value = 0
            db.session.add(setting)
        db.session.commit()

    @staticmethod
    def _wire_successful_tasks(mock_execute_command):
        backend = FakeResultBackend()
        mock_execute_command.backend = backend
        counter = itertools.count()

        def fake_apply_async(args=None, queue=None, countdown=0):
            job = args[0]
            task_id = "task-{0}".format(next(counter))
            backend.store[task_id] = _success_meta(job["environment_id"])
            return MagicMock(id=task_id)

        mock_execute_command.apply_async.side_effect = fake_apply_async
        return backend

    @staticmethod
    def _break_check_commit(monkeypatch, keep_failing=False):
        """Make the commit that writes the round's checks blow up."""
        real_commit = db.session.commit
        state = {"tripped": False}

        def flaky_commit():
            if state["tripped"] and keep_failing:
                raise RuntimeError("database went away")
            if not state["tripped"] and any(isinstance(obj, Check) for obj in db.session.new):
                state["tripped"] = True
                raise RuntimeError("database went away")
            return real_commit()

        monkeypatch.setattr(db.session, "commit", flaky_commit)
        return state

    @patch("scoring_engine.engine.engine.execute_command")
    def test_failed_round_leaves_no_partial_data_and_does_not_exit(self, mock_execute_command, monkeypatch):
        _make_service()
        self._wire_successful_tasks(mock_execute_command)
        state = self._break_check_commit(monkeypatch)

        engine = Engine(total_rounds=1)
        engine.CLEANUP_RETRY_DELAY = 0
        # Must not raise SystemExit
        engine.run()

        assert state["tripped"] is True
        assert engine.rounds_run == 1
        assert engine.round_running is False
        # Nothing at all from the failed round survives
        assert db.session.query(Round).count() == 0
        assert db.session.query(Check).count() == 0
        assert db.session.query(KB).count() == 0

    @patch("scoring_engine.engine.engine.execute_command")
    def test_engine_continues_to_next_round_after_failure(self, mock_execute_command, monkeypatch):
        _make_service()
        self._wire_successful_tasks(mock_execute_command)
        self._break_check_commit(monkeypatch)

        engine = Engine(total_rounds=2)
        engine.CLEANUP_RETRY_DELAY = 0
        engine.run()

        assert engine.rounds_run == 2
        rounds = db.session.query(Round).all()
        # Round 1 failed and was discarded, so its number is reused
        assert [r.number for r in rounds] == [1]
        assert engine.current_round == 1
        assert db.session.query(Check).count() == 1
        assert db.session.query(KB).count() == 1

    @patch("scoring_engine.engine.engine.execute_command")
    def test_cleanup_failure_is_logged_and_engine_survives(self, mock_execute_command, monkeypatch):
        _make_service()
        self._wire_successful_tasks(mock_execute_command)
        self._break_check_commit(monkeypatch, keep_failing=True)

        engine = Engine(total_rounds=1)
        engine.CLEANUP_RETRY_DELAY = 0

        with patch("scoring_engine.engine.engine.logger") as mock_logger:
            engine.run()
            assert mock_logger.critical.called

        assert engine.rounds_run == 1

    def test_cleanup_removes_round_checks_and_kb(self):
        env = _make_service()
        round_obj = Round(round_start=datetime.now(), number=4)
        db.session.add(round_obj)
        other_round = Round(round_start=datetime.now(), number=3)
        db.session.add(other_round)
        db.session.commit()
        db.session.add(Check(service=env.service, round=round_obj))
        db.session.add(Check(service=env.service, round=other_round))
        db.session.add(KB(name="task_ids", value="{}", round_num=4))
        db.session.add(KB(name="task_ids", value="{}", round_num=3))
        db.session.commit()

        engine = Engine()
        assert engine._cleanup_failed_round(4) is True

        assert [r.number for r in db.session.query(Round).all()] == [3]
        assert db.session.query(Check).count() == 1
        assert [kb.round_num for kb in db.session.query(KB).all()] == [3]

    def test_cleanup_is_a_noop_when_nothing_was_written(self):
        engine = Engine()
        assert engine._cleanup_failed_round(99) is True

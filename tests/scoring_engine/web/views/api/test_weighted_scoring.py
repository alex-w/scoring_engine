"""Tests for weighted scoring (wave 2, phase 3).

Weighted scoring rebalances how service uptime, injects, and flag captures
combine into the scoreboard total. Each weight is a multiplier applied at the
point the categories combine:

- the blue teams' combined total on the scoreboard (service + inject), and
- the red team's flag total on the flags API.

Manual adjustments are never weighted. With the feature disabled every total is
identical to the un-weighted behaviour.
"""

from datetime import datetime, timedelta, timezone

import pytest

from scoring_engine.db import db
from scoring_engine.models.check import Check
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.models.user import User


def _set(name, value):
    setting = Setting.get_setting(name)
    setting.value = value
    db.session.commit()
    Setting.clear_cache(name)


def _enable_weights(service=1.0, inject=1.0, flag=1.0):
    _set("weighted_scoring_enabled", True)
    _set("service_weight", str(service))
    _set("inject_weight", str(inject))
    _set("flag_weight", str(flag))


class TestSlaConfigWeights:
    def test_defaults_when_seeded(self):
        from scoring_engine.sla import get_sla_config

        cfg = get_sla_config()
        assert cfg.weighted_scoring_enabled is False
        assert cfg.service_weight == 1.0
        assert cfg.inject_weight == 1.0
        assert cfg.flag_weight == 1.0

    def test_reads_overrides(self):
        from scoring_engine.sla import get_sla_config

        _enable_weights(service=2.0, inject=0.5, flag=3.0)
        cfg = get_sla_config()
        assert cfg.weighted_scoring_enabled is True
        assert cfg.service_weight == 2.0
        assert cfg.inject_weight == 0.5
        assert cfg.flag_weight == 3.0


class TestWeightedBarData:
    @pytest.fixture(autouse=True)
    def setup(self, test_client, db_session):
        self.client = test_client
        self.white = Team(name="White Team", color="White")
        self.blue = Team(name="Blue Team", color="Blue")
        db.session.add_all([self.white, self.blue])
        db.session.flush()
        self.white_user = User(username="whiteuser", password="testpass", team=self.white)
        db.session.add(self.white_user)
        self.svc = Service(name="SSH", check_name="SSHCheck", host="10.0.0.1", team=self.blue, points=100)
        db.session.add(self.svc)
        db.session.commit()
        # Two passing rounds -> raw service score = 200.
        self._round(1, True)
        self._round(2, True)

    def _round(self, number, result):
        now = datetime.now(timezone.utc)
        r = Round(number=number, round_start=now - timedelta(seconds=60), round_end=now)
        db.session.add(r)
        db.session.flush()
        db.session.add(Check(service=self.svc, round=r, result=result, output=""))
        db.session.commit()
        from scoring_engine.scores import materialize_round

        materialize_round(db.session, r.id, number)

    def _bar(self):
        return self.client.get("/api/scoreboard/get_bar_data").json

    def test_disabled_is_unweighted(self):
        data = self._bar()
        assert data["service_scores"] == ["200"]
        assert data["adjusted_scores"] == ["200"]
        assert data["weighted_scoring_enabled"] is False
        assert "weights" not in data

    def test_service_weight_scales_total(self):
        _enable_weights(service=1.5)
        data = self._bar()
        # 200 service * 1.5 = 300
        assert data["service_scores"] == ["300"]
        assert data["adjusted_scores"] == ["300"]
        assert data["weighted_scoring_enabled"] is True
        assert data["weights"]["service"] == 1.5
        # Raw (pre-weight) value is preserved for the breakdown.
        assert data["raw_service_scores"] == ["200"]

    def test_inject_weight_scales_injects(self):
        from scoring_engine.models.inject import Inject, InjectRubricScore, RubricItem, Template

        _set("inject_scores_visible", True)
        template = Template(
            title="T",
            scenario="s",
            deliverable="d",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.session.add(template)
        db.session.flush()
        rubric = RubricItem(title="r", points=100, template=template)
        db.session.add(rubric)
        db.session.flush()
        inject = Inject(team=self.blue, template=template)
        inject.status = "Graded"
        db.session.add(inject)
        db.session.flush()
        db.session.add(InjectRubricScore(score=100, inject=inject, rubric_item=rubric, grader=self.white_user))
        db.session.commit()

        _enable_weights(service=1.0, inject=2.0)
        data = self._bar()
        # service 200*1.0 + inject 100*2.0 = 400
        assert data["inject_scores"] == ["200"]
        assert data["adjusted_scores"] == ["400"]

    def test_adjustments_are_not_weighted(self):
        from scoring_engine.models.score_adjustment import ScoreAdjustment

        db.session.add(ScoreAdjustment(team_id=self.blue.id, points=100, reason="bonus"))
        db.session.commit()

        _enable_weights(service=2.0)
        data = self._bar()
        # service 200*2.0 = 400, plus un-weighted adjustment 100 = 500
        assert data["adjustments"] == ["100"]
        assert data["adjusted_scores"] == ["500"]


class TestWeightedFlagTotal:
    @pytest.fixture(autouse=True)
    def setup(self, test_client, db_session):
        self.client = test_client

    def test_flag_weight_scales_red_total(self, three_teams):
        from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform, Solve

        blue = three_teams["blue_team"]
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            perm=Perm.user,
            data={"path": "/f", "content": "x"},
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            dummy=False,
        )
        db.session.add(flag)
        db.session.commit()
        db.session.add(Solve(host="h", team=blue, flag=flag))
        db.session.commit()

        from tests.scoring_engine.conftest import _login

        _login(self.client, "whiteuser")

        # Unweighted: one user flag = 100.
        assert self.client.get("/api/flags/score").json["red_total"] == 100

        # flag_weight 2.5 -> 250.
        _enable_weights(flag=2.5)
        assert self.client.get("/api/flags/score").json["red_total"] == 250


class TestWeightedScoringAdminAPI:
    @pytest.fixture(autouse=True)
    def setup(self, white_login):
        self.client, self.teams = white_login

    def test_get_defaults(self):
        data = self.client.get("/api/admin/weighted_scoring").json
        assert data == {"enabled": False, "service_weight": 1.0, "inject_weight": 1.0, "flag_weight": 1.0}

    def test_set_and_read_back(self):
        resp = self.client.post(
            "/api/admin/weighted_scoring",
            json={"enabled": True, "service_weight": 1.5, "inject_weight": 0.5, "flag_weight": 2.0},
        )
        assert resp.status_code == 200
        data = self.client.get("/api/admin/weighted_scoring").json
        assert data["enabled"] is True
        assert data["service_weight"] == 1.5
        assert data["inject_weight"] == 0.5
        assert data["flag_weight"] == 2.0

    def test_rejects_negative_weight(self):
        resp = self.client.post("/api/admin/weighted_scoring", json={"service_weight": -1})
        assert resp.status_code == 400

    def test_rejects_non_numeric_weight(self):
        resp = self.client.post("/api/admin/weighted_scoring", json={"inject_weight": "abc"})
        assert resp.status_code == 400

    def test_requires_white_team(self):
        self.client.get("/logout")
        from tests.scoring_engine.conftest import _login

        _login(self.client, "blueuser")
        assert self.client.get("/api/admin/weighted_scoring").status_code == 403
        assert self.client.post("/api/admin/weighted_scoring", json={"enabled": True}).status_code == 403

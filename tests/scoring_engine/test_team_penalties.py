"""Equivalence test for the batched SLA penalty path (scores.team_penalties).

team_penalties replaces the per-service calculate_team_total_penalties loop with
a handful of grouped queries. It must produce byte-identical penalties, or a
mid-competition deploy would silently change every team's score. This test pins
that: for a fixed dataset and a matrix of SLA configurations, the batched result
equals the per-service oracle for every team.
"""

import pytest

from scoring_engine.db import db
from scoring_engine.models.check import Check
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team

NUM_ROUNDS = 60  # early=1..10, mid=11..49, late=50..60 under the default dynamic config

# service pattern -> list of per-round results (True=pass) across the 60 rounds
PATTERNS = {
    "allpass": [True] * NUM_ROUNDS,
    "allfail": [False] * NUM_ROUNDS,
    "fail_tail_8": [True] * 52 + [False] * 8,  # 8 consecutive failures (over threshold)
    "fail_tail_3": [True] * 57 + [False] * 3,  # 3 consecutive (below default threshold 5)
    "flap": [i % 2 == 0 for i in range(NUM_ROUNDS)],  # ends on a fail (round 60 -> index 59 odd)
    "late_only": [False] * 49 + [True] * 6 + [False] * 5,  # passes only in late rounds, then fails
}
# points chosen so dynamic multipliers produce fractional products (truncation matters)
POINTS = {"allpass": 100, "allfail": 100, "fail_tail_8": 25, "fail_tail_3": 100, "flap": 75, "late_only": 25}


def _set(name, value):
    setting = Setting.get_setting(name)
    setting.value = value
    db.session.commit()
    Setting.clear_cache(name)


def _build_team(name):
    team = Team(name=name, color="Blue")
    db.session.add(team)
    db.session.flush()
    for pname, pattern in PATTERNS.items():
        svc = Service(
            name=f"{name}-{pname}",
            check_name="ICMPCheck",
            host="127.0.0.1",
            team=team,
            points=POINTS[pname],
        )
        db.session.add(svc)
        db.session.flush()
        for rnd, result in zip(_build_team.rounds, pattern):
            db.session.add(Check(service=svc, round=rnd, result=result, completed=True, output=""))
    db.session.commit()
    return team


class TestTeamPenaltiesEquivalence:
    @pytest.fixture(autouse=True)
    def setup(self, db_session):
        _build_team.rounds = []
        for n in range(1, NUM_ROUNDS + 1):
            r = Round(number=n)
            db.session.add(r)
            _build_team.rounds.append(r)
        db.session.commit()
        # Two teams so the per-team grouping is exercised, not just per-service math.
        self.teams = [_build_team("Alpha"), _build_team("Bravo")]

    def _assert_equiv(self, **settings):
        for name, value in settings.items():
            _set(name, value)
        from scoring_engine.scores import recompute_consecutive_failures_cache, team_penalties
        from scoring_engine.sla import calculate_team_total_penalties, get_sla_config

        # team_penalties reads the materialized cache; populate it from the checks
        # these tests built directly (the engine would maintain it in production).
        # The oracle (calculate_team_total_penalties) still scans checks, so the
        # two remain independent.
        recompute_consecutive_failures_cache(db.session)

        cfg = get_sla_config()
        batched = team_penalties(db.session, cfg)
        for team in self.teams:
            oracle = calculate_team_total_penalties(team, cfg)
            got = batched.get(team.id, 0)
            assert got == oracle, f"team {team.name} settings={settings}: batched {got} != oracle {oracle}"
            assert oracle > 0, f"team {team.name} settings={settings}: oracle penalty was 0 (not exercised)"

    def test_disabled_returns_empty(self):
        _set("sla_enabled", False)
        from scoring_engine.scores import team_penalties
        from scoring_engine.sla import get_sla_config

        assert team_penalties(db.session, get_sla_config()) == {}

    def test_additive_static(self):
        self._assert_equiv(sla_enabled=True, dynamic_scoring_enabled=False, sla_penalty_mode="additive")

    def test_additive_dynamic(self):
        self._assert_equiv(sla_enabled=True, dynamic_scoring_enabled=True, sla_penalty_mode="additive")

    def test_flat_dynamic(self):
        self._assert_equiv(sla_enabled=True, dynamic_scoring_enabled=True, sla_penalty_mode="flat")

    def test_exponential_dynamic(self):
        self._assert_equiv(sla_enabled=True, dynamic_scoring_enabled=True, sla_penalty_mode="exponential")

    def test_allow_negative_uncapped(self):
        self._assert_equiv(
            sla_enabled=True,
            dynamic_scoring_enabled=True,
            sla_penalty_mode="exponential",
            sla_allow_negative=True,
        )

    def test_lower_threshold(self):
        self._assert_equiv(sla_enabled=True, dynamic_scoring_enabled=True, sla_penalty_threshold="2")

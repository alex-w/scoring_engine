"""Tests for red-team flag scoring (wave 2, phase 4).

A captured flag is a non-dummy Solve (a blue team's agent reported a red flag
present on one of its hosts). Each is worth flag_points_user or flag_points_root
by the flag's permission level; the total accrues to the red team, and a blue
team's own score is unaffected ("add to red only").
"""

from datetime import datetime, timedelta, timezone

from scoring_engine.db import db
from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform, Solve
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.scores import flag_points_by_team, get_flag_point_values, red_team_flag_total


def _flag(perm, dummy=False):
    flag = Flag(
        type=FlagTypeEnum.file,
        platform=Platform.nix,
        perm=perm,
        data={"path": "/tmp/f", "content": "x"},
        start_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        dummy=dummy,
    )
    db.session.add(flag)
    db.session.commit()
    return flag


def _solve(flag, team, host):
    solve = Solve(host=host, team=team, flag=flag)
    db.session.add(solve)
    db.session.commit()
    return solve


class TestGetFlagPointValues:
    def test_defaults_from_seeded_settings(self):
        assert get_flag_point_values() == (100, 200)

    def test_reads_overridden_settings(self):
        for name, value in (("flag_points_user", "5"), ("flag_points_root", "50")):
            setting = Setting.get_setting(name)
            setting.value = value
            db.session.commit()
            Setting.clear_cache(name)
        assert get_flag_point_values() == (5, 50)


class TestFlagPointsByTeam:
    def test_sums_by_permission_level(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        user_flag = _flag(Perm.user)
        root_flag = _flag(Perm.root)
        _solve(user_flag, team, "host-a")
        _solve(user_flag, team, "host-b")  # same flag, different host -> another capture
        _solve(root_flag, team, "host-a")

        # 2 user captures * 100 + 1 root capture * 200 = 400
        assert flag_points_by_team(db.session) == {team.id: 400}

    def test_dummy_flags_excluded(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        _solve(_flag(Perm.user), team, "host-a")
        _solve(_flag(Perm.root, dummy=True), team, "host-b")  # dummy: not scored
        assert flag_points_by_team(db.session) == {team.id: 100}

    def test_no_solves_is_empty(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        assert flag_points_by_team(db.session) == {}

    def test_explicit_point_values_override_config(self):
        team = Team(name="Blue 1", color="Blue")
        db.session.add(team)
        db.session.commit()
        _solve(_flag(Perm.root), team, "host-a")
        assert flag_points_by_team(db.session, user_points=1, root_points=7) == {team.id: 7}


class TestRedTeamFlagTotal:
    def test_total_is_sum_across_all_blue_teams(self):
        blue1 = Team(name="Blue 1", color="Blue")
        blue2 = Team(name="Blue 2", color="Blue")
        db.session.add_all([blue1, blue2])
        db.session.commit()
        user_flag = _flag(Perm.user)
        root_flag = _flag(Perm.root)
        _solve(user_flag, blue1, "h1")  # 100
        _solve(root_flag, blue1, "h1")  # 200
        _solve(user_flag, blue2, "h1")  # 100

        assert red_team_flag_total(db.session) == 400

    def test_zero_when_nothing_captured(self):
        db.session.add(Team(name="Blue 1", color="Blue"))
        db.session.commit()
        assert red_team_flag_total(db.session) == 0

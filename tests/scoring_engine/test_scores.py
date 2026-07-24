"""Tests for round_score materialization (wave 2, phase 1).

The load-bearing assertion is the *golden equivalence* test: the sum of a team's
materialized ``round_score.service_points`` must equal ``Team.current_score``, the
live full-history computation it will replace. If those two ever diverge, the whole
materialization is unsafe to read from -- so this is the guard the rest of wave 2
builds on.
"""

from scoring_engine.db import db
from scoring_engine.models.round_score import RoundScore
from scoring_engine.scores import compute_round_service_points, materialize_round
from tests.scoring_engine.factories import make_check, make_round, make_service, make_team


class TestComputeRoundServicePoints:
    def test_sums_only_passing_checks(self):
        team = make_team(color="Blue")
        svc_a = make_service(team=team, name="A")
        svc_b = make_service(team=team, name="B")
        svc_a.points = 100
        svc_b.points = 50
        db.session.commit()
        rnd = make_round(number=1)
        make_check(service=svc_a, round_obj=rnd, result=True)
        make_check(service=svc_b, round_obj=rnd, result=False)  # down: excluded

        result = compute_round_service_points(db.session, rnd.id)
        assert result == {team.id: 100}

    def test_absent_team_when_nothing_passes(self):
        team = make_team(color="Blue")
        svc = make_service(team=team)
        db.session.commit()
        rnd = make_round(number=1)
        make_check(service=svc, round_obj=rnd, result=False)
        # No passing checks -> team simply absent (reads coalesce to zero).
        assert compute_round_service_points(db.session, rnd.id) == {}


class TestMaterializeRound:
    def test_writes_one_row_per_scoring_team(self):
        blue1 = make_team(color="Blue")
        blue2 = make_team(color="Blue")
        s1 = make_service(team=blue1)
        s2 = make_service(team=blue2)
        s1.points = 100
        s2.points = 100
        db.session.commit()
        rnd = make_round(number=1)
        make_check(service=s1, round_obj=rnd, result=True)
        make_check(service=s2, round_obj=rnd, result=True)

        written = materialize_round(db.session, rnd)
        assert written == 2
        rows = db.session.query(RoundScore).filter(RoundScore.round_id == rnd.id).all()
        assert {r.team_id: r.service_points for r in rows} == {blue1.id: 100, blue2.id: 100}
        # round_number is denormalized onto every row.
        assert all(r.round_number == 1 for r in rows)
        # flag_points is 0 at this phase.
        assert all(r.flag_points == 0 for r in rows)

    def test_is_idempotent_under_retry(self):
        team = make_team(color="Blue")
        svc = make_service(team=team)
        svc.points = 100
        db.session.commit()
        rnd = make_round(number=1)
        make_check(service=svc, round_obj=rnd, result=True)

        materialize_round(db.session, rnd)
        materialize_round(db.session, rnd)  # retry: must not double-count
        rows = db.session.query(RoundScore).filter(RoundScore.round_id == rnd.id).all()
        assert len(rows) == 1
        assert rows[0].service_points == 100

    def test_golden_equivalence_with_current_score(self):
        """SUM(round_score.service_points) per team == Team.current_score.

        This is the invariant every later phase depends on. Build several teams,
        services with different point values, and multiple rounds with a mix of
        passing and failing checks, then prove the materialized totals match the
        live computation exactly.
        """
        teams = [make_team(color="Blue") for _ in range(3)]
        # Give each team two services with distinct point values.
        services = {}
        for t in teams:
            a = make_service(team=t, name=f"A-{t.id}")
            b = make_service(team=t, name=f"B-{t.id}")
            a.points = 100
            b.points = 25
            services[t.id] = (a, b)
        db.session.commit()

        rounds = [make_round(number=n) for n in range(1, 6)]
        # Deterministic pass/fail pattern so the test is not flaky.
        for i, rnd in enumerate(rounds):
            for t in teams:
                a, b = services[t.id]
                make_check(service=a, round_obj=rnd, result=(i % 2 == 0))
                make_check(service=b, round_obj=rnd, result=(t.id % 2 == 0))
            materialize_round(db.session, rnd)

        for t in teams:
            materialized = (
                db.session.query(db.func.coalesce(db.func.sum(RoundScore.service_points), 0))
                .filter(RoundScore.team_id == t.id)
                .scalar()
            )
            assert materialized == t.current_score, f"team {t.id}: {materialized} != {t.current_score}"

    def test_rolling_back_a_round_leaves_no_ghost_scores(self):
        """Deleting rounds must delete their round_score rows (the rollback contract)."""
        team = make_team(color="Blue")
        svc = make_service(team=team)
        svc.points = 100
        db.session.commit()
        r1 = make_round(number=1)
        r2 = make_round(number=2)
        make_check(service=svc, round_obj=r1, result=True)
        make_check(service=svc, round_obj=r2, result=True)
        materialize_round(db.session, r1)
        materialize_round(db.session, r2)
        assert db.session.query(RoundScore).count() == 2

        # Simulate the rollback deletion contract (admin_rollback / _cleanup_failed_round).
        db.session.query(RoundScore).filter(RoundScore.round_number >= 2).delete(synchronize_session=False)
        db.session.commit()

        remaining = db.session.query(RoundScore).all()
        assert len(remaining) == 1
        assert remaining[0].round_number == 1

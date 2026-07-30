"""Tests for the materialized SLA consecutive-failure cache.

``Service.consecutive_failures_cache`` is maintained by the engine at round close
via ``scores.apply_consecutive_failures`` (incremental, in memory) and repaired /
backfilled via ``scores.recompute_consecutive_failures_cache`` (from checks). It
must always equal ``sla.get_consecutive_failures`` (the check-scanning source of
truth), or the batched penalty read (``team_penalties``) would score differently
from the single-service path.

These pin that invariant across round-by-round maintenance (pass resets, fail
increments, a round with no check, multiple failing checks in one round), a full
recompute, and a round rollback.
"""

from scoring_engine.db import db
from scoring_engine.models.check import Check
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from scoring_engine.scores import apply_consecutive_failures, recompute_consecutive_failures_cache
from scoring_engine.sla import get_consecutive_failures


def _cache(service_id):
    return db.session.query(Service.consecutive_failures_cache).filter(Service.id == service_id).scalar()


def _assert_cache_matches_scan(service_ids):
    for sid in service_ids:
        assert _cache(sid) == get_consecutive_failures(sid), f"service {sid}: cache {_cache(sid)} != scan"


class TestConsecutiveFailuresCache:
    def _setup_services(self, n=3):
        team = Team(name="Blue", color="Blue")
        db.session.add(team)
        db.session.flush()
        services = []
        for i in range(n):
            svc = Service(name=f"s{i}", check_name="ICMPCheck", host="127.0.0.1", team=team, points=100)
            db.session.add(svc)
            services.append(svc)
        db.session.commit()
        return services

    def _run_round(self, number, results):
        """Simulate the engine closing a round.

        ``results`` maps a Service to its per-check results this round: True/False
        for a single check, or a list for multiple checks (multi-environment).
        A service absent from ``results`` is simply not checked this round.
        """
        rnd = Round(number=number)
        db.session.add(rnd)
        db.session.flush()
        pass_ids = set()
        fail_counts = {}
        for svc, res in results.items():
            outcomes = res if isinstance(res, list) else [res]
            for outcome in outcomes:
                db.session.add(Check(service=svc, round=rnd, result=outcome, completed=True, output=""))
                if outcome:
                    pass_ids.add(svc.id)
                else:
                    fail_counts[svc.id] = fail_counts.get(svc.id, 0) + 1
        apply_consecutive_failures(db.session, pass_ids, fail_counts)
        db.session.commit()
        return rnd

    def test_incremental_maintenance_matches_scan(self):
        a, b, c = self._setup_services(3)
        ids = [a.id, b.id, c.id]

        self._run_round(1, {a: True, b: False, c: False})  # a=0 b=1 c=1
        _assert_cache_matches_scan(ids)
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (0, 1, 1)

        self._run_round(2, {a: False, b: False, c: True})  # a=1 b=2 c=0
        _assert_cache_matches_scan(ids)
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (1, 2, 0)

        self._run_round(3, {a: False, b: True})  # c not checked -> unchanged
        _assert_cache_matches_scan(ids)
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (2, 0, 0)

        # c fails twice in one round (two environments) -> streak jumps by 2.
        self._run_round(4, {a: True, b: False, c: [False, False]})  # a=0 b=1 c=2
        _assert_cache_matches_scan(ids)
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (0, 1, 2)

    def test_recompute_matches_scan(self):
        a, b, c = self._setup_services(3)
        ids = [a.id, b.id, c.id]
        # Build history directly (as if the cache had never been maintained).
        for number, results in enumerate(
            [{a: True, b: False, c: False}, {a: False, b: False, c: False}, {a: False, b: True, c: False}], start=1
        ):
            rnd = Round(number=number)
            db.session.add(rnd)
            db.session.flush()
            for svc, res in results.items():
                db.session.add(Check(service=svc, round=rnd, result=res, completed=True, output=""))
        db.session.commit()

        recompute_consecutive_failures_cache(db.session)
        _assert_cache_matches_scan(ids)
        # a failed rounds 2,3; b passed round 3; c failed all three.
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (2, 0, 3)

    def test_recompute_subset_only_touches_given_services(self):
        a, b = self._setup_services(2)
        r = Round(number=1)
        db.session.add(r)
        db.session.flush()
        db.session.add(Check(service=a, round=r, result=False, completed=True, output=""))
        db.session.add(Check(service=b, round=r, result=False, completed=True, output=""))
        db.session.commit()
        recompute_consecutive_failures_cache(db.session, service_ids=[a.id])
        assert _cache(a.id) == 1  # recomputed
        assert _cache(b.id) == 0  # untouched (default), even though it has a failing check

    def test_rollback_repairs_cache(self):
        a, b, c = self._setup_services(3)
        ids = [a.id, b.id, c.id]
        self._run_round(1, {a: False, b: False, c: True})
        r2 = self._run_round(2, {a: False, b: False, c: False})  # a=2 b=2 c=1
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (2, 2, 1)

        # Delete round 2 (rollback) and repair the affected services.
        affected = [row[0] for row in db.session.query(Check.service_id).filter(Check.round_id == r2.id).distinct()]
        db.session.query(Check).filter(Check.round_id == r2.id).delete(synchronize_session=False)
        db.session.query(Round).filter(Round.id == r2.id).delete(synchronize_session=False)
        recompute_consecutive_failures_cache(db.session, service_ids=affected)

        _assert_cache_matches_scan(ids)
        # Back to the round-1 state: a=1 b=1 c=0.
        assert (_cache(a.id), _cache(b.id), _cache(c.id)) == (1, 1, 0)

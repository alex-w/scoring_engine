"""Score materialization and reads.

Wave 2 replaces recompute-from-full-history scoring with a per-round fact table
(``round_score``). This module owns both sides of it:

- Write: :func:`materialize_round` (called by the engine at round close) and
  :func:`materialize_all_rounds` (backfill / non-engine contexts).
- Read: :func:`team_service_scores` (all teams, dynamic-aware, for the
  scoreboard/overview), :func:`all_team_service_scores` (all teams, raw, backing
  ``Team.place``), :func:`team_service_score` (one team's raw total, backing
  ``Team.current_score``), and :func:`team_dynamic_service_score` (one team,
  dynamic multiplier applied, backing the SLA base). ``Team.get_array_of_scores`` /
  ``get_round_scores`` and the scoreboard line chart also read round_score directly.
  These replace the full-history ``JOIN checks`` scans.

Per-service reads (``Service.score_earned``/``rank``) and SLA consecutive-failure
detection still read ``checks`` directly -- round_score is team-grained.
"""

from sqlalchemy import and_, case, or_
from sqlalchemy.sql import func

from scoring_engine.models.check import Check
from scoring_engine.models.round_score import RoundScore
from scoring_engine.models.service import Service


def get_freeze_time():
    """Return the effective scoreboard freeze time as a naive-UTC datetime, or None.

    Two sources, in precedence order:

    1. The manual ``scoreboard_freeze_time`` setting (an ISO string; empty when
       not set). This is the operator's explicit last-hour freeze and wins when
       present -- it freezes the display while the engine keeps scoring.
    2. Otherwise the schedule-derived freeze: between competition windows the
       board shows the last completed window's standings (see
       :func:`scoring_engine.schedule.window_derived_freeze`).

    Normalized to naive UTC to compare against the naive-UTC datetimes the app
    stores (round_end, graded, created_at).
    """
    import pytz
    from dateutil.parser import parse

    from scoring_engine.models.setting import Setting

    setting = Setting.get_setting("scoreboard_freeze_time")
    value = setting.value if setting else None
    if value:
        try:
            dt = parse(str(value))
        except (ValueError, TypeError, OverflowError):
            dt = None
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(pytz.utc).replace(tzinfo=None)
            return dt

    # No explicit freeze -- fall back to the competition schedule.
    from scoring_engine.schedule import window_derived_freeze

    return window_derived_freeze()


def _round_freeze_filter(query, freeze_time):
    """Restrict a round_score query to rounds that closed at/before the freeze."""
    if freeze_time is None:
        return query
    from scoring_engine.models.round import Round

    return query.join(Round, Round.id == RoundScore.round_id).filter(Round.round_end <= freeze_time)


def compute_round_service_points(session, round_id):
    """Return ``{team_id: service_points}`` for a single round.

    ``service_points`` is the sum of ``Service.points`` over the team's *passing*
    checks in this round. Only teams with a non-zero total appear.
    """
    rows = (
        session.query(Service.team_id, func.sum(Service.points))
        .join(Check, Check.service_id == Service.id)
        .filter(Check.round_id == round_id)
        .filter(Check.result.is_(True))
        .group_by(Service.team_id)
        .all()
    )
    return {team_id: int(points or 0) for team_id, points in rows if points}


def materialize_round(session, round_id, round_number, points_by_team=None, clear_existing=True, commit=True):
    """Write ``round_score`` rows for a freshly closed round.

    Writes one row per team that scored something this round; a team with zero
    simply has no row, and reads coalesce a missing ``(team, round)`` to zero.
    ``flag_points`` is written as 0 here -- red-team scoring (phase 4) will
    populate it. The column exists so the schema is stable across phases.

    Takes ``round_id`` / ``round_number`` as plain integers rather than a Round
    object on purpose: the engine's Round was expired by its earlier commit, and
    reading an expired ORM attribute here would trigger a refresh query that
    autoflushes the round's still-pending checks ahead of the single round-close
    commit -- breaking both atomicity and the engine's failure handling. Ints
    can't do that.

    Parameters
    ----------
    points_by_team : dict, optional
        Precomputed ``{team_id: service_points}``. The engine passes the sums it
        already accumulated while processing checks, so the common path runs no
        query at all. When omitted (tests, backfill-equivalence), the sums are
        queried from the checks for ``round_id``.
    clear_existing : bool
        Delete any prior rows for this round first, for idempotency under retry.
        The engine passes ``False`` because a freshly created round has none, and
        skipping the delete avoids an autoflush of the pending checks.

    Returns the number of rows written.
    """
    if clear_existing:
        session.query(RoundScore).filter(RoundScore.round_id == round_id).delete(synchronize_session=False)

    if points_by_team is None:
        points_by_team = compute_round_service_points(session, round_id)

    written = 0
    for team_id, service_points in points_by_team.items():
        if not service_points:
            continue
        session.add(
            RoundScore(
                round_id=round_id,
                round_number=round_number,
                team_id=team_id,
                service_points=service_points,
                flag_points=0,
            )
        )
        written += 1
    if commit:
        session.commit()
    return written


def materialize_all_rounds(session, commit=True):
    """(Re)materialize round_score for every round from the current checks.

    Idempotent per round (clear-and-rewrite). The engine keeps round_score current
    at round close in production; this is for backfill parity and for any context
    that creates checks without the engine (tests, manual imports, a one-off
    recompute after correcting historical data).
    """
    from scoring_engine.models.round import Round

    total = 0
    for round_id, round_number in session.query(Round.id, Round.number).all():
        total += materialize_round(session, round_id, round_number, clear_existing=True, commit=False)
    if commit:
        session.commit()
    return total


def team_service_scores(session, sla_config=None, freeze_time=None):
    """Return ``{team_id: service_score}`` for all teams, read from round_score.

    Replaces the full-history ``SUM(Service.points) JOIN checks GROUP BY team``
    scans on the scoreboard and overview. Dynamic scoring, when enabled, is applied
    per round from the stored raw points and ``round_number`` -- exactly as the old
    per-round path did -- so behaviour is unchanged.

    ``freeze_time`` (naive UTC) restricts the total to rounds that closed at/before
    that instant -- the wall-clock scoreboard freeze.
    """
    from scoring_engine.sla import apply_dynamic_scoring_to_round, get_sla_config

    if sla_config is None:
        sla_config = get_sla_config()

    if not sla_config.dynamic_enabled:
        return all_team_service_scores(session, freeze_time=freeze_time)

    # Dynamic: each round_score row already holds a team's per-round total, so apply
    # the multiplier per row keyed on the stored round_number.
    scores = {}
    query = session.query(RoundScore.team_id, RoundScore.round_number, RoundScore.service_points)
    query = _round_freeze_filter(query, freeze_time)
    for team_id, round_number, service_points in query.all():
        scores[team_id] = scores.get(team_id, 0) + apply_dynamic_scoring_to_round(
            round_number, service_points, sla_config
        )
    return scores


def all_team_service_scores(session, freeze_time=None):
    """Raw ``{team_id: service_score}`` for every team, no dynamic multiplier.

    Backs ``Team.place`` (raw ranking) and the non-dynamic branch of
    :func:`team_service_scores`. ``freeze_time`` restricts to rounds closed at/before
    the freeze.
    """
    query = session.query(RoundScore.team_id, func.sum(RoundScore.service_points))
    query = _round_freeze_filter(query, freeze_time)
    rows = query.group_by(RoundScore.team_id).all()
    return {team_id: int(total or 0) for team_id, total in rows}


def team_service_score(session, team_id, freeze_time=None):
    """Raw (un-weighted, un-multiplied) service score for one team from round_score.

    Matches the historical semantics of ``Team.current_score`` -- callers that want
    dynamic scoring go through :func:`team_service_scores`.
    """
    query = session.query(func.coalesce(func.sum(RoundScore.service_points), 0)).filter(RoundScore.team_id == team_id)
    query = _round_freeze_filter(query, freeze_time)
    return int(query.scalar() or 0)


def team_adjustment_totals(session, freeze_time=None):
    """Return ``{team_id: net_adjustment_points}`` for every team with adjustments.

    Sum of the signed manual adjustments (bonuses positive, penalties negative)
    from the append-only score_adjustment log. Teams with no adjustments are absent
    (reads coalesce to zero). ``freeze_time`` restricts to adjustments created
    at/before the freeze.
    """
    from scoring_engine.models.score_adjustment import ScoreAdjustment

    query = session.query(ScoreAdjustment.team_id, func.sum(ScoreAdjustment.points))
    if freeze_time is not None:
        query = query.filter(ScoreAdjustment.created_at <= freeze_time)
    rows = query.group_by(ScoreAdjustment.team_id).all()
    return {team_id: int(total or 0) for team_id, total in rows}


def team_adjustment_total(session, team_id):
    """Net manual adjustment points for one team (0 if none)."""
    from scoring_engine.models.score_adjustment import ScoreAdjustment

    total = (
        session.query(func.coalesce(func.sum(ScoreAdjustment.points), 0))
        .filter(ScoreAdjustment.team_id == team_id)
        .scalar()
    )
    return int(total or 0)


def get_flag_point_values():
    """Return ``(user_points, root_points)`` for captured flags from settings.

    Admin-tunable mid-competition (like the SLA settings); falls back to the
    documented defaults if a setting is missing or unparseable.
    """
    from scoring_engine.models.setting import Setting

    def _int(name, default):
        setting = Setting.get_setting(name)
        try:
            return int(setting.value) if setting else default
        except (ValueError, TypeError):
            return default

    return _int("flag_points_user", 100), _int("flag_points_root", 200)


def flag_points_by_team(session, user_points=None, root_points=None, freeze_time=None):
    """Return ``{blue_team_id: captured_flag_value}`` -- each team's flag exposure.

    A captured flag is a non-dummy ``Solve`` (a blue team's agent reported a red
    flag present on one of its hosts). Each is worth ``root_points`` when the flag
    is a root flag, else ``user_points``. This is the value the red team earns for
    compromising that team; per the "add to red only" rule the blue team's own
    score is unaffected. ``freeze_time`` restricts to solves recorded at/before the
    freeze.
    """
    from scoring_engine.models.flag import Flag, Solve

    if user_points is None or root_points is None:
        cfg_user, cfg_root = get_flag_point_values()
        user_points = cfg_user if user_points is None else user_points
        root_points = cfg_root if root_points is None else root_points

    query = (
        session.query(Solve.team_id, Flag.perm, func.count())
        .join(Flag, Flag.id == Solve.flag_id)
        .filter(Flag.dummy.is_(False))
    )
    if freeze_time is not None:
        query = query.filter(Solve.created_at <= freeze_time)
    rows = query.group_by(Solve.team_id, Flag.perm).all()
    result = {}
    for team_id, perm, count in rows:
        perm_value = perm.value if hasattr(perm, "value") else perm
        points = root_points if perm_value == "root" else user_points
        result[team_id] = result.get(team_id, 0) + points * count
    return result


def red_team_flag_total(session, user_points=None, root_points=None, freeze_time=None):
    """Total red-team flag score: the sum of every blue team's captured-flag value.

    The red team scores by compromising anyone, so its total is the sum across all
    blue teams (a handful of red teams share this pool today).
    """
    return sum(flag_points_by_team(session, user_points, root_points, freeze_time=freeze_time).values())


def _service_consecutive_failures(session):
    """Return ``{service_id: consecutive_failures}`` for every service, batched.

    Consecutive failures = the run of failing *completed* checks from the most
    recent round backwards, i.e. the completed failures in rounds after the
    service's last passing round (all completed failures if it never passed).
    Equivalent to ``sla.get_consecutive_failures`` per service, in one grouped
    query instead of one-query-per-service.
    """
    last_pass = (
        session.query(Check.service_id.label("sid"), func.max(Check.round_id).label("lpr"))
        .filter(Check.completed.is_(True))
        .filter(Check.result.is_(True))
        .group_by(Check.service_id)
        .subquery()
    )
    rows = (
        session.query(Check.service_id, func.count())
        .outerjoin(last_pass, last_pass.c.sid == Check.service_id)
        .filter(Check.completed.is_(True))
        .filter(Check.result.is_(False))
        .filter(or_(last_pass.c.lpr.is_(None), Check.round_id > last_pass.c.lpr))
        .group_by(Check.service_id)
        .all()
    )
    return {sid: count for sid, count in rows}


def _service_base_scores(session, config, points_by_service, service_ids):
    """Return ``{service_id: earned base score}`` for the given services, batched.

    Only the services passed in ``service_ids`` are queried -- the caller has
    already narrowed to the handful that are actually over the penalty threshold,
    so this never scans the whole (large) checks table for the 99% of services
    that carry no penalty.

    Matches ``sla.calculate_service_base_score_with_dynamic`` exactly:

    - non-dynamic: ``count(passing checks) * points`` (``Service.score_earned``).
    - dynamic: ``sum(int(points * multiplier))`` per passing check, where the
      multiplier is the round bucket (early/mid/late). Because the multiplier is
      constant within a bucket, ``sum`` over a bucket equals
      ``count_bucket * int(points * multiplier)`` -- which reproduces the
      original per-check integer truncation without a non-portable SQL FLOOR.
    """
    if not service_ids:
        return {}

    if not config.dynamic_enabled:
        rows = (
            session.query(Check.service_id, func.count())
            .filter(Check.result.is_(True))
            .filter(Check.service_id.in_(service_ids))
            .group_by(Check.service_id)
            .all()
        )
        return {sid: count * points_by_service.get(sid, 0) for sid, count in rows}

    from scoring_engine.models.round import Round

    early, late = config.early_rounds, config.late_start_round
    # Buckets mirror the original elif chain: <= early wins first, then >= late.
    early_c = func.sum(case((Round.number <= early, 1), else_=0))
    mid_c = func.sum(case((and_(Round.number > early, Round.number < late), 1), else_=0))
    late_c = func.sum(case((and_(Round.number > early, Round.number >= late), 1), else_=0))
    rows = (
        session.query(Check.service_id, early_c, mid_c, late_c)
        .join(Round, Round.id == Check.round_id)
        .filter(Check.result.is_(True))
        .filter(Check.service_id.in_(service_ids))
        .group_by(Check.service_id)
        .all()
    )
    scores = {}
    for sid, e, m, ln in rows:
        p = points_by_service.get(sid, 0)
        # SUM() comes back as Decimal on MySQL/MariaDB (int on SQLite); coerce so
        # the later ``base * (percent / 100)`` is plain int/float arithmetic.
        e, m, ln = int(e or 0), int(m or 0), int(ln or 0)
        scores[sid] = e * int(p * config.early_multiplier) + m * p + ln * int(p * config.late_multiplier)
    return scores


def apply_consecutive_failures(session, pass_service_ids, fail_counts):
    """Fold one round's results into ``Service.consecutive_failures_cache``.

    Called by the engine at round close from the pass/fail results it already
    holds in memory -- so it never queries the round's still-pending checks (which
    would autoflush them ahead of the single atomic round commit). The caller
    wraps this in ``session.no_autoflush`` and lets the round's own commit persist
    the updates atomically with the checks and round_score.

    A service that passed at least once this round resets to 0; a service that
    only failed increments by its number of failing checks this round (the scan
    counts per check, and round_score already scores multi-environment services
    per check, so the streak does too).
    """
    from collections import defaultdict

    if pass_service_ids:
        session.query(Service).filter(Service.id.in_(list(pass_service_ids))).update(
            {Service.consecutive_failures_cache: 0}, synchronize_session=False
        )
    # Group fail-only services by their increment so each distinct count is a
    # single UPDATE (one UPDATE total in the common one-check-per-service case).
    by_increment = defaultdict(list)
    for service_id, count in fail_counts.items():
        if service_id in pass_service_ids:
            continue  # passed at least once -> already reset to 0
        by_increment[count].append(service_id)
    for increment, service_ids in by_increment.items():
        session.query(Service).filter(Service.id.in_(service_ids)).update(
            {Service.consecutive_failures_cache: Service.consecutive_failures_cache + increment},
            synchronize_session=False,
        )


def recompute_consecutive_failures_cache(session, service_ids=None, commit=True):
    """Recompute ``Service.consecutive_failures_cache`` from the checks table.

    The authoritative slow path, for when the incremental in-memory maintenance
    does not apply: backfill (migration / example data loaded without the engine)
    and repair after a round is deleted (engine or admin rollback). Recomputes for
    all services, or just ``service_ids``.
    """
    from collections import defaultdict

    counts = _service_consecutive_failures(session)

    target = session.query(Service.id)
    if service_ids is not None:
        target = target.filter(Service.id.in_(list(service_ids)))
    target_ids = [sid for (sid,) in target.all()]
    if not target_ids:
        if commit:
            session.commit()
        return

    # Reset the target set, then set the (few) services that have failures --
    # grouped by value so it is a handful of UPDATEs, not one per service.
    session.query(Service).filter(Service.id.in_(target_ids)).update(
        {Service.consecutive_failures_cache: 0}, synchronize_session=False
    )
    target_set = set(target_ids)
    by_value = defaultdict(list)
    for service_id, failures in counts.items():
        if failures and service_id in target_set:
            by_value[failures].append(service_id)
    for value, sids in by_value.items():
        session.query(Service).filter(Service.id.in_(sids)).update(
            {Service.consecutive_failures_cache: value}, synchronize_session=False
        )
    if commit:
        session.commit()


def team_penalties(session, config):
    """Return ``{team_id: total_sla_penalty}`` for every team, batched.

    Replaces the per-service ``sla.calculate_team_total_penalties`` loop -- which
    issued two check queries for *every* service (~20k queries at 100 teams x 100
    services) -- with a read that scans no checks at all for the failure counts:
    they come from the materialized ``Service.consecutive_failures_cache`` (kept
    current by the engine at round close). Produces byte-identical penalties
    (guarded by an equivalence test); teams with no penalty are absent.

    The (small) earned-score scan then runs only for the services actually over
    the penalty threshold -- typically a tiny fraction -- so a service with no
    penalty never has its base score queried.

    Deliberately not freeze-aware, matching the existing penalty path (the frozen
    public scoreboard already shows live penalties).
    """
    if not config.sla_enabled:
        return {}

    from scoring_engine.sla import calculate_sla_penalty_percent

    percents = {}
    points_by_service = {}
    team_by_service = {}
    for service_id, team_id, points, failures in session.query(
        Service.id, Service.team_id, Service.points, Service.consecutive_failures_cache
    ).all():
        percent = calculate_sla_penalty_percent(failures or 0, config)
        if percent > 0:
            percents[service_id] = percent
            points_by_service[service_id] = points
            team_by_service[service_id] = team_id
    if not percents:
        return {}

    penalized_ids = list(percents)
    base_scores = _service_base_scores(session, config, points_by_service, penalized_ids)

    penalties = {}
    for service_id, percent in percents.items():
        penalty = int(base_scores.get(service_id, 0) * (percent / 100))
        if penalty:
            team_id = team_by_service[service_id]
            penalties[team_id] = penalties.get(team_id, 0) + penalty
    return penalties


def team_dynamic_service_score(session, team_id, sla_config):
    """One team's service score with the dynamic multiplier applied per round.

    Reads the team's per-round raw points from round_score and applies the
    multiplier keyed on the stored round_number -- the same result as the old
    full-history GROUP BY over checks.
    """
    from scoring_engine.sla import apply_dynamic_scoring_to_round

    total = 0
    rows = session.query(RoundScore.round_number, RoundScore.service_points).filter(RoundScore.team_id == team_id).all()
    for round_number, service_points in rows:
        total += apply_dynamic_scoring_to_round(round_number, service_points, sla_config)
    return total

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

"""Score materialization and reads.

Wave 2 replaces recompute-from-full-history scoring with a per-round fact table
(``round_score``). This module owns both sides of it:

- Write: :func:`materialize_round` (called by the engine at round close) and
  :func:`materialize_all_rounds` (backfill / non-engine contexts).
- Read: :func:`team_service_scores` (all teams, for the scoreboard/overview),
  :func:`team_service_score` (one team's raw total, backing ``Team.current_score``),
  and :func:`team_dynamic_service_score` (one team, dynamic multiplier applied,
  backing the SLA base). These replace the full-history ``JOIN checks`` scans.

Per-service reads (``Service.score_earned``/``rank``) and SLA consecutive-failure
detection still read ``checks`` directly -- round_score is team-grained.
"""

from sqlalchemy.sql import func

from scoring_engine.models.check import Check
from scoring_engine.models.round_score import RoundScore
from scoring_engine.models.service import Service


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


def team_service_scores(session, sla_config=None):
    """Return ``{team_id: service_score}`` for all teams, read from round_score.

    Replaces the full-history ``SUM(Service.points) JOIN checks GROUP BY team``
    scans on the scoreboard and overview. Dynamic scoring, when enabled, is applied
    per round from the stored raw points and ``round_number`` -- exactly as the old
    per-round path did -- so behaviour is unchanged.
    """
    from scoring_engine.sla import apply_dynamic_scoring_to_round, get_sla_config

    if sla_config is None:
        sla_config = get_sla_config()

    if not sla_config.dynamic_enabled:
        rows = session.query(RoundScore.team_id, func.sum(RoundScore.service_points)).group_by(RoundScore.team_id).all()
        return {team_id: int(total or 0) for team_id, total in rows}

    # Dynamic: each round_score row already holds a team's per-round total, so apply
    # the multiplier per row keyed on the stored round_number.
    scores = {}
    rows = session.query(RoundScore.team_id, RoundScore.round_number, RoundScore.service_points).all()
    for team_id, round_number, service_points in rows:
        scores[team_id] = scores.get(team_id, 0) + apply_dynamic_scoring_to_round(
            round_number, service_points, sla_config
        )
    return scores


def team_service_score(session, team_id):
    """Raw (un-weighted, un-multiplied) service score for one team from round_score.

    Matches the historical semantics of ``Team.current_score`` -- callers that want
    dynamic scoring go through :func:`team_service_scores`.
    """
    total = (
        session.query(func.coalesce(func.sum(RoundScore.service_points), 0))
        .filter(RoundScore.team_id == team_id)
        .scalar()
    )
    return int(total or 0)


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

"""Score materialization.

Wave 2 replaces recompute-from-full-history scoring with a per-round fact table
(``round_score``). This module owns writing those rows. Read helpers that consume
them land in phase 2; for now the only public entry point is
:func:`materialize_round`, called by the engine when a round closes.
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

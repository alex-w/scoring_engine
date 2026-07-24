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


def materialize_round(session, round_obj, commit=True):
    """Write ``round_score`` rows for a freshly closed round.

    Idempotent: safe to call again for the same round (any existing rows for it
    are cleared first), so a retried round-close cannot double-count. Writes one
    row per team that scored something this round; a team with zero simply has no
    row, and reads coalesce a missing ``(team, round)`` to zero.

    ``flag_points`` is written as 0 here -- red-team scoring (phase 4) will
    populate it. The column exists so the schema is stable across phases.

    Returns the number of rows written.
    """
    # Clear any prior rows for this round to stay idempotent under retry.
    session.query(RoundScore).filter(RoundScore.round_id == round_obj.id).delete(synchronize_session=False)

    points_by_team = compute_round_service_points(session, round_obj.id)
    for team_id, service_points in points_by_team.items():
        session.add(
            RoundScore(
                round_id=round_obj.id,
                round_number=round_obj.number,
                team_id=team_id,
                service_points=service_points,
                flag_points=0,
            )
        )
    if commit:
        session.commit()
    return len(points_by_team)

"""Materialized per-team, per-round score facts.

Written once by the engine when a round closes (see engine._materialize_round),
and never updated afterwards -- a corrected round is rolled back and re-run, never
patched in place. This is the fast-read fact table that replaces recomputing every
team's total from the full ``checks`` history on every scoreboard render.

Only components that are known at round close live here:

- ``service_points``: raw uptime points earned this round (sum of ``Service.points``
  for the team's passing checks). "Raw" is deliberate -- weights and the dynamic
  per-round multiplier are policy an admin may tune mid-competition, so they are
  applied at read time over these rows, not baked in.
- ``flag_points``: red-team capture points attributable to this round. The column
  exists now so the schema is complete, but it is populated as 0 until wave-2
  phase 4 (red-team scoring) lands its config and read path.

Rows are written only for teams that scored something (a missing ``(team, round)``
means zero, and reads coalesce accordingly), which keeps the table small and keeps
the live write path consistent with the historical backfill in migration 005.
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from scoring_engine.models.base import Base


class RoundScore(Base):
    __tablename__ = "round_score"
    __table_args__ = (
        # One row per team per round. Guards the idempotent round-close write:
        # a retried close cannot double-count.
        UniqueConstraint("round_id", "team_id", name="_round_score_round_team_uc"),
        # The hot read is "this team's points across rounds", keyed on the
        # denormalized round_number so no join to rounds is needed.
        Index("ix_round_score_team_round", "team_id", "round_number"),
        # Freeze and rollback scan by round.
        Index("ix_round_score_round_number", "round_number"),
    )

    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    # Denormalized copy of rounds.number: reads and freeze filters key on the
    # number, not the id, and it is immutable once written.
    round_number = Column(Integer, nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    service_points = Column(Integer, nullable=False, default=0)
    flag_points = Column(Integer, nullable=False, default=0)

    round = relationship("Round")
    team = relationship("Team")

    def __repr__(self):
        return "<RoundScore team={0} round={1} service={2} flag={3}>".format(
            self.team_id, self.round_number, self.service_points, self.flag_points
        )

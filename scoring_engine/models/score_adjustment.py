"""Manual, white-team score adjustments (wave 2, phase 5).

An append-only audit log: a white-team member grants a bonus or applies a penalty
to a team with a required reason. You never silently edit a competition score --
to reverse an adjustment you add a compensating row, so the trail is complete.

Kept separate from ``round_score`` because it is operator-authored (not engine
materialized) and must survive a rollback of rounds by default (an operator ruling
should not vanish when the engine rolls back service rounds). ``created_at`` is the
basis for the wall-clock scoreboard freeze added in phase 6.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import relationship

from scoring_engine.models.base import Base


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScoreAdjustment(Base):
    __tablename__ = "score_adjustment"
    __table_args__ = (Index("ix_score_adjustment_team_id", "team_id"),)

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    # Signed: positive is a bonus, negative is a penalty.
    points = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    # Who applied it (nullable so history survives a user deletion).
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    team = relationship("Team")
    author = relationship("User")

    def __repr__(self):
        return "<ScoreAdjustment team={0} points={1:+d}>".format(self.team_id, self.points)

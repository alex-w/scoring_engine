"""Scheduled competition windows (multi-day scheduling).

A competition window is a ``[start_time, end_time)`` interval during which the
engine is meant to be scoring. Between windows -- overnight, between competition
days -- the engine idles instead of burning resources probing hosts that are
powered down or firewalled off, and the public scoreboard freezes to the end of
the last completed window.

Semantics live in :mod:`scoring_engine.schedule`; this is just the storage:

- Times are stored naive-UTC, exactly like ``rounds.round_end`` and the
  ``scoreboard_freeze_time`` setting, so the engine's ``_utcnow()`` and the
  read-time freeze comparisons all speak the same clock.
- ``enabled`` lets an operator park a window (e.g. a cancelled day) without
  losing the row.
- **No rows means no schedule**, which is the historical behaviour: the engine
  runs continuously and only the manual ``scoreboard_freeze_time`` freezes the
  board. Scheduling is opt-in the moment the first window is added.
"""

import pytz
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String

from scoring_engine.config import config
from scoring_engine.datetime_utils import ensure_utc_aware
from scoring_engine.models.base import Base


class CompetitionWindow(Base):
    __tablename__ = "competition_window"
    # The hot query is "windows ordered by start", asked on every scoreboard
    # read (via the cached schedule) and every engine round boundary.
    __table_args__ = (Index("ix_competition_window_start", "start_time"),)

    id = Column(Integer, primary_key=True)
    # Optional human label ("Day 1"); purely cosmetic for the admin list.
    name = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=False)  # naive UTC
    end_time = Column(DateTime, nullable=False)  # naive UTC
    enabled = Column(Boolean, nullable=False, default=True)

    def _localize(self, value):
        return ensure_utc_aware(value).astimezone(pytz.timezone(config.timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")

    @property
    def local_start_time(self):
        return self._localize(self.start_time)

    @property
    def local_end_time(self):
        return self._localize(self.end_time)

    def to_dict(self):
        """Serialize for the admin API.

        ``start_time`` / ``end_time`` are the raw naive-UTC ISO strings (what the
        POST endpoint round-trips); ``*_display`` are pre-localized for the UI.
        """
        return {
            "id": self.id,
            "name": self.name,
            "enabled": bool(self.enabled),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "start_display": self.local_start_time if self.start_time else None,
            "end_display": self.local_end_time if self.end_time else None,
        }

    def __repr__(self):
        return "<CompetitionWindow id={0} name={1} start={2} end={3} enabled={4}>".format(
            self.id, self.name, self.start_time, self.end_time, self.enabled
        )

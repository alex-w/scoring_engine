"""Competition scheduling: when the engine runs and how the board freezes.

Wraps the :class:`~scoring_engine.models.competition_window.CompetitionWindow`
rows in the two decisions the rest of the system needs:

- :func:`engine_should_run` -- gates the engine's round loop. Outside every
  window the engine idles instead of probing hosts that are powered down between
  competition days.
- :func:`window_derived_freeze` -- the display freeze implied by the schedule.
  Between windows the public scoreboard shows the final standings of the last
  completed window; inside a window it is live. This composes with the manual
  ``scoreboard_freeze_time`` (see :func:`scores.get_freeze_time`), which still
  wins when an operator sets an explicit last-hour freeze.

**No windows == no schedule == the historical behaviour**: the engine runs
continuously and the schedule implies no freeze. Scheduling turns on the moment
the first window exists.

The window list is read on every scoreboard request and every engine round
boundary, so it is cached in Redis (60s TTL, mirroring the settings cache) and
invalidated on any window mutation via :func:`clear_windows_cache`.
"""

import json
import logging
from datetime import datetime, timezone

# Reuse the exact Redis client factory the settings cache uses, so schedule and
# settings share connection behaviour and the "Redis disabled -> query the DB"
# fallback (tests, cache_type != redis).
from scoring_engine.models.setting import _get_redis

logger = logging.getLogger(__name__)

CACHE_KEY = "schedule:windows"
CACHE_TTL = 60


def _naive_utcnow():
    """Current time as a naive-UTC datetime (matches how the engine stores times)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _query_windows(session=None):
    """Enabled windows from the DB as ``{"id","name","start","end"}`` dicts, sorted."""
    from scoring_engine.models.competition_window import CompetitionWindow

    if session is None:
        from scoring_engine.db import db

        session = db.session
    rows = (
        session.query(CompetitionWindow)
        .filter(CompetitionWindow.enabled.is_(True))
        .order_by(CompetitionWindow.start_time)
        .all()
    )
    return [{"id": w.id, "name": w.name, "start": w.start_time, "end": w.end_time} for w in rows]


def _row_to_json(w):
    return {"id": w["id"], "name": w["name"], "start": w["start"].isoformat(), "end": w["end"].isoformat()}


def _row_from_json(d):
    return {
        "id": d["id"],
        "name": d["name"],
        "start": datetime.fromisoformat(d["start"]),
        "end": datetime.fromisoformat(d["end"]),
    }


def get_windows(session=None):
    """Return enabled competition windows, sorted by start (naive-UTC datetimes).

    Redis-cached (60s) because the scoreboard read path asks for this per request.
    On a cache miss -- or with Redis disabled -- it queries the DB directly.
    Disabled windows are excluded; they exist only so an operator can park a
    window without deleting it.
    """
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(CACHE_KEY)
            if cached is not None:
                return [_row_from_json(row) for row in json.loads(cached)]
        except Exception:
            logger.debug("windows cache read failed", exc_info=True)

    windows = _query_windows(session)

    if r is not None:
        try:
            r.setex(CACHE_KEY, CACHE_TTL, json.dumps([_row_to_json(w) for w in windows]))
        except Exception:
            logger.debug("windows cache write failed", exc_info=True)
    return windows


def clear_windows_cache():
    """Drop the cached window list. Call after any window create/update/delete."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(CACHE_KEY)
    except Exception:
        logger.debug("windows cache clear failed", exc_info=True)


def engine_should_run(now=None, session=None):
    """Whether the engine should run a round right now.

    No windows configured -> always ``True`` (historical always-on behaviour).
    Otherwise ``True`` iff *now* falls inside some window (``start <= now < end``).
    Half-open on the end so a round is never started exactly at a window's close.
    """
    windows = get_windows(session)
    if not windows:
        return True
    if now is None:
        now = _naive_utcnow()
    return any(w["start"] <= now < w["end"] for w in windows)


def window_derived_freeze(now=None, session=None):
    """The display freeze implied by the schedule, or ``None``.

    - No windows, or *now* inside a window -> ``None`` (board is live).
    - *now* before the first window opens -> ``None`` (nothing scored yet, so the
      board is trivially empty).
    - Between/after windows -> the end of the last window that has already closed,
      so the public board shows the last completed window's final standings and
      nothing from the current gap.
    """
    windows = get_windows(session)
    if not windows:
        return None
    if now is None:
        now = _naive_utcnow()
    if any(w["start"] <= now < w["end"] for w in windows):
        return None
    closed_ends = [w["end"] for w in windows if w["end"] <= now]
    if not closed_ends:
        return None  # before the competition's first window
    return max(closed_ends)

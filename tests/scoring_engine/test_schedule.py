"""Tests for competition scheduling (scoring_engine.schedule).

Windows gate the engine (engine_should_run) and imply a display freeze between
windows (window_derived_freeze). With no windows the system behaves exactly as
before: the engine always runs and the schedule implies no freeze.

Redis is disabled in tests (cache_type=null), so get_windows queries the DB
directly -- these tests exercise that path and never touch a cache.
"""

from datetime import datetime

from scoring_engine.db import db
from scoring_engine.models.competition_window import CompetitionWindow
from scoring_engine.schedule import engine_should_run, get_windows, window_derived_freeze


def _mk(start, end, enabled=True, name=None):
    w = CompetitionWindow(name=name, start_time=start, end_time=end, enabled=enabled)
    db.session.add(w)
    db.session.commit()
    return w


# Day 1: 09:00-17:00, Day 2: 09:00-17:00 (naive UTC).
D1_START = datetime(2026, 7, 27, 9, 0, 0)
D1_END = datetime(2026, 7, 27, 17, 0, 0)
D2_START = datetime(2026, 7, 28, 9, 0, 0)
D2_END = datetime(2026, 7, 28, 17, 0, 0)


class TestGetWindows:
    def test_empty_when_none(self):
        assert get_windows(session=db.session) == []

    def test_excludes_disabled_and_sorts(self):
        _mk(D2_START, D2_END, name="Day 2")
        _mk(D1_START, D1_END, name="Day 1")
        _mk(D1_START, D1_END, enabled=False, name="Parked")
        windows = get_windows(session=db.session)
        # Disabled excluded, remaining sorted by start.
        assert [w["name"] for w in windows] == ["Day 1", "Day 2"]
        assert windows[0]["start"] == D1_START
        assert windows[0]["end"] == D1_END


class TestEngineShouldRun:
    def test_no_windows_always_runs(self):
        assert engine_should_run(now=datetime(2026, 1, 1, 3, 0, 0), session=db.session) is True

    def test_inside_window_runs(self):
        _mk(D1_START, D1_END)
        assert engine_should_run(now=datetime(2026, 7, 27, 12, 0, 0), session=db.session) is True

    def test_before_first_window_pauses(self):
        _mk(D1_START, D1_END)
        assert engine_should_run(now=datetime(2026, 7, 27, 8, 0, 0), session=db.session) is False

    def test_between_windows_pauses(self):
        _mk(D1_START, D1_END)
        _mk(D2_START, D2_END)
        assert engine_should_run(now=datetime(2026, 7, 27, 20, 0, 0), session=db.session) is False

    def test_end_is_exclusive(self):
        _mk(D1_START, D1_END)
        # Exactly at close: a round should not start.
        assert engine_should_run(now=D1_END, session=db.session) is False
        # Exactly at open: it should.
        assert engine_should_run(now=D1_START, session=db.session) is True

    def test_disabled_window_does_not_open(self):
        # An enabled Day-2 window plus a disabled Day-1 window: during Day 1's
        # interval the engine stays paused because the disabled window is ignored.
        _mk(D2_START, D2_END, name="Day 2")
        _mk(D1_START, D1_END, enabled=False, name="Day 1 parked")
        assert engine_should_run(now=datetime(2026, 7, 27, 12, 0, 0), session=db.session) is False

    def test_only_disabled_windows_means_no_schedule(self):
        # If every window is parked, there is no active schedule -> always run.
        _mk(D1_START, D1_END, enabled=False)
        assert engine_should_run(now=datetime(2026, 7, 27, 12, 0, 0), session=db.session) is True


class TestWindowDerivedFreeze:
    def test_no_windows_no_freeze(self):
        assert window_derived_freeze(now=datetime(2026, 7, 27, 20, 0, 0), session=db.session) is None

    def test_inside_window_live(self):
        _mk(D1_START, D1_END)
        assert window_derived_freeze(now=datetime(2026, 7, 27, 12, 0, 0), session=db.session) is None

    def test_before_first_window_no_freeze(self):
        _mk(D1_START, D1_END)
        assert window_derived_freeze(now=datetime(2026, 7, 27, 8, 0, 0), session=db.session) is None

    def test_between_windows_freezes_to_last_close(self):
        _mk(D1_START, D1_END)
        _mk(D2_START, D2_END)
        # Overnight gap after day 1: frozen to day 1's close.
        assert window_derived_freeze(now=datetime(2026, 7, 27, 22, 0, 0), session=db.session) == D1_END

    def test_after_all_windows_freezes_to_final_close(self):
        _mk(D1_START, D1_END)
        _mk(D2_START, D2_END)
        assert window_derived_freeze(now=datetime(2026, 7, 29, 0, 0, 0), session=db.session) == D2_END

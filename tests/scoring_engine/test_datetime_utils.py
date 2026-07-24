from datetime import datetime, timedelta, timezone

import pytz

from scoring_engine.datetime_utils import ensure_utc_aware


class TestEnsureUtcAware:
    def test_none_returns_none(self):
        assert ensure_utc_aware(None) is None

    def test_naive_is_assumed_utc(self):
        naive = datetime(2024, 6, 1, 12, 30, 45)
        result = ensure_utc_aware(naive)

        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        # The wall-clock reading is unchanged - naive is interpreted as UTC, not shifted.
        assert result.replace(tzinfo=None) == naive

    def test_naive_with_microseconds_preserved(self):
        naive = datetime(2024, 6, 1, 12, 30, 45, 123456)
        result = ensure_utc_aware(naive)

        assert result.microsecond == 123456
        assert result.replace(tzinfo=None) == naive

    def test_aware_non_utc_is_converted_to_utc(self):
        eastern = pytz.timezone("US/Eastern")
        # 2024-06-01 08:30:45 EDT == 2024-06-01 12:30:45 UTC
        aware = eastern.localize(datetime(2024, 6, 1, 8, 30, 45))
        result = ensure_utc_aware(aware)

        assert result.utcoffset() == timedelta(0)
        assert result == aware  # same instant
        assert result.replace(tzinfo=None) == datetime(2024, 6, 1, 12, 30, 45)

    def test_aware_utc_is_unchanged(self):
        aware = datetime(2024, 6, 1, 12, 30, 45, tzinfo=timezone.utc)
        result = ensure_utc_aware(aware)

        assert result.utcoffset() == timedelta(0)
        assert result == aware
        assert result.replace(tzinfo=None) == datetime(2024, 6, 1, 12, 30, 45)

    def test_aware_pytz_utc_is_unchanged(self):
        aware = pytz.utc.localize(datetime(2024, 6, 1, 12, 30, 45))
        result = ensure_utc_aware(aware)

        assert result == aware
        assert result.replace(tzinfo=None) == datetime(2024, 6, 1, 12, 30, 45)

    def test_is_idempotent(self):
        eastern = pytz.timezone("US/Eastern")
        aware = eastern.localize(datetime(2024, 6, 1, 8, 30, 45))

        once = ensure_utc_aware(aware)
        twice = ensure_utc_aware(once)

        assert once == twice
        assert once.tzinfo == twice.tzinfo

    def test_timestamp_matches_for_equivalent_inputs(self):
        """Naive-UTC and aware-non-UTC inputs for the same instant normalize identically."""
        eastern = pytz.timezone("US/Eastern")
        naive = datetime(2024, 6, 1, 12, 30, 45)
        aware = eastern.localize(datetime(2024, 6, 1, 8, 30, 45))

        assert ensure_utc_aware(naive).timestamp() == ensure_utc_aware(aware).timestamp()

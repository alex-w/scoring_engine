"""Shared datetime helpers.

Datetimes reach us from a few different places with inconsistent tzinfo:
the engine writes naive values, model defaults write aware UTC values, and
some databases (SQLite in particular) hand back naive values regardless of
the column type. These helpers normalize that before formatting or
arithmetic.
"""

import pytz


def ensure_utc_aware(dt):
    """Ensure datetime is timezone-aware in UTC. Handles both naive and aware datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Naive datetime - assume UTC
        return pytz.utc.localize(dt)
    # Already aware - convert to UTC
    return dt.astimezone(pytz.utc)

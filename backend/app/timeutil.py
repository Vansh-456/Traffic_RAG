"""Timezone helpers. All user-facing time reasoning happens in Asia/Kolkata."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """Convert to IST; naive datetimes are assumed to already be IST."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def fmt_ist(dt: datetime) -> str:
    """Human format used in answers, e.g. '18:40 IST, 15 Nov'."""
    return to_ist(dt).strftime("%H:%M IST, %d %b %Y")

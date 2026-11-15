"""Rule-based date/time extraction for advisories (English + Hindi), IST.

Used as the no-LLM fallback for validity windows and to sanity-check LLM
output. Handles: 15.11.2026, 15/11/2026, 13-Nov-2026, 15th November 2026,
November 15, 2026, "15 & 16 Nov 2026", "14–27 Nov 2026", Hindi month names,
and times like 06:00 AM, 6 pm, 1800 hrs.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from app.timeutil import IST

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
    "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
    "जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7,
    "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "नवंबर": 11, "नवम्बर": 11,
    "दिसंबर": 12, "दिसम्बर": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

_NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
_DAY_MON = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*(?:&|and|,|to|–|-|—)\s*(\d{{1,2}})(?:st|nd|rd|th)?)?[\s-]*(?:of\s+)?({_MONTH_ALT})\.?,?[\s-]*(\d{{4}})?",
    re.IGNORECASE,
)
_MON_DAY = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?,?\s*(\d{{4}})?", re.IGNORECASE)
_TIME = re.compile(
    r"\b(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.|hrs|hours|बजे)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b",
    re.IGNORECASE,
)


def _year(y: str | None, ref: date) -> int:
    if not y:
        return ref.year
    yi = int(y)
    return yi + 2000 if yi < 100 else yi


def extract_dates(text: str, ref: date) -> list[date]:
    found: list[date] = []

    def add(y: int, m: int, d: int) -> None:
        try:
            found.append(date(y, m, d))
        except ValueError:
            pass

    for d, m, y in _NUMERIC.findall(text):
        add(_year(y, ref), int(m), int(d))
    for d1, d2, mon, y in _DAY_MON.findall(text):
        m = MONTHS[mon.lower()]
        add(_year(y, ref), m, int(d1))
        if d2:
            add(_year(y, ref), m, int(d2))
    for mon, d, y in _MON_DAY.findall(text):
        add(_year(y, ref), MONTHS[mon.lower()], int(d))
    # drop implausible dates (> 1 year from reference)
    return sorted({f for f in found if abs((f - ref).days) <= 400})


def extract_times(text: str) -> list[time]:
    out: list[time] = []
    for h, mnt, suffix, h24, m24 in _TIME.findall(text):
        if h24:
            out.append(time(int(h24), int(m24)))
            continue
        hour, minute = int(h), int(mnt or 0)
        suf = suffix.lower().replace(".", "")
        if suf == "pm" and hour < 12:
            hour += 12
        if suf == "am" and hour == 12:
            hour = 0
        if suf in ("hrs", "hours") and not mnt and hour >= 100:
            hour, minute = hour // 100, hour % 100
        if 0 <= hour < 24 and 0 <= minute < 60:
            out.append(time(hour, minute))
    return out


def validity_window(text: str, published_at: datetime | None) -> tuple[datetime | None, datetime | None]:
    """Best-effort (valid_from, valid_to) in IST from free text."""
    ref = (published_at or datetime.now(IST)).astimezone(IST).date()
    dates = extract_dates(text, ref)
    if not dates:
        return None, None
    times = extract_times(text)
    start_t = min(times) if times else time(0, 0)
    end_t = max(times) if len(times) > 1 else time(23, 59)
    start = datetime.combine(dates[0], start_t, IST)
    end = datetime.combine(dates[-1], end_t, IST)
    if end <= start:
        end = start + timedelta(hours=12)
    return start, end

"""Resolve relative time expressions to IST windows.

Handles English, Hindi and Hinglish: now/abhi/अभी, today/aaj/आज,
tonight/aaj raat, tomorrow/kal (future tense) / yesterday/kal (past tense),
parso, this weekend, last <weekday> [evening], next <weekday>, "6 pm Friday",
last week / last month, morning/evening qualifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.timeutil import IST

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
    "somvar": 0, "mangalvar": 1, "budhvar": 2, "guruvar": 3, "shukravar": 4, "shanivar": 5,
    "ravivar": 6, "सोमवार": 0, "मंगलवार": 1, "बुधवार": 2, "गुरुवार": 3, "शुक्रवार": 4,
    "शनिवार": 5, "रविवार": 6,
}
PARTS_OF_DAY = {
    "morning": (time(6), time(11, 59)), "subah": (time(6), time(11, 59)), "सुबह": (time(6), time(11, 59)),
    "afternoon": (time(12), time(16, 59)), "dopahar": (time(12), time(16, 59)),
    "evening": (time(17), time(21, 59)), "shaam": (time(17), time(21, 59)), "शाम": (time(17), time(21, 59)),
    "night": (time(20), time(23, 59)), "raat": (time(20), time(23, 59)), "रात": (time(20), time(23, 59)),
}
_PAST_TENSE = re.compile(r"\b(tha|thi|the|hua|hui|hue|gaya|gayi|was|were|happened|did)\b|था|थी|हुआ|हुई|गया", re.I)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|बजे)\b", re.I)


@dataclass
class TimeWindow:
    start: datetime
    end: datetime
    label: str
    is_live: bool = False
    is_historical: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "is_live": self.is_live,
            "is_historical": self.is_historical,
        }

    def bucket(self) -> str:
        """Coarse key for the semantic cache."""
        if self.is_live:
            return f"live:{self.start.strftime('%Y%m%d%H')}{self.start.minute // 15}"
        return f"{self.label}:{self.start.date().isoformat()}"


def _day(d: datetime, start: time = time(0), end: time = time(23, 59, 59)) -> tuple[datetime, datetime]:
    base = d.astimezone(IST).date()
    return datetime.combine(base, start, IST), datetime.combine(base, end, IST)


def _part_of_day(text: str) -> tuple[time, time] | None:
    for word, span in PARTS_OF_DAY.items():
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I):
            return span
    return None


def _clock(text: str) -> time | None:
    m = _TIME.search(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = m.group(3).lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if suffix in ("baje", "बजे") and hour < 7:  # "6 baje" in traffic talk usually means evening
        hour += 12
    return time(hour % 24, minute)


def _weekday(text: str) -> int | None:
    for word, idx in WEEKDAYS.items():
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I):
            return idx
    return None


def resolve_time_window(text: str, now: datetime, intent: str | None = None) -> TimeWindow:
    """Map a query to a time window (defaults depend on intent)."""
    t = text.lower()
    now = now.astimezone(IST)
    pod = _part_of_day(t)
    clock = _clock(t)
    wd = _weekday(t)

    def narrow(start: datetime, end: datetime, label: str, **kw: bool) -> TimeWindow:
        if clock:
            c = datetime.combine(start.date(), clock, IST)
            return TimeWindow(c - timedelta(hours=1), c + timedelta(hours=1, minutes=30), label, **kw)
        if pod:
            return TimeWindow(
                datetime.combine(start.date(), pod[0], IST),
                datetime.combine(start.date(), pod[1], IST),
                label,
                **kw,
            )
        return TimeWindow(start, end, label, **kw)

    if re.search(r"\b(right now|now|currently|at the moment|abhi|is waqt|live)\b|अभी|इस समय", t):
        return TimeWindow(now - timedelta(hours=2), now + timedelta(hours=1), "now", is_live=True)
    if re.search(r"\b(tonight|aaj raat)\b|आज रात", t):
        s = max(now, datetime.combine(now.date(), time(18), IST))
        return TimeWindow(s, datetime.combine(now.date(), time(23, 59, 59), IST), "tonight")
    if re.search(r"\b(parso|day after tomorrow)\b|परसों", t):
        s, e = _day(now + timedelta(days=2))
        return narrow(s, e, "day_after_tomorrow")
    if re.search(r"\btomorrow\b|\bkal\b|कल", t) and not re.search(r"\byesterday\b", t):
        past = bool(_PAST_TENSE.search(t)) and not re.search(r"\btomorrow\b", t)
        if past or re.search(r"\byesterday\b", t):
            s, e = _day(now - timedelta(days=1))
            return narrow(s, e, "yesterday", is_historical=True)
        s, e = _day(now + timedelta(days=1))
        return narrow(s, e, "tomorrow")
    if re.search(r"\byesterday\b", t):
        s, e = _day(now - timedelta(days=1))
        return narrow(s, e, "yesterday", is_historical=True)
    if re.search(r"\b(today|aaj|aj)\b|आज", t):
        s, e = _day(now)
        return narrow(s, e, "today")
    if re.search(r"\b(this weekend|weekend)\b|वीकेंड", t):
        days_to_sat = (5 - now.weekday()) % 7
        sat = now + timedelta(days=days_to_sat if now.weekday() < 5 else -(now.weekday() - 5))
        s, _ = _day(sat)
        _, e = _day(sat + timedelta(days=1))
        return TimeWindow(max(s, now - timedelta(hours=1)), e, "this_weekend")
    if re.search(r"\blast week\b|pichle hafte|पिछले हफ्ते", t):
        return TimeWindow(now - timedelta(days=7), now, "last_week", is_historical=True)
    if re.search(r"\blast month\b|pichle mahine|पिछले महीने", t):
        return TimeWindow(now - timedelta(days=30), now, "last_month", is_historical=True)
    if wd is not None:
        if re.search(r"\b(last|previous|pichle|pichhle)\b|पिछले", t) or _PAST_TENSE.search(t):
            delta = (now.weekday() - wd) % 7 or 7
            s, e = _day(now - timedelta(days=delta))
            return narrow(s, e, "last_weekday", is_historical=True)
        delta = (wd - now.weekday()) % 7
        if delta == 0 and not clock and not pod and not re.search(r"\bnext\b", t):
            delta = 0
        elif re.search(r"\bnext\b|agle|अगले", t) and delta == 0:
            delta = 7
        s, e = _day(now + timedelta(days=delta))
        return narrow(s, e, "weekday")
    if clock or pod:
        s, e = _day(now)
        w = narrow(s, e, "today_at")
        if w.end < now:  # "6 pm" said at 8 pm → tomorrow 6 pm
            w = narrow(s + timedelta(days=1), e + timedelta(days=1), "tomorrow_at")
        return w
    # intent defaults
    if intent in ("live_conditions", "cause_explanation"):
        return TimeWindow(now - timedelta(hours=2), now + timedelta(hours=1), "now", is_live=True)
    if intent == "historical_lookup":
        return TimeWindow(now - timedelta(days=30), now, "last_30_days", is_historical=True)
    return TimeWindow(now - timedelta(hours=12), now + timedelta(days=7), "upcoming")

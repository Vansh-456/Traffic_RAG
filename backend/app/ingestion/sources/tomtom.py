"""TomTom Traffic: flow segments + incidents (``trust_level=sensor``).

Budget manager
--------------
The free tier allows ~2,500 non-tile requests/day. ``BudgetManager`` keeps a
per-IST-day counter in Redis and spends requests with an atomic Lua
check-and-increment, so the cap ``TOMTOM_DAILY_BUDGET`` can never be exceeded
even with concurrent workers. Polling intervals adapt: base 15 min (06-23 IST)
and 60 min at night, stretched when the projected spend for the rest of the
day would not fit in the remaining budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import yaml
from redis.asyncio import Redis

from app.config import get_settings
from app.ingestion.http import PoliteFetcher
from app.ingestion.pipeline import DocumentDraft
from app.observability.logging import get_logger
from app.observability.metrics import TOMTOM_BUDGET_USED
from app.timeutil import IST, fmt_ist, now_ist

log = get_logger(__name__)

SOURCE_ID = "tomtom"
FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
INCIDENTS_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
DELHI_BBOX = "76.84,28.40,77.35,28.88"
INCIDENT_FIELDS = (
    "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,"
    "events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers}}}"
)
ICON_CATEGORY = {
    0: "unknown",
    1: "accident",
    2: "fog",
    3: "dangerous_conditions",
    4: "rain",
    5: "ice",
    6: "jam",
    7: "lane_closed",
    8: "road_closed",
    9: "road_works",
    10: "wind",
    11: "flooding",
    14: "broken_down_vehicle",
}
EVENT_TYPE_FOR_CATEGORY = {
    "accident": "accident",
    "road_closed": "closure",
    "lane_closed": "closure",
    "road_works": "roadwork",
    "flooding": "waterlogging",
    "broken_down_vehicle": "breakdown",
    "jam": "other",
}
DAY_INTERVAL_MIN = 15
NIGHT_INTERVAL_MIN = 60

_SPEND_LUA = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local n = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if used + n > cap then return -1 end
local v = redis.call('INCRBY', KEYS[1], n)
redis.call('EXPIRE', KEYS[1], 172800)
return v
"""


def is_night(dt: datetime) -> bool:
    h = dt.astimezone(IST).hour
    return h >= 23 or h < 6


class BudgetManager:
    """Daily TomTom request budget with adaptive polling cadence."""

    def __init__(self, redis: Redis[bytes], daily_budget: int | None = None) -> None:
        self.redis = redis
        self.cap = daily_budget or get_settings().tomtom_daily_budget

    @staticmethod
    def _day_key(now: datetime) -> str:
        return f"tomtom:budget:{now.astimezone(IST).date().isoformat()}"

    async def used(self, now: datetime | None = None) -> int:
        now = now or now_ist()
        raw = await self.redis.get(self._day_key(now))
        return int(raw or 0)

    async def try_spend(self, n: int, now: datetime | None = None) -> bool:
        now = now or now_ist()
        result = await self.redis.eval(_SPEND_LUA, 1, self._day_key(now), n, self.cap)  # type: ignore[no-untyped-call]
        ok = int(result) >= 0
        if ok:
            TOMTOM_BUDGET_USED.set(int(result))
        return ok

    def planned_requests(
        self, now: datetime, cost_per_cycle: int, base_scale: float = 1.0
    ) -> float:
        """Requests needed from `now` to midnight IST at base cadence × scale."""
        end = (now.astimezone(IST) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        total, t = 0.0, now.astimezone(IST)
        while t < end:
            step = (NIGHT_INTERVAL_MIN if is_night(t) else DAY_INTERVAL_MIN) * base_scale
            total += cost_per_cycle
            t += timedelta(minutes=step)
        return total

    async def interval_minutes(self, now: datetime, cost_per_cycle: int) -> float:
        """Adaptive interval so the rest of the day fits in the remaining budget."""
        remaining = self.cap - await self.used(now)
        base = NIGHT_INTERVAL_MIN if is_night(now) else DAY_INTERVAL_MIN
        if remaining <= 0:
            return math.inf
        planned = self.planned_requests(now, cost_per_cycle)
        scale = max(1.0, planned / remaining)
        return base * scale

    async def should_poll(self, kind: str, cost: int, now: datetime | None = None) -> bool:
        now = now or now_ist()
        interval = await self.interval_minutes(now, cost)
        if math.isinf(interval):
            return False
        last_raw = await self.redis.get(f"tomtom:last:{kind}")
        if last_raw:
            last = datetime.fromisoformat(
                last_raw.decode() if isinstance(last_raw, bytes) else last_raw
            )
            if now - last < timedelta(minutes=interval) - timedelta(seconds=30):
                return False
        return True

    async def mark_polled(self, kind: str, now: datetime | None = None) -> None:
        now = now or now_ist()
        await self.redis.set(f"tomtom:last:{kind}", now.isoformat(), ex=86400)

    async def status(self) -> dict[str, Any]:
        now = now_ist()
        used = await self.used(now)
        return {
            "date": now.date().isoformat(),
            "used": used,
            "cap": self.cap,
            "remaining": self.cap - used,
        }


@dataclass
class SegmentSeed:
    id: str
    name: str
    road: str
    lat: float
    lon: float


def load_segments(path: Any) -> list[SegmentSeed]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return [SegmentSeed(**s) for s in data["segments"]]


@dataclass
class FlowObs:
    segment_id: str
    current_speed: float
    free_flow_speed: float
    current_travel_time: float | None
    free_flow_travel_time: float | None
    confidence: float | None
    road_closure: bool
    coordinates: list[tuple[float, float]]  # (lon, lat)


def parse_flow(segment_id: str, payload: dict[str, Any]) -> FlowObs | None:
    d = payload.get("flowSegmentData")
    if not d:
        return None
    coords = [
        (c["longitude"], c["latitude"]) for c in d.get("coordinates", {}).get("coordinate", [])
    ]
    return FlowObs(
        segment_id=segment_id,
        current_speed=float(d.get("currentSpeed", 0)),
        free_flow_speed=float(d.get("freeFlowSpeed", 0)) or 1.0,
        current_travel_time=d.get("currentTravelTime"),
        free_flow_travel_time=d.get("freeFlowTravelTime"),
        confidence=d.get("confidence"),
        road_closure=bool(d.get("roadClosure", False)),
        coordinates=coords,
    )


async def fetch_flow(fetcher: PoliteFetcher, seg: SegmentSeed, key: str) -> FlowObs | None:
    res = await fetcher.get(
        FLOW_URL,
        params={"point": f"{seg.lat},{seg.lon}", "unit": "KMPH", "key": key},
        check_robots=False,
    )
    if res.status != 200:
        log.warning("tomtom flow http error", extra={"segment": seg.id, "status": res.status})
        return None
    import json

    return parse_flow(seg.id, json.loads(res.text))


@dataclass
class Incident:
    id: str
    category: str
    icon_category: int
    description: str
    from_name: str | None
    to_name: str | None
    road_numbers: list[str]
    delay_seconds: int | None
    magnitude: int | None
    length_m: float | None
    geometry: dict[str, Any]
    start_time: datetime | None
    end_time: datetime | None
    raw: dict[str, Any]


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_incidents(payload: dict[str, Any]) -> list[Incident]:
    out: list[Incident] = []
    for inc in payload.get("incidents", []):
        p = inc.get("properties", {})
        icon = int(p.get("iconCategory", 0) or 0)
        events = p.get("events") or []
        desc = "; ".join(e.get("description", "") for e in events if e.get("description"))
        out.append(
            Incident(
                id=str(p.get("id")),
                category=ICON_CATEGORY.get(icon, "unknown"),
                icon_category=icon,
                description=desc or ICON_CATEGORY.get(icon, "incident").replace("_", " "),
                from_name=p.get("from"),
                to_name=p.get("to"),
                road_numbers=list(p.get("roadNumbers") or []),
                delay_seconds=int(p["delay"]) if p.get("delay") is not None else None,
                magnitude=p.get("magnitudeOfDelay"),
                length_m=p.get("length"),
                geometry=inc.get("geometry") or {},
                start_time=_iso(p.get("startTime")),
                end_time=_iso(p.get("endTime")),
                raw=p,
            )
        )
    return out


async def fetch_incidents(fetcher: PoliteFetcher, key: str) -> list[Incident]:
    res = await fetcher.get(
        INCIDENTS_URL,
        params={
            "bbox": DELHI_BBOX,
            "fields": INCIDENT_FIELDS,
            "language": "en-GB",
            "timeValidityFilter": "present",
            "key": key,
        },
        check_robots=False,
    )
    if res.status != 200:
        log.warning("tomtom incidents http error", extra={"status": res.status})
        return []
    import json

    return parse_incidents(json.loads(res.text))


def render_incident(inc: Incident, now: datetime | None = None) -> str:
    """One-sentence text document, e.g. 'Accident on Ring Road near Moolchand ...'."""
    roads = ", ".join(inc.road_numbers)
    what = inc.description[:1].upper() + inc.description[1:]
    where = ""
    if inc.from_name and inc.to_name:
        where = f" between {inc.from_name} and {inc.to_name}"
    elif inc.from_name:
        where = f" near {inc.from_name}"
    on = f" on {roads}" if roads else ""
    delay = f", delay ~{round(inc.delay_seconds / 60)} min" if inc.delay_seconds else ""
    length = f", affecting {inc.length_m / 1000:.1f} km" if inc.length_m else ""
    since = f", since {fmt_ist(inc.start_time)}" if inc.start_time else ""
    seen = f" (TomTom live data as of {fmt_ist(now or now_ist())})"
    return f"{what}{on}{where}{delay}{length}{since}.{seen}"


def incident_draft(inc: Incident, now: datetime | None = None) -> DocumentDraft:
    now = now or now_ist()
    end = inc.end_time or (now + timedelta(hours=2))
    title = f"Live incident: {inc.category.replace('_', ' ')}" + (
        f" near {inc.from_name}" if inc.from_name else ""
    )
    return DocumentDraft(
        source_id=SOURCE_ID,
        url=f"tomtom:incident:{inc.id}",
        title=title,
        trust_level="sensor",
        content_type="text",
        body_text=render_incident(inc, now),
        published_at=inc.start_time or now,
        authority="TomTom Traffic",
        language="en",
        valid_from_hint=inc.start_time or now,
        valid_to_hint=end,
        metadata={"incident_id": inc.id, "category": inc.category},
    )


def incident_geometry_wkt(geometry: dict[str, Any]) -> str | None:
    from shapely.geometry import shape

    if not geometry:
        return None
    try:
        return shape(geometry).wkt
    except Exception:
        return None

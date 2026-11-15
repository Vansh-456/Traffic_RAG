"""Agentic tool routing (Section 6.3).

The fast LLM picks up to 4 tool calls via native tool-calling; the routing
rules below are then *enforced as defaults* (missing mandatory tools for the
intent are added), so behaviour is predictable even if the LLM under-calls
or is unavailable. Tools run concurrently, each with a timeout, and every
call is recorded (args, latency, summary) for the internals panel.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.ingestion.geocoding import GeoMatch, get_geocoder
from app.observability.logging import get_logger
from app.providers.llm import LLMClient, LLMUnavailableError, Usage
from app.rag.query_understanding import QueryUnderstanding
from app.rag.retrieval.hybrid import RetrievalResult, Toggles, hybrid_search
from app.rag.timewindow import resolve_time_window
from app.rag.tools import history, live, route_check

log = get_logger(__name__)

MAX_TOOL_CALLS = 4
TOOL_TIMEOUT_S = 10.0

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_advisories",
            "description": "Hybrid search over official advisories, news and live-incident texts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "event_types": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_traffic",
            "description": "Current speed vs free-flow on monitored road segments near the locations, with 60-min trend.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_incidents",
            "description": "Active TomTom incidents (accidents, closures, jams) near the locations.",
            "parameters": {"type": "object", "properties": {"radius_m": {"type": "number"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather now and in the time window for the nearest Delhi zone (rain, fog, heat flags).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_incident_history",
            "description": "Counts of past closures/incidents/advisories by type for the locations and time window.",
            "parameters": {"type": "object", "properties": {"event_type": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_route",
            "description": "Advisories, incidents, congestion and weather along the corridor from origin to destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "depart_time": {"type": "string", "description": "e.g. '6 pm Friday'"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "typical_conditions",
            "description": "Historical average congestion for a weekday and hour (is 6 pm Friday bad?).",
            "parameters": {
                "type": "object",
                "properties": {
                    "weekday": {"type": "integer", "description": "0=Mon"},
                    "hour": {"type": "integer"},
                },
            },
        },
    },
]

DEFAULT_TOOLS: dict[str, list[str]] = {
    "live_conditions": [
        "get_live_traffic",
        "get_active_incidents",
        "search_advisories",
        "get_weather",
    ],
    "cause_explanation": [
        "get_live_traffic",
        "get_active_incidents",
        "search_advisories",
        "get_weather",
    ],
    "advisory_lookup": ["search_advisories"],
    "plan_trip": ["check_route", "typical_conditions", "search_advisories"],
    "historical_lookup": ["search_advisories", "query_incident_history"],
    "general_info": ["search_advisories"],
    "out_of_scope": [],
}


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    origin: str  # llm | rule
    latency_ms: float = 0.0
    ok: bool = True
    summary: str = ""
    result: Any = None

    def trace(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "origin": self.origin,
            "latency_ms": round(self.latency_ms, 1),
            "ok": self.ok,
            "summary": self.summary,
        }


@dataclass
class RouterOutput:
    calls: list[ToolCall] = field(default_factory=list)
    retrieval: RetrievalResult | None = None
    llm_selected: list[str] = field(default_factory=list)


async def select_tools(
    qu: QueryUnderstanding, llm: LLMClient, usage: Usage | None
) -> list[tuple[str, dict[str, Any]]]:
    """Ask the LLM which tools to call; returns [(name, args)]."""
    if not llm.enabled:
        return []
    messages = [
        {
            "role": "system",
            "content": (
                "You route a Delhi traffic question to data tools. Call the tools needed to answer "
                f"(at most {MAX_TOOL_CALLS}). Locations and time are already resolved: "
                f"locations={[m.name for m in qu.locations]}, window={qu.time_window.label}, intent={qu.intent}."
            ),
        },
        {"role": "user", "content": qu.standalone},
    ]
    try:
        msg = await llm.complete(
            messages, "route", tools=TOOL_SPECS, usage=usage, purpose="routing", max_tokens=250
        )
    except (LLMUnavailableError, TimeoutError) as exc:
        log.info("tool selection failed; using rules", extra={"err": str(exc)[:200]})
        return []
    out = []
    for tc in (getattr(msg, "tool_calls", None) or [])[:MAX_TOOL_CALLS]:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (json.JSONDecodeError, AttributeError):
            args = {}
        out.append((tc.function.name, args if isinstance(args, dict) else {}))
    return out


def plan_calls(
    qu: QueryUnderstanding, llm_calls: list[tuple[str, dict[str, Any]]]
) -> list[ToolCall]:
    valid = {t["function"]["name"] for t in TOOL_SPECS}
    calls: list[ToolCall] = [ToolCall(n, a, "llm") for n, a in llm_calls if n in valid]
    names = {c.name for c in calls}
    for name in DEFAULT_TOOLS.get(qu.intent, ["search_advisories"]):
        if name not in names:
            calls.append(ToolCall(name, {}, "rule"))
            names.add(name)
    # rule tools are mandatory; drop surplus LLM extras beyond the cap first
    while len(calls) > MAX_TOOL_CALLS:
        extra = next(
            (
                c
                for c in reversed(calls)
                if c.origin == "llm" and c.name not in DEFAULT_TOOLS.get(qu.intent, [])
            ),
            None,
        )
        calls.remove(extra or calls[-1])
    return calls


def _summarise(name: str, result: Any) -> str:
    if isinstance(result, RetrievalResult):
        return f"{len(result.candidates)} chunks, best score {result.best_score:.3f}"
    if not isinstance(result, dict):
        return str(result)[:120]
    if not result.get("available", True):
        return f"unavailable: {result.get('reason', '')}"[:160]
    if name == "get_live_traffic":
        return ", ".join(
            f"{s['name']}: {s['level']} ({s['current_speed_kmph']}/{s['free_flow_speed_kmph']} km/h)"
            for s in result["segments"][:3]
        )
    if name == "get_active_incidents":
        return f"{result['count']} active incidents"
    if name == "get_weather":
        return (
            f"{result['zone']}: {result['current']['conditions']}, flags={result['window_flags']}"
        )
    if name == "check_route":
        return f"{len(result['advisories'])} advisories, {len(result['incidents'])} incidents, {len(result['segments'])} segments on corridor"
    if name == "typical_conditions":
        return ", ".join(f"{s['name']}: {s['level']}" for s in result["segments"][:3])
    if name == "query_incident_history":
        return f"counts={result['advisory_and_news_counts']}"
    return json.dumps(result, default=str)[:160]


async def _trip_endpoints(
    qu: QueryUnderstanding, args: dict[str, Any]
) -> tuple[GeoMatch | None, GeoMatch | None]:
    geocoder = get_geocoder()
    origin = await geocoder.geocode(args["origin"]) if args.get("origin") else None
    dest = await geocoder.geocode(args["destination"]) if args.get("destination") else None
    locs = qu.locations
    if dest is None and locs:
        dest = locs[-1]
    if origin is None:
        origin = locs[0] if len(locs) >= 2 else await geocoder.geocode("Connaught Place")
    return origin, dest


async def execute(
    qu: QueryUnderstanding,
    calls: list[ToolCall],
    now: datetime,
    toggles: Toggles,
) -> RouterOutput:
    out = RouterOutput(calls=calls)

    async def run(call: ToolCall) -> None:
        started = time.perf_counter()
        try:
            async with session_scope() as s:
                call.result = await asyncio.wait_for(
                    _dispatch(s, qu, call, now, toggles),
                    TOOL_TIMEOUT_S * (2 if call.name == "search_advisories" else 1),
                )
        except TimeoutError:
            call.ok, call.result = False, {"available": False, "reason": "timeout"}
        except Exception as exc:
            log.warning(
                "tool failed",
                extra={"tool": call.name, "err": f"{type(exc).__name__}: {exc}"[:300]},
            )
            call.ok, call.result = (
                False,
                {"available": False, "reason": f"error: {type(exc).__name__}"},
            )
        call.latency_ms = (time.perf_counter() - started) * 1000
        call.summary = _summarise(call.name, call.result)

    await asyncio.gather(*(run(c) for c in calls))
    for c in calls:
        if c.name == "search_advisories" and isinstance(c.result, RetrievalResult):
            out.retrieval = c.result
    return out


async def _dispatch(
    s: AsyncSession, qu: QueryUnderstanding, call: ToolCall, now: datetime, toggles: Toggles
) -> Any:
    w = qu.time_window
    if call.name == "search_advisories":
        queries = list(qu.rewritten_queries)
        if call.args.get("query") and call.args["query"] not in queries:
            queries.append(call.args["query"])
        return await hybrid_search(
            s,
            queries=queries[:4] or [qu.standalone],
            locations=qu.locations,
            window=w,
            intent=qu.intent,
            now=now,
            toggles=toggles,
        )
    if call.name == "get_live_traffic":
        return await live.get_live_traffic(s, qu.locations, now)
    if call.name == "get_active_incidents":
        return await live.get_active_incidents(s, qu.locations, call.args.get("radius_m"))
    if call.name == "get_weather":
        return await live.get_weather(s, qu.locations, w.start, w.end, now)
    if call.name == "query_incident_history":
        return await history.query_incident_history(
            s, qu.locations, w.start, w.end, call.args.get("event_type")
        )
    if call.name == "check_route":
        origin, dest = await _trip_endpoints(qu, call.args)
        if origin is None or dest is None:
            return {"available": False, "reason": "could not resolve origin/destination"}
        depart = w.start + (w.end - w.start) / 2 if not w.is_live else now
        if call.args.get("depart_time"):
            depart = resolve_time_window(str(call.args["depart_time"]), now).start + timedelta(
                hours=1
            )
        return await route_check.check_route(s, origin, dest, depart, now)
    if call.name == "typical_conditions":
        ref = w.start + timedelta(hours=1) if not w.is_live else now
        wd, hour = live.ist_weekday_hour(ref)
        wd = int(call.args.get("weekday", wd))
        hour = int(call.args.get("hour", hour))
        return await live.typical_conditions(s, qu.locations, wd, hour)
    return {"available": False, "reason": "unknown tool"}

"""Route alert matching (Section 8).

Triggers:
- new active advisory/incident chunk whose geometry intersects a saved
  route corridor (300 m buffer) and whose validity overlaps the route's
  active window (today/tomorrow on an active weekday around departure);
- live congestion on corridor segments below ``ALERT_CONGESTION_RATIO`` in
  the 90 minutes before the usual departure;
- morning digest at the user's chosen time.
``alerts_sent`` (unique route+event+channel) guarantees no duplicate alerts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notify import send_email, send_push
from app.config import get_settings
from app.db.models.users import AlertSent
from app.db.session import session_scope
from app.observability.logging import get_logger
from app.timeutil import IST, fmt_ist, now_ist, utcnow

log = get_logger(__name__)


@dataclass
class AlertCandidate:
    route_id: int
    event_key: str
    kind: str
    title: str
    body: str
    url: str | None


def route_active_now(days: list[int], depart: time | None, now: datetime, lead: timedelta = timedelta(hours=3)) -> bool:
    """Is the route relevant within the next few hours (or anytime today if no departure set)?"""
    local = now.astimezone(IST)
    if local.weekday() not in (days or list(range(7))):
        return False
    if depart is None:
        return True
    dep = datetime.combine(local.date(), depart, IST)
    return dep - lead <= local <= dep + timedelta(hours=1)


async def _claim(session: AsyncSession, cand: AlertCandidate, channel: str) -> bool:
    """Insert into alerts_sent; False if this alert was already sent (dedup)."""
    stmt = insert(AlertSent).values(
        route_id=cand.route_id, event_key=cand.event_key, kind=cand.kind, channel=channel,
        sent_at=utcnow(), payload={"title": cand.title, "body": cand.body},
    ).on_conflict_do_nothing(constraint="uq_alerts_sent_route_event").returning(AlertSent.id)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def deliver(session: AsyncSession, cand: AlertCandidate) -> list[str]:
    route = (await session.execute(text(
        """SELECT r.id, r.name, r.channels, u.email, u.email_verified, r.user_id
           FROM saved_routes r JOIN users u ON u.id = r.user_id WHERE r.id = :id"""
    ), {"id": cand.route_id})).first()
    if route is None:
        return []
    sent: list[str] = []
    frontend = get_settings().frontend_url
    payload = {"title": cand.title, "body": cand.body, "url": cand.url or f"{frontend}/routes", "tag": cand.event_key}
    if "push" in (route.channels or []):
        subs = (await session.execute(text("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = :u"), {"u": route.user_id})).all()
        if subs and await _claim(session, cand, "push"):
            ok = False
            for s in subs:
                ok |= await send_push({"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}, payload)
            if ok:
                sent.append("push")
    if "email" in (route.channels or []) and route.email and route.email_verified and await _claim(session, cand, "email"):
        if await send_email(route.email, cand.title, f"{cand.body}\n\n{payload['url']}\n\nNot an official service; follow Delhi Traffic Police instructions."):
            sent.append("email")
    if "inapp" in (route.channels or []) or not route.channels:
        if await _claim(session, cand, "inapp"):
            sent.append("inapp")
    return sent


async def match_chunks(chunk_ids: list[int], now: datetime | None = None) -> list[AlertCandidate]:
    """Saved routes whose corridor intersects these (new) chunks."""
    if not chunk_ids:
        return []
    now = now or now_ist()
    buffer_m = get_settings().alert_corridor_meters
    async with session_scope() as s:
        rows = (await s.execute(text(
            """SELECT r.id AS route_id, r.name, r.days_of_week, r.depart_time, c.id AS chunk_id, d.title, d.url,
                      c.severity, c.valid_from, c.valid_to, c.trust_level, c.metadata->>'summary_en' AS summary
               FROM chunks c JOIN documents d ON d.id = c.document_id
               JOIN saved_routes r ON r.active AND r.corridor IS NOT NULL
                 AND ST_DWithin(r.corridor::geography, c.geom::geography, :buf)
               WHERE c.id = ANY(:ids) AND c.is_canonical AND c.geom IS NOT NULL
                 AND (c.valid_to IS NULL OR c.valid_to >= :now)
                 AND (c.valid_from IS NULL OR c.valid_from <= :horizon)"""
        ), {"ids": chunk_ids, "buf": buffer_m, "now": now, "horizon": now + timedelta(hours=36)})).all()
    out: list[AlertCandidate] = []
    for r in rows:
        # advisories for tomorrow are relevant now; live incidents only near departure
        if r.trust_level == "sensor" and not route_active_now(r.days_of_week, r.depart_time, now):
            continue
        when = f" ({fmt_ist(r.valid_from)} – {fmt_ist(r.valid_to) if r.valid_to else 'until further notice'})" if r.valid_from else ""
        out.append(AlertCandidate(
            route_id=r.route_id, event_key=f"chunk:{r.chunk_id}", kind="incident" if r.trust_level == "sensor" else "advisory",
            title=f"⚠ {r.name}: {r.title[:80]}", body=(r.summary or r.title)[:300] + when, url=r.url if r.url.startswith("http") else None,
        ))
    return out


async def match_congestion(now: datetime | None = None) -> list[AlertCandidate]:
    now = now or now_ist()
    ratio = get_settings().alert_congestion_ratio
    async with session_scope() as s:
        rows = (await s.execute(text(
            """WITH latest AS (
                 SELECT DISTINCT ON (segment_id) segment_id, current_speed, free_flow_speed, observed_at
                 FROM traffic_flow_obs WHERE observed_at > :since ORDER BY segment_id, observed_at DESC)
               SELECT r.id AS route_id, r.name, r.days_of_week, r.depart_time, s.id AS seg, s.name AS seg_name,
                      l.current_speed, l.free_flow_speed, l.observed_at
               FROM saved_routes r
               JOIN segments s ON r.active AND r.corridor IS NOT NULL AND ST_DWithin(r.corridor::geography, s.geom::geography, 300)
               JOIN latest l ON l.segment_id = s.id
               WHERE l.current_speed / NULLIF(l.free_flow_speed, 0) < :ratio"""
        ), {"since": now - timedelta(minutes=45), "ratio": ratio})).all()
    out = []
    for r in rows:
        if r.depart_time is None or not route_active_now(r.days_of_week, r.depart_time, now, lead=timedelta(minutes=90)):
            continue
        out.append(AlertCandidate(
            route_id=r.route_id, event_key=f"congestion:{r.seg}:{now.astimezone(IST):%Y%m%d}", kind="congestion",
            title=f"🚦 {r.name}: heavy traffic on {r.seg_name}",
            body=f"{round(r.current_speed)} km/h vs usual {round(r.free_flow_speed)} km/h (as of {fmt_ist(r.observed_at)}). Consider leaving earlier or an alternate route.",
            url=None,
        ))
    return out


async def digests(now: datetime | None = None) -> list[AlertCandidate]:
    """Morning digest: today's active advisories on each route, at the user's digest time."""
    now = (now or now_ist()).astimezone(IST)
    start = datetime.combine(now.date(), time(0), IST)
    end = start + timedelta(days=1)
    async with session_scope() as s:
        routes = (await s.execute(text(
            "SELECT id, name, digest_time, days_of_week FROM saved_routes WHERE active AND digest_time IS NOT NULL AND corridor IS NOT NULL"
        ))).all()
        out = []
        for r in routes:
            dt = datetime.combine(now.date(), r.digest_time, IST)
            if not (dt <= now < dt + timedelta(minutes=15)) or now.weekday() not in (r.days_of_week or list(range(7))):
                continue
            items = (await s.execute(text(
                """SELECT d.title FROM chunks c JOIN documents d ON d.id = c.document_id, saved_routes r
                   WHERE r.id = :rid AND c.is_canonical AND c.is_active AND c.geom IS NOT NULL
                     AND ST_DWithin(r.corridor::geography, c.geom::geography, 300)
                     AND COALESCE(c.valid_from, c.published_at) < :end AND COALESCE(c.valid_to, :end) >= :start
                   ORDER BY c.severity LIMIT 5"""
            ), {"rid": r.id, "start": start, "end": end})).all()
            body = "\n".join(f"• {i.title}" for i in items) or "No advisories on your route today."
            out.append(AlertCandidate(r.id, f"digest:{now.date().isoformat()}", "digest", f"☀ {r.name}: today's traffic digest", body, None))
    return out


async def dispatch(cands: list[AlertCandidate]) -> dict[str, Any]:
    sent: dict[str, int] = {}
    for cand in cands:
        async with session_scope() as s:
            for ch in await deliver(s, cand):
                sent[ch] = sent.get(ch, 0) + 1
    return {"candidates": len(cands), "sent": sent}


async def recent_chunk_ids(minutes: int = 10) -> list[int]:
    async with session_scope() as s:
        rows = await s.execute(text("SELECT id FROM chunks WHERE status='ready' AND created_at > now() - make_interval(mins => :m)"), {"m": minutes})
        return [r[0] for r in rows]


async def run_alerts_cycle(now: datetime | None = None) -> dict[str, Any]:
    """Periodic sweep (every 5 min): recent chunks + congestion + digests."""
    cands = await match_chunks(await recent_chunk_ids(), now)
    cands += await match_congestion(now)
    cands += await digests(now)
    return await dispatch(cands)

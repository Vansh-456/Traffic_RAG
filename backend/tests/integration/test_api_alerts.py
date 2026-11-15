"""HTTP API + saved-route alert flow against real Postgres/Redis."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
from sqlalchemy import text

from app.alerts.matcher import dispatch, match_chunks
from app.db.session import session_scope
from app.ingestion.pipeline import DocumentDraft, process
from app.main import create_app
from app.timeutil import utcnow


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    )


async def test_ready_checks_db_and_redis():
    async with _client() as c:
        r = await c.get("/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"] == {"db": "ok", "redis": "ok"}


async def test_places_search_hindi_and_alias():
    async with _client() as c:
        hi = (await c.get("/api/places/search", params={"q": "लाल किला"})).json()
        cp = (await c.get("/api/places/search", params={"q": "connaught"})).json()
    assert hi[0]["name"] == "Red Fort"
    assert cp[0]["name"] == "Connaught Place"


async def test_chat_sse_stream_and_feedback():
    now = utcnow()
    async with session_scope() as s:
        await process(
            s,
            DocumentDraft(
                source_id="dtp_advisories",
                url="https://traffic.delhipolice.gov.in/en/node/7",
                title="Ring Road closed near AIIMS",
                trust_level="official",
                content_type="text",
                body_text="Ring Road near AIIMS will be closed today for repair work. Use Aurobindo Marg.",
                published_at=now - timedelta(hours=1),
                valid_from_hint=now - timedelta(hours=1),
                valid_to_hint=now + timedelta(hours=8),
            ),
        )
    async with _client() as c:
        async with c.stream(
            "POST", "/api/chat", json={"message": "Is Ring Road closed near AIIMS today?"}
        ) as r:
            assert r.status_code == 200
            assert "dtc_sid" in r.headers.get("set-cookie", "")
            body = "".join([chunk async for chunk in r.aiter_text()])
        events = [
            ln.split(":", 1)[1].strip() for ln in body.splitlines() if ln.startswith("event:")
        ]
        assert events[0] == "meta" and "token" in events and events[-1] == "done"
        meta = json.loads(
            next(ln.split(":", 1)[1] for ln in body.splitlines() if ln.startswith("data:"))
        )
        fb = await c.post(
            "/api/feedback",
            json={"message_id": meta["message_id"], "rating": -1, "comment": "too vague"},
        )
        assert fb.status_code == 200 and fb.json()["in_review_queue"] is True
        adv = (await c.get("/api/advisories")).json()
        assert adv["total"] == 1 and adv["items"][0]["title"] == "Ring Road closed near AIIMS"


async def test_admin_requires_token():
    async with _client() as c:
        assert (await c.get("/api/admin/overview")).status_code == 401
        ok = await c.get("/api/admin/overview", headers={"x-admin-token": "change-me-admin-token"})
    assert ok.status_code == 200 and "sources" in ok.json()


async def test_saved_route_alert_fires_once_for_new_advisory():
    async with _client() as c:
        r = await c.post(
            "/api/routes",
            json={
                "name": "Home to Office",
                "origin": {"label": "Lajpat Nagar", "lat": 28.5677, "lon": 77.2433},
                "destination": {"label": "Connaught Place", "lat": 28.6315, "lon": 77.2167},
                "depart_time": "09:00",
                "days_of_week": [0, 1, 2, 3, 4, 5, 6],
                "channels": ["inapp"],
            },
        )
        assert r.status_code == 200, r.text
        cookie = r.cookies
        now = utcnow()
        async with session_scope() as s:
            res = await process(
                s,
                DocumentDraft(
                    source_id="dtp_advisories",
                    url="https://traffic.delhipolice.gov.in/en/node/9",
                    title="Closure near India Gate",
                    trust_level="official",
                    content_type="text",
                    body_text="Road closed near India Gate C-Hexagon for an event.",
                    published_at=now,
                    valid_from_hint=now,
                    valid_to_hint=now + timedelta(hours=4),
                ),
            )
            # put the advisory squarely on the corridor midpoint
            await s.execute(
                text(
                    "UPDATE chunks SET geom = ST_SetSRID(ST_MakePoint(77.23, 28.60), 4326) WHERE id = ANY(:ids)"
                ),
                {"ids": res.chunk_ids},
            )
        first = await dispatch(await match_chunks(res.chunk_ids))
        second = await dispatch(await match_chunks(res.chunk_ids))
        assert first["sent"] == {"inapp": 1}
        assert second["sent"] == {}  # alerts_sent dedup
        alerts = (await c.get("/api/alerts", cookies=cookie)).json()
    assert len(alerts) == 1 and "Closure near India Gate" in alerts[0]["payload"]["title"]

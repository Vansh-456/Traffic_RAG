"""Ingestion pipeline on a real Postgres: indexing, geocoding, idempotency, dedup."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.pipeline import DocumentDraft, process
from app.timeutil import IST

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=IST)


def _advisory(
    url: str = "https://traffic.delhipolice.gov.in/en/node/1", trust: str = "official", **kw: object
) -> DocumentDraft:
    body = (
        "Traffic advisory: diversions near ITO for procession\n\n"
        "A procession will move from Ramlila Maidan to Rajghat via ITO on 10.11.2026 between 10:00 AM and 6:00 PM.\n"
        "Diversions:\n1. Traffic from Vikas Marg towards ITO will be diverted at ITO chowk towards Ring Road.\n"
        "2. Bahadur Shah Zafar Marg will be closed between Delhi Gate and ITO.\n"
        "Commuters are advised to use Ring Road and Mathura Road."
    )
    defaults = dict(
        source_id="dtp_advisories",
        url=url,
        title="Traffic advisory: diversions near ITO for procession",
        trust_level=trust,
        content_type="text",
        body_text=body,
        published_at=NOW - timedelta(hours=15),
        authority="Delhi Traffic Police",
        valid_from_hint=None,
        valid_to_hint=None,
    )
    defaults.update(kw)
    return DocumentDraft(**defaults)  # type: ignore[arg-type]


async def test_pipeline_creates_indexed_geocoded_chunk():
    async with session_scope() as s:
        res = await process(s, _advisory())
    assert res.status == "new" and len(res.chunk_ids) == 1
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """SELECT c.event_type, c.valid_from, c.valid_to, c.text_with_context, c.geom IS NOT NULL AS has_geom,
                      c.tsv_en @@ to_tsquery('english','diversion') AS fts, c.is_canonical, c.dedup_cluster_id,
                      (SELECT count(*) FROM chunk_embeddings e WHERE e.chunk_id = c.id) AS emb,
                      (SELECT count(*) FROM question_embeddings q WHERE q.chunk_id = c.id) AS qs,
                      (SELECT array_agg(p.name) FROM chunk_locations cl JOIN places p ON p.id = cl.place_id WHERE cl.chunk_id = c.id) AS places
               FROM chunks c"""
                )
            )
        ).one()
    assert row.event_type == "procession"
    assert row.valid_from == datetime(2026, 11, 10, 10, 0, tzinfo=IST)
    assert row.text_with_context.startswith("[Source: Delhi Traffic Police (official)")
    assert row.has_geom and row.fts and row.is_canonical and row.emb == 1 and row.qs >= 3
    assert {"ITO", "Ring Road", "Vikas Marg"} <= set(row.places)


async def test_reprocessing_same_content_is_idempotent():
    async with session_scope() as s:
        first = await process(s, _advisory())
    async with session_scope() as s:
        again = await process(s, _advisory())
    assert first.status == "new" and again.status == "unchanged"


async def test_dedup_prefers_official_over_news():
    async with session_scope() as s:
        news = await process(
            s,
            _advisory(
                url="https://news.example/ito",
                trust="news",
                source_id="google_news",
                authority=None,
                publisher="City Times",
            ),
        )
    async with session_scope() as s:
        official = await process(s, _advisory())
    async with session_scope() as s:
        rows = (
            await s.execute(
                text("SELECT id, is_canonical, dedup_cluster_id FROM chunks ORDER BY id")
            )
        ).all()
    assert rows[0].dedup_cluster_id == rows[1].dedup_cluster_id
    canon = {r.id: r.is_canonical for r in rows}
    assert canon[official.chunk_ids[0]] and not canon[news.chunk_ids[0]]

"""Retrieval and full answer flow on a real Postgres (no LLM keys)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.db.session import session_scope
from app.ingestion.pipeline import DocumentDraft, process
from app.rag.orchestrator import RunConfig, RunResult, run
from app.rag.retrieval.hybrid import hybrid_search
from app.rag.timewindow import resolve_time_window
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


async def test_hybrid_search_ranks_relevant_advisory_first():
    async with session_scope() as s:
        await process(s, _advisory())
        await process(
            s,
            DocumentDraft(
                source_id="google_news",
                url="https://news.example/rain",
                title="Rain lashes Dwarka",
                trust_level="news",
                content_type="text",
                body_text="Rain lashes Dwarka; waterlogging reported near Dwarka Sector 21 metro.",
                published_at=NOW - timedelta(hours=2),
            ),
        )
    from app.ingestion.geocoding import get_geocoder

    ito = await get_geocoder().geocode("ITO")
    async with session_scope() as s:
        res = await hybrid_search(
            s,
            queries=["Any diversions near ITO today?"],
            locations=[ito] if ito else [],
            window=resolve_time_window("today", NOW),
            intent="advisory_lookup",
            now=NOW,
        )
    assert res.candidates, res.trace
    top = res.candidates[0]
    assert "ITO" in top.title
    assert top.final > 0 and top.freshness == 1.0  # validity window covers the query day
    assert set(res.trace["lists"]) >= {"dense[0]", "question_index", "fulltext", "trigram"}


async def test_orchestrator_end_to_end_without_llm():
    async with session_scope() as s:
        await process(s, _advisory())
    result = RunResult()
    events = [
        e
        async for e in run(
            "Any diversions near ITO today?", [], RunConfig(now=NOW, use_cache=False), result=result
        )
    ]
    names = [e["event"] for e in events]
    assert (
        names[-1] == "done" and "token" in names and "citations" in names and "internals" in names
    )
    assert not result.abstained
    assert "[1]" in result.answer
    assert result.citations[0]["url"] == "https://traffic.delhipolice.gov.in/en/node/1"
    internals = result.internals
    assert internals["query_understanding"]["locations"][0]["name"] == "ITO"
    assert internals["retrieval"]["candidates"][0]["final"] > 0
    json.dumps(internals, default=str)  # serialisable for SSE / storage


async def test_unanswerable_question_abstains():
    async with session_scope() as s:
        await process(s, _advisory())
    result = RunResult()
    _ = [
        e
        async for e in run(
            "Is there a diversion at Dwarka Sector 21 today?",
            [],
            RunConfig(now=NOW, use_cache=False),
            result=result,
        )
    ]
    assert result.abstained
    assert "don't have enough reliable information" in result.answer


async def test_out_of_scope_refusal():
    result = RunResult()
    _ = [
        e
        async for e in run(
            "Give me a recipe for paneer butter masala", [], RunConfig(now=NOW), result=result
        )
    ]
    assert result.intent == "out_of_scope" and "Delhi traffic" in result.answer

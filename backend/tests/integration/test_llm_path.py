"""Orchestrator LLM path with a scripted fake LLM (no network).

Covers: LLM query understanding, tool selection merged with default rules,
streamed generation with a stripped <followups> block, LLM claim verification
and the `revision` event when a claim is unsupported.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.db.session import session_scope
from app.ingestion.pipeline import DocumentDraft, process
from app.providers.llm import LLMClient
from app.rag.orchestrator import RunConfig, RunResult, run
from app.rag.router import plan_calls
from app.timeutil import IST

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=IST)


class FakeLLM(LLMClient):
    def __init__(self, answer: str, unsupported_index: int | None = None) -> None:
        super().__init__(Settings(_env_file=None, groq_api_key="test", gemini_api_key="", llm_rpm_limit=1000))
        self.answer = answer
        self.unsupported_index = unsupported_index
        self.purposes: list[str] = []

    async def _acompletion(self, model: str, **kwargs: Any) -> Any:  # noqa: ARG002
        messages = kwargs["messages"]
        joined = "\n".join(str(m.get("content")) for m in messages)
        if kwargs.get("tools"):
            self.purposes.append("routing")
            call = SimpleNamespace(function=SimpleNamespace(name="get_weather", arguments="{}"))
            return _resp(None, tool_calls=[call])
        if "verify whether each claim" in joined:
            self.purposes.append("verification")
            idx = [int(x) for x in __import__("re").findall(r"Claim (\d+):", joined)]
            return _resp(json.dumps({"results": [{"index": i, "supported": i != self.unsupported_index, "reason": "ok"} for i in idx]}))
        if "analyse questions" in joined:
            self.purposes.append("query_understanding")
            return _resp(json.dumps({
                "language": "en", "intent": "advisory_lookup", "standalone_question": "Any diversions near ITO today?",
                "locations": ["ITO"], "time_expression": "today",
                "rewritten_queries": ["ITO diversions today", "आईटीओ डायवर्जन आज", "ITO diversion procession"], "sub_questions": [],
            }))
        raise AssertionError(f"unexpected call: {joined[:200]}")

    async def stream(self, messages, role="answer", **kw):  # type: ignore[no-untyped-def]
        self.purposes.append("generation")
        for i in range(0, len(self.answer), 7):
            yield self.answer[i : i + 7]


def _resp(content: str | None, tool_calls: list[Any] | None = None) -> Any:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20))


async def _seed() -> None:
    async with session_scope() as s:
        await process(s, DocumentDraft(
            source_id="dtp_advisories", url="https://traffic.delhipolice.gov.in/en/node/1",
            title="Traffic advisory: diversions near ITO for procession", trust_level="official", content_type="text",
            body_text="A procession will pass ITO on 10.11.2026 from 10:00 AM to 6:00 PM. Bahadur Shah Zafar Marg will be closed between Delhi Gate and ITO.",
            published_at=NOW - timedelta(hours=12), authority="Delhi Traffic Police",
        ))


async def test_llm_path_streams_cited_answer_and_strips_followups():
    await _seed()
    llm = FakeLLM("Bahadur Shah Zafar Marg is closed between Delhi Gate and ITO from 10 am [1].\n<followups>[\"Is ITO open tomorrow?\",\"Alternate route?\",\"Live traffic at ITO?\"]</followups>")
    result = RunResult()
    events = [e async for e in run("Any diversions near ITO today?", [], RunConfig(now=NOW, use_cache=False), llm=llm, result=result)]
    streamed = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert "<followups>" not in streamed and "[1]" in streamed
    done = events[-1]
    assert done["event"] == "done" and done["data"]["verified"] is True
    assert done["data"]["follow_ups"][0] == "Is ITO open tomorrow?"
    assert result.internals["query_understanding"]["method"] == "llm"
    tools = {t["name"]: t["origin"] for t in result.internals["tools"]}
    assert tools["get_weather"] == "llm" and tools["search_advisories"] == "rule"
    assert llm.purposes[:2] == ["query_understanding", "routing"] and "verification" in llm.purposes
    assert result.internals["usage"]["total_tokens"] > 0


async def test_unsupported_claim_triggers_revision():
    await _seed()
    llm = FakeLLM("Bahadur Shah Zafar Marg is closed between Delhi Gate and ITO from 10 am [1]. The Yellow Line metro is suspended all day [1].", unsupported_index=1)
    result = RunResult()
    events = [e async for e in run("Any diversions near ITO today?", [], RunConfig(now=NOW, use_cache=False), llm=llm, result=result)]
    revisions = [e for e in events if e["event"] == "revision"]
    assert revisions and "(Unconfirmed)" in revisions[0]["data"]["text"]
    assert result.faithfulness == 0.5


def test_plan_calls_caps_and_keeps_rule_tools():
    class QU:
        intent = "live_conditions"

    calls = plan_calls(QU(), [("check_route", {}), ("typical_conditions", {})])  # type: ignore[arg-type]
    names = [c.name for c in calls]
    assert len(calls) == 4
    assert {"get_live_traffic", "get_active_incidents", "search_advisories", "get_weather"} == set(names)

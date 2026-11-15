"""Full answer flow (Section 6) as an async stream of SSE-style events.

Events: status, token, cards, citations, map_features, revision, internals,
done, error. The final ``done`` payload carries everything needed to persist
the assistant message.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.observability.logging import get_logger
from app.observability.metrics import FIRST_TOKEN_LATENCY, STAGE_LATENCY
from app.observability.tracing import Trace
from app.providers.embeddings import get_embedder
from app.providers.llm import LLMClient, LLMUnavailableError, Usage, get_llm
from app.rag import generation as gen
from app.rag.cache import SemanticCache, normalize_query
from app.rag.query_understanding import QueryUnderstanding, heuristic_understanding, understand
from app.rag.retrieval.hybrid import Toggles
from app.rag.router import ToolCall, execute, plan_calls, select_tools
from app.rag.verification import REGENERATE_THRESHOLD, VerificationResult, verify
from app.redis_client import get_redis
from app.timeutil import fmt_ist, now_ist

log = get_logger(__name__)

Event = dict[str, Any]

OUT_OF_SCOPE = {
    "en": 'I can only help with Delhi traffic: diversions, closures, incidents, live congestion, weather impact and trip timing. Try: "Any diversions near ITO today?"',
    "hinglish": 'Main sirf Delhi traffic ke baare mein madad kar sakta hoon — diversions, road closures, live jam, weather aur travel timing. Try: "ITO ke paas aaj koi diversion hai?"',
    "hi": 'मैं केवल दिल्ली ट्रैफिक से जुड़ी जानकारी दे सकता हूँ — डायवर्जन, सड़क बंद, लाइव जाम, मौसम और यात्रा का समय। उदाहरण: "आज आईटीओ के पास कोई डायवर्जन है?"',
}


@dataclass
class RunConfig:
    now: datetime | None = None
    toggles: Toggles = field(default_factory=Toggles)
    use_rewrite: bool = True
    use_verification: bool = True
    use_cache: bool = True
    is_admin: bool = False


@dataclass
class RunResult:
    answer: str = ""
    language: str = "en"
    intent: str = ""
    abstained: bool = False
    cached: bool = False
    faithfulness: float | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    internals: dict[str, Any] = field(default_factory=dict)
    retrieved_chunk_ids: list[int] = field(default_factory=list)
    first_token_ms: int | None = None
    latency_ms: int = 0


def _has_useful_tool_data(calls: list[ToolCall]) -> bool:
    for c in calls:
        if (
            c.name == "search_advisories"
            or not isinstance(c.result, dict)
            or not c.result.get("available")
        ):
            continue
        r = c.result
        if c.name == "get_live_traffic" and r.get("segments"):
            return True
        if c.name == "get_active_incidents" and r.get("count"):
            return True
        if c.name == "check_route" and (
            r.get("advisories") or r.get("incidents") or r.get("segments")
        ):
            return True
        if c.name == "typical_conditions" and r.get("segments"):
            return True
        if c.name == "query_incident_history" and (
            r.get("advisory_and_news_counts") or r.get("tomtom_incident_counts")
        ):
            return True
    return False


async def run(
    question: str,
    history: list[dict[str, str]] | None = None,
    config: RunConfig | None = None,
    llm: LLMClient | None = None,
    result: RunResult | None = None,
) -> AsyncIterator[Event]:
    """Answer one user message; yields events and fills `result`."""
    settings = get_settings()
    cfg = config or RunConfig()
    llm = llm or get_llm()
    res = result if result is not None else RunResult()
    history = history or []
    now = cfg.now or now_ist()
    usage = Usage()
    trace = Trace(name="chat", input=question)
    t_start = time.perf_counter()
    stages: dict[str, float] = {}
    internals: dict[str, Any] = {"features": settings.feature_flags()}

    def lap(name: str, started: float) -> None:
        dt = time.perf_counter() - started
        stages[name] = round(dt * 1000, 1)
        STAGE_LATENCY.labels(name).observe(dt)

    question = question.strip()[: settings.max_query_chars]

    # 6.1 semantic cache --------------------------------------------------
    t0 = time.perf_counter()
    pre = await heuristic_understanding(question, now, history)
    qvec = await get_embedder().embed_query(normalize_query(question))
    cache = SemanticCache(get_redis())
    bucket = pre.cache_bucket()
    hit = None
    sim = 0.0
    if cfg.use_cache and not history:
        hit, sim = await cache.lookup(bucket, qvec)
    lap("cache_lookup", t0)
    internals["cache"] = {
        "hit": hit is not None,
        "bucket": bucket,
        "best_similarity": round(sim, 4),
        "threshold": settings.cache_sim_threshold,
    }
    if hit is not None:
        yield {"event": "status", "data": {"message": "Answer served from cache"}}
        yield {"event": "token", "data": {"text": hit["answer"]}}
        for name in ("cards", "citations", "map_features"):
            yield {"event": name, "data": hit.get(name)}
        res.answer, res.language, res.intent, res.cached = (
            hit["answer"],
            hit["language"],
            hit["intent"],
            True,
        )
        res.citations = hit.get("citations") or []
        res.faithfulness = hit.get("faithfulness")
        internals["cached_internals"] = hit.get("internals")
        internals["latency_ms"] = {
            "total": round((time.perf_counter() - t_start) * 1000, 1),
            **stages,
        }
        res.internals = internals
        res.latency_ms = int(internals["latency_ms"]["total"])
        yield {"event": "internals", "data": internals}
        yield {
            "event": "done",
            "data": {"cached": True, "verified": True, "follow_ups": hit.get("follow_ups", [])},
        }
        return

    # 6.2 query understanding --------------------------------------------
    yield {"event": "status", "data": {"message": "Understanding your question…"}}
    t0 = time.perf_counter()
    qu: QueryUnderstanding = await understand(
        question, now, llm, history, usage, use_rewrite=cfg.use_rewrite
    )
    lap("query_understanding", t0)
    internals["query_understanding"] = qu.to_dict()
    res.language, res.intent = qu.language, qu.intent
    trace.span("query_understanding", qu.to_dict())

    if qu.intent == "out_of_scope":
        msg = OUT_OF_SCOPE.get(qu.language, OUT_OF_SCOPE["en"])
        res.answer, res.abstained = msg, True
        yield {"event": "token", "data": {"text": msg}}
        internals["latency_ms"] = {
            "total": round((time.perf_counter() - t_start) * 1000, 1),
            **stages,
        }
        internals["usage"] = {
            "calls": usage.calls,
            "total_tokens": usage.total_tokens,
            "cost_usd": usage.total_cost,
        }
        res.internals, res.latency_ms = internals, int(internals["latency_ms"]["total"])
        yield {"event": "internals", "data": internals}
        yield {
            "event": "done",
            "data": {"verified": True, "abstained": True, "follow_ups": gen.default_followups(qu)},
        }
        return

    # 6.3 routing + tools -------------------------------------------------
    t0 = time.perf_counter()
    llm_calls = await select_tools(qu, llm, usage)
    calls = plan_calls(qu, llm_calls)
    labels = {
        "search_advisories": "Searching advisories…",
        "get_live_traffic": "Checking live traffic…",
        "get_active_incidents": "Checking incidents…",
        "get_weather": "Checking weather…",
        "check_route": "Checking your route…",
        "typical_conditions": "Looking at typical conditions…",
        "query_incident_history": "Looking up history…",
    }
    yield {
        "event": "status",
        "data": {"message": " ".join(labels.get(c.name, c.name) for c in calls)},
    }
    lap("routing", t0)
    t0 = time.perf_counter()
    routed = await execute(qu, calls, now, cfg.toggles)
    lap("tools", t0)
    internals["tools"] = [c.trace() for c in calls]
    internals["llm_selected_tools"] = [n for n, _ in llm_calls]
    retrieval = routed.retrieval
    chunks = retrieval.candidates if retrieval else []
    internals["retrieval"] = retrieval.trace if retrieval else None
    res.retrieved_chunk_ids = [c.chunk_id for c in chunks]
    trace.span("retrieval", {"chunks": res.retrieved_chunk_ids})

    # 6.4.8 relevance gate -------------------------------------------------
    best = retrieval.best_score if retrieval else 0.0
    useful_tools = _has_useful_tool_data(calls)
    relevant = [c for c in chunks if c.final >= settings.relevance_threshold * 0.5]
    abstain = best < settings.relevance_threshold and not useful_tools
    internals["relevance_gate"] = {
        "best_score": round(best, 4),
        "threshold": settings.relevance_threshold,
        "tool_data": useful_tools,
        "abstain": abstain,
    }
    evidence = gen.build_evidence(relevant if not abstain else chunks[:2], routed)
    for e in evidence:
        if e.chunk_id is not None:
            cand = next((c for c in chunks if c.chunk_id == e.chunk_id), None)
            if cand is not None:
                e.extra["summary"] = cand.metadata.get("summary_en")
    cards = gen.cards(evidence)
    if cards:
        yield {"event": "cards", "data": cards}

    # 6.5 generation --------------------------------------------------------
    messages = gen.build_messages(qu, evidence, fmt_ist(now), history)
    internals["context"] = {
        "evidence_items": len(evidence),
        "approx_tokens": sum(len(m["content"]) for m in messages) // 4,
        "abstain_path": abstain,
    }
    yield {"event": "status", "data": {"message": "Writing the answer…"}}
    t0 = time.perf_counter()
    answer = ""
    follow_ups: list[str] = []
    generated_by = "llm"
    if abstain or not llm.enabled:
        answer = gen.extractive_answer(qu, evidence, abstain)
        generated_by = "abstain" if abstain else "extractive"
        res.first_token_ms = int((time.perf_counter() - t_start) * 1000)
        yield {"event": "token", "data": {"text": answer}}
    else:
        stripper = gen.FollowupStripper()
        try:
            async for tok in gen.stream_answer(llm, messages, usage):
                if res.first_token_ms is None:
                    res.first_token_ms = int((time.perf_counter() - t_start) * 1000)
                    FIRST_TOKEN_LATENCY.observe(res.first_token_ms / 1000)
                visible = stripper.feed(tok)
                if visible:
                    yield {"event": "token", "data": {"text": visible}}
            tail = stripper.flush()
            if tail:
                yield {"event": "token", "data": {"text": tail}}
            answer = stripper.answer_text()
            follow_ups = stripper.followups()
        except LLMUnavailableError as exc:
            log.warning("generation failed; extractive fallback", extra={"err": str(exc)[:200]})
            answer = gen.extractive_answer(qu, evidence, abstain=False)
            generated_by = "extractive"
            yield {"event": "revision", "data": {"text": answer, "reason": "generation_failed"}}
    lap("generation", t0)
    internals["generation"] = {"by": generated_by}

    # 6.6 verification --------------------------------------------------------
    t0 = time.perf_counter()
    verification: VerificationResult = await verify(
        answer, evidence, llm, usage, enabled=cfg.use_verification and generated_by != "abstain"
    )
    final_answer = answer
    if (
        verification.changed
        and verification.unsupported_ratio > REGENERATE_THRESHOLD
        and llm.enabled
    ):
        yield {"event": "status", "data": {"message": "Re-checking the answer against sources…"}}
        try:
            strict = gen.build_messages(qu, evidence, fmt_ist(now), history, strict=True)
            regen = await llm.complete_text(
                strict,
                "answer",
                usage=usage,
                purpose="regeneration",
                max_tokens=900,
                temperature=0.0,
            )
            stripper = gen.FollowupStripper()
            stripper.feed(regen)
            regen_text = stripper.answer_text()
            follow_ups = stripper.followups() or follow_ups
            second = await verify(regen_text, evidence, llm, usage)
            internals["verification_first_pass"] = verification.to_dict()
            verification = second
            final_answer = second.revised_text if second.changed else regen_text
            internals["regenerated"] = True
        except LLMUnavailableError:
            final_answer = verification.revised_text
    elif verification.changed:
        final_answer = verification.revised_text
    if final_answer != answer:
        yield {"event": "revision", "data": {"text": final_answer, "reason": "verification"}}
    lap("verification", t0)
    internals["verification"] = verification.to_dict()

    # structured outputs -------------------------------------------------------
    cited = gen.cited_ids(final_answer)
    citations = [e.citation() for e in evidence if (not cited or e.n in cited)]
    features = gen.map_features(evidence, qu, cited)
    follow_ups = follow_ups or gen.default_followups(qu)
    yield {"event": "citations", "data": citations}
    yield {"event": "map_features", "data": features}

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    internals["latency_ms"] = {"total": total_ms, "first_token": res.first_token_ms, **stages}
    internals["usage"] = {
        "calls": usage.calls,
        "total_tokens": usage.total_tokens,
        "cost_usd": usage.total_cost,
    }
    trace_url = trace.finish(output=final_answer, usage=usage)
    if cfg.is_admin and trace_url:
        internals["langfuse_trace_url"] = trace_url
    yield {"event": "internals", "data": internals}

    res.answer = final_answer
    res.abstained = abstain
    res.faithfulness = (
        verification.faithfulness if verification.method not in ("disabled", "none") else None
    )
    res.citations = citations
    res.internals = internals
    res.latency_ms = int(total_ms)
    verified = verification.method in ("llm", "lexical") and verification.faithfulness >= 0.7

    if cfg.use_cache and not abstain and generated_by == "llm":
        await cache.store(
            bucket,
            qu.intent,
            qvec,
            {
                "answer": final_answer,
                "language": qu.language,
                "intent": qu.intent,
                "citations": citations,
                "map_features": features,
                "cards": cards,
                "follow_ups": follow_ups,
                "faithfulness": res.faithfulness,
                "internals": {"cached_from": now.isoformat()},
            },
        )
    yield {
        "event": "done",
        "data": {
            "verified": verified,
            "abstained": abstain,
            "faithfulness": res.faithfulness,
            "follow_ups": follow_ups,
        },
    }

"""Admin endpoints (X-Admin-Token / Bearer ADMIN_TOKEN)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.models.ops import EvalQuestion
from app.db.session import get_db
from app.ingestion.sources.tomtom import BudgetManager
from app.observability.metrics import CACHE_LOOKUPS
from app.redis_client import get_redis
from app.timeutil import utcnow

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _counter_value(counter: Any, label: str) -> float:
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels.get("result") == label:
                return float(sample.value)
    return 0.0


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    sources = (await db.execute(text(
        """SELECT s.id, s.name, s.kind, s.enabled, s.last_run_at, s.last_status, s.last_error,
                  (SELECT count(*) FROM documents d WHERE d.source_id = s.id) AS documents,
                  (SELECT count(*) FROM ingestion_runs r WHERE r.source_id = s.id AND r.status = 'error'
                     AND r.started_at > now() - interval '24 hours') AS errors_24h
           FROM sources s ORDER BY s.id"""
    ))).mappings().all()
    counts = (await db.execute(text(
        """SELECT (SELECT count(*) FROM documents) AS documents, (SELECT count(*) FROM chunks) AS chunks,
                  (SELECT count(*) FROM chunks WHERE is_active) AS active_chunks,
                  (SELECT count(*) FROM places) AS places, (SELECT count(*) FROM traffic_incidents WHERE is_active) AS active_incidents,
                  (SELECT count(*) FROM traffic_flow_obs WHERE observed_at > now() - interval '24 hours') AS flow_obs_24h,
                  (SELECT count(*) FROM messages WHERE role='assistant') AS answers,
                  (SELECT count(*) FROM saved_routes) AS saved_routes"""
    ))).mappings().first()
    volume = (await db.execute(text(
        """SELECT date_trunc('day', created_at AT TIME ZONE 'Asia/Kolkata')::date AS day, source_id, count(*) AS n
           FROM documents WHERE created_at > now() - interval '14 days' GROUP BY 1, 2 ORDER BY 1"""
    ))).mappings().all()
    latency = (await db.execute(text(
        """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY first_token_ms) AS ft_p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY first_token_ms) AS ft_p95,
                  avg(CASE WHEN cached THEN 1.0 ELSE 0.0 END) AS cache_rate, count(*) AS n
           FROM messages WHERE role='assistant' AND created_at > now() - interval '7 days'"""
    ))).mappings().first()
    hits, misses = _counter_value(CACHE_LOOKUPS, "hit"), _counter_value(CACHE_LOOKUPS, "miss")
    try:
        budget = await BudgetManager(get_redis()).status()
    except Exception:
        budget = {"error": "redis unavailable"}
    return {
        "sources": [dict(s) for s in sources],
        "counts": dict(counts or {}),
        "ingestion_volume": [dict(v) for v in volume],
        "latency": dict(latency or {}),
        "cache": {"process_hits": hits, "process_misses": misses, "hit_rate_7d": float((latency or {}).get("cache_rate") or 0)},
        "tomtom_budget": budget,
    }


@router.get("/ingestion-runs")
async def ingestion_runs(limit: int = 50, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        "SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT :l"), {"l": min(limit, 200)})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/sources/{source_id}/toggle")
async def toggle_source(source_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = (await db.execute(text("UPDATE sources SET enabled = NOT enabled WHERE id = :id RETURNING enabled"), {"id": source_id})).first()
    if row is None:
        raise HTTPException(404, "unknown source")
    return {"id": source_id, "enabled": row[0]}


@router.get("/answers/worst")
async def worst_answers(limit: int = 20, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        """SELECT m.id, m.created_at, m.intent, m.language, m.faithfulness, m.abstained, m.latency_ms,
                  left(m.content, 400) AS answer,
                  (SELECT content FROM messages u WHERE u.conversation_id = m.conversation_id AND u.role='user'
                     AND u.created_at <= m.created_at ORDER BY u.created_at DESC LIMIT 1) AS question,
                  f.rating, f.comment, f.in_review_queue
           FROM messages m LEFT JOIN feedback f ON f.message_id = m.id
           WHERE m.role = 'assistant' AND m.created_at > now() - interval '30 days'
             AND (m.faithfulness IS NOT NULL OR f.rating IS NOT NULL)
           ORDER BY COALESCE(f.rating, 0) ASC, m.faithfulness ASC NULLS LAST LIMIT :l"""
    ), {"l": min(limit, 100)})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/messages/{message_id}/internals")
async def message_internals(message_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = (await db.execute(text("SELECT internals FROM messages WHERE id = CAST(:id AS uuid)"), {"id": message_id})).first()
    if row is None:
        raise HTTPException(404, "not found")
    return row[0]


@router.get("/eval/runs")
async def eval_runs(limit: int = 30, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text(
        "SELECT id, started_at, finished_at, label, metrics, n_questions, git_sha, config FROM eval_runs ORDER BY started_at DESC LIMIT :l"
    ), {"l": min(limit, 200)})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/eval/questions")
async def eval_questions(status: str | None = None, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM eval_questions"
    params: dict[str, Any] = {}
    if status:
        sql += " WHERE status = :s"
        params["s"] = status
    rows = (await db.execute(text(sql + " ORDER BY created_at DESC LIMIT 500"), params)).mappings().all()
    return [dict(r) for r in rows]


class ReviewIn(BaseModel):
    status: Literal["approved", "rejected", "draft"]
    question: str | None = Field(None, max_length=1000)
    expected_answer: str | None = Field(None, max_length=4000)
    unanswerable: bool | None = None


@router.patch("/eval/questions/{qid}")
async def review_question(qid: str, body: ReviewIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await db.get(EvalQuestion, qid)
    if q is None:
        raise HTTPException(404, "not found")
    q.status = body.status
    if body.question:
        q.question = body.question
    if body.expected_answer is not None:
        q.expected_answer = body.expected_answer
    if body.unanswerable is not None:
        q.unanswerable = body.unanswerable
    return {"id": q.id, "status": q.status}


@router.post("/review-queue/{feedback_id}/promote")
async def promote_feedback(feedback_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Turn a thumbs-down answer into a draft eval question."""
    row = (await db.execute(text(
        """SELECT f.id, m.intent, m.language, m.content AS answer,
                  (SELECT content FROM messages u WHERE u.conversation_id = m.conversation_id AND u.role='user'
                     AND u.created_at <= m.created_at ORDER BY u.created_at DESC LIMIT 1) AS question
           FROM feedback f JOIN messages m ON m.id = f.message_id WHERE f.id = :id"""
    ), {"id": feedback_id})).first()
    if row is None or not row.question:
        raise HTTPException(404, "feedback not found")
    qid = f"fb-{feedback_id}"
    if await db.get(EvalQuestion, qid) is None:
        db.add(EvalQuestion(id=qid, question=row.question, expected_answer=None, intent=row.intent or "general_info",
                            language=row.language or "en", status="draft", origin="feedback", eval_clock=utcnow()))
    await db.execute(text("UPDATE feedback SET in_review_queue = false WHERE id = :id"), {"id": feedback_id})
    return {"eval_question_id": qid}


class SimulateIn(BaseModel):
    lat: float
    lon: float
    title: str = "Simulated closure for alert testing"
    hours: int = Field(3, ge=1, le=48)


@router.post("/simulate-event")
async def simulate_event(body: SimulateIn) -> dict[str, Any]:
    """Inject a simulated official advisory at a point and run the alert matcher on it."""
    from app.alerts.matcher import dispatch, match_chunks
    from app.db.session import session_scope
    from app.ingestion.pipeline import DocumentDraft, process

    now = utcnow()
    draft = DocumentDraft(
        source_id="system", url=f"simulated:{now.timestamp()}", title=body.title, trust_level="official",
        content_type="text", body_text=f"{body.title}. Road closed near the marked point; expect diversions.",
        published_at=now, authority="Simulation", valid_from_hint=now, valid_to_hint=now + timedelta(hours=body.hours),
        metadata={"simulated": True},
    )
    async with session_scope() as s:
        res = await process(s, draft)
        await s.execute(text("UPDATE chunks SET geom = ST_SetSRID(ST_MakePoint(:lon,:lat),4326), valid_from=:vf, valid_to=:vt WHERE id = ANY(:ids)"),
                        {"lon": body.lon, "lat": body.lat, "ids": res.chunk_ids, "vf": now, "vt": now + timedelta(hours=body.hours)})
    out = await dispatch(await match_chunks(res.chunk_ids))
    return {"document_id": res.document_id, "chunk_ids": res.chunk_ids, **out}

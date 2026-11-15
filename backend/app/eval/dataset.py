"""Eval datasets: frozen fixture corpus loader, LLM question drafting, import/export."""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.db.models.ops import EvalQuestion
from app.db.session import session_scope
from app.ingestion.pipeline import DocumentDraft, process
from app.observability.logging import get_logger
from app.providers.llm import get_llm
from app.timeutil import IST

log = get_logger(__name__)


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "data" / "seed").exists():
            return p
    return Path.cwd()


def fixture_path() -> Path:
    return repo_root() / "data" / "seed" / "eval_fixture.yaml"


def questions_path() -> Path:
    return repo_root() / "data" / "seed" / "eval_questions.jsonl"


def load_questions() -> list[dict[str, Any]]:
    return [json.loads(line) for line in questions_path().read_text(encoding="utf-8").splitlines() if line.strip()]


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=IST)


async def load_fixture_corpus() -> dict[str, int]:
    """(Re)load the frozen fixture documents into the *current* (eval) database."""
    data = yaml.safe_load(fixture_path().read_text(encoding="utf-8"))
    async with session_scope() as s:
        await s.execute(text("INSERT INTO sources (id, name, kind, trust_level, enabled) VALUES ('eval_fixture','Eval fixture','advisory','official',true) ON CONFLICT DO NOTHING"))
    loaded = 0
    for d in data["documents"]:
        vf, vt = (d.get("valid") or [None, None])
        draft = DocumentDraft(
            source_id="eval_fixture",
            url=f"fixture://{d['id']}",
            title=d["title"],
            trust_level=d["trust"],
            content_type="text",
            body_text=f"{d['title']}\n\n{d['text']}",
            published_at=_dt(d["published"]),
            authority="Delhi Traffic Police" if d["trust"] == "official" else ("TomTom Traffic" if d["trust"] == "sensor" else None),
            publisher=d.get("publisher"),
            language=d["lang"],
            valid_from_hint=_dt(vf) if vf else None,
            valid_to_hint=_dt(vt) if vt else None,
            metadata={"fixture": True},
        )
        async with session_scope() as s:
            res = await process(s, draft)
            if vf or vt:  # fixture validity is ground truth; don't let enrichment drift it
                await s.execute(text("UPDATE chunks SET valid_from = :vf, valid_to = :vt WHERE document_id = :doc"),
                                {"vf": _dt(vf) if vf else None, "vt": _dt(vt) if vt else None, "doc": res.document_id})
        loaded += 1
    return {"documents": loaded}


class DraftQuestion(BaseModel):
    question: str
    expected_answer: str
    language: str = Field(pattern="^(en|hi|hinglish)$")
    intent: str


class DraftSet(BaseModel):
    questions: list[DraftQuestion]


async def generate_questions(n: int = 160, seed: int = 7) -> dict[str, Any]:
    """Draft eval questions from sampled live documents (status=draft, for review)."""
    llm = get_llm()
    if not llm.enabled:
        return {"error": "LLM not configured; set GROQ_API_KEY"}
    async with session_scope() as s:
        docs = (await s.execute(text(
            """SELECT d.id, d.url, d.title, d.published_at, string_agg(c.text, E'\n' ORDER BY c.ordinal) AS body,
                      array_agg(c.id) AS chunk_ids
               FROM documents d JOIN chunks c ON c.document_id = d.id
               WHERE d.source_id <> 'eval_fixture' AND d.status = 'ready'
               GROUP BY d.id ORDER BY d.id"""
        ))).all()
    rng = random.Random(seed)
    rng.shuffle(docs)
    created = 0
    for d in docs:
        if created >= n:
            break
        prompt = (
            "Write 3 questions a Delhi commuter might ask that this document answers: one English, one Hindi "
            "(Devanagari), one Hinglish. Vary intents (advisory_lookup, live_conditions, plan_trip, historical_lookup, "
            "cause_explanation). Include the expected answer (1-2 sentences, from the document only).\n\n"
            f"Published: {d.published_at}\nTitle: {d.title}\n<document>{(d.body or '')[:3000]}</document>"
        )
        try:
            out = await llm.complete_json([{"role": "user", "content": prompt}], DraftSet, role="fast", purpose="eval_generation")
        except Exception as exc:
            log.warning("question drafting failed", extra={"doc": d.id, "err": str(exc)[:200]})
            continue
        async with session_scope() as s:
            for i, q in enumerate(out.questions[:3]):
                qid = f"live-{d.id}-{i}"
                if await s.get(EvalQuestion, qid):
                    continue
                s.add(EvalQuestion(
                    id=qid, question=q.question, expected_answer=q.expected_answer, gold_chunk_ids=list(d.chunk_ids),
                    gold_urls=[d.url], intent=q.intent, language=q.language, eval_clock=d.published_at,
                    status="draft", origin="generated", tags=["live"],
                ))
                created += 1
    return {"drafted": created, "review_at": "/admin (Eval review tab)"}


async def import_jsonl() -> dict[str, int]:
    n = 0
    async with session_scope() as s:
        for r in load_questions():
            if await s.get(EvalQuestion, r["id"]):
                continue
            s.add(EvalQuestion(
                id=r["id"], question=r["question"], expected_answer=r.get("expected_answer"),
                gold_urls=r.get("gold_urls", []), intent=r["intent"], language=r["language"],
                unanswerable=r.get("unanswerable", False), tags=r.get("tags", []),
                eval_clock=_dt(r["eval_clock"]) if r.get("eval_clock") else None, status="approved", origin="manual",
            ))
            n += 1
    return {"imported": n}


async def export_approved() -> dict[str, Any]:
    """Merge approved DB questions (live/feedback) into eval_questions.jsonl."""
    existing = {r["id"]: r for r in load_questions()}
    async with session_scope() as s:
        rows = (await s.execute(select(EvalQuestion).where(EvalQuestion.status == "approved"))).scalars().all()
    for q in rows:
        existing[q.id] = {
            "id": q.id, "question": q.question, "language": q.language, "intent": q.intent,
            "eval_clock": q.eval_clock.isoformat() if q.eval_clock else None, "gold_urls": q.gold_urls,
            "unanswerable": q.unanswerable, "out_of_scope": q.intent == "out_of_scope", "tags": q.tags,
            "expected_answer": q.expected_answer,
        }
    questions_path().write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in existing.values()) + "\n", encoding="utf-8")
    return {"total": len(existing)}

"""Operational models: ingestion runs, evaluation questions/runs/results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error|skipped
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class EvalQuestion(Base, TimestampMixin):
    """Eval question under review (approved ones are exported to eval_questions.jsonl)."""

    __tablename__ = "eval_questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    gold_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    gold_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    intent: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(12))
    unanswerable: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    eval_clock: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|rejected
    origin: Mapped[str] = mapped_column(
        String(16), default="generated"
    )  # generated|feedback|manual


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    label: Mapped[str] = mapped_column(String(64), default="full")  # full|smoke|ablation name
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    n_questions: Mapped[int] = mapped_column(Integer, default=0)
    git_sha: Mapped[str | None] = mapped_column(String(40))


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(Text, default="")
    retrieved_chunk_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Float)

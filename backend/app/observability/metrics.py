"""Prometheus metrics shared by the API and worker."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUEST_LATENCY = Histogram(
    "http_request_seconds", "HTTP request latency", ["method", "route", "status"]
)
STAGE_LATENCY = Histogram(
    "rag_stage_seconds",
    "Latency of RAG pipeline stages",
    ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)
CACHE_LOOKUPS = Counter("rag_cache_lookups_total", "Semantic cache lookups", ["result"])
INGEST_ITEMS = Counter("ingest_items_total", "Ingested items", ["source", "outcome"])
INGEST_ERRORS = Counter("ingest_errors_total", "Ingestion errors", ["source"])
TOMTOM_BUDGET_USED = Gauge("tomtom_budget_used", "TomTom requests used today")
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "kind"])
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["model", "outcome"])
FIRST_TOKEN_LATENCY = Histogram(
    "chat_first_token_seconds", "Time to first streamed token", buckets=(0.5, 1, 2, 3, 4, 6, 8, 12)
)

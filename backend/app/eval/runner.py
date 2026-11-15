"""Evaluation runner: frozen clock + isolated DB snapshot, metrics, ablations, CI gate.

Reproducibility: the fixture corpus is loaded into a separate database
(``EVAL_DATABASE_URL``); each question runs with its own frozen ``eval_clock``;
the semantic cache is disabled. Results are written to
``backend/eval_reports/*.json``, summarised in ``docs/evaluation.md`` and
recorded in the live DB ``eval_runs`` table for the admin dashboard.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_engine, get_sessionmaker, session_scope
from app.eval import metrics as M
from app.eval.dataset import fixture_path, load_fixture_corpus, load_questions, repo_root
from app.observability.logging import get_logger
from app.rag.orchestrator import RunConfig, RunResult, run
from app.rag.retrieval.hybrid import Toggles

log = get_logger(__name__)

ABLATIONS = ["no-rerank", "no-hybrid", "no-geo-filter", "no-freshness", "no-query-rewrite", "no-question-index", "no-verification"]
_LIVE_DB_URL: str | None = None


def _clear_caches() -> None:
    from app.ingestion import geocoding

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    geocoding._geocoder = None


def use_eval_db() -> str:
    """Point this process at the isolated eval database (create + migrate if needed)."""
    global _LIVE_DB_URL
    settings = get_settings()
    _LIVE_DB_URL = settings.database_url
    eval_url = settings.resolved_eval_database_url
    os.environ["DATABASE_URL"] = eval_url
    os.environ["PUBLISH_EVENTS"] = "false"
    _clear_caches()
    import psycopg

    base, _, name = get_settings().sync_database_url.replace("+psycopg", "").rpartition("/")
    with psycopg.connect(f"{base}/postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')
            log.info("created eval database", extra={"db": name})
    backend_dir = Path(__file__).resolve().parents[2]
    subprocess.run(["alembic", "upgrade", "head"], cwd=backend_dir, check=True, env={**os.environ})
    return eval_url


def _fixture_hash() -> str:
    return hashlib.sha256(fixture_path().read_bytes()).hexdigest()[:16]


async def ensure_fixture() -> dict[str, Any]:
    """Load the fixture corpus once per fixture version (hash stored on the source row)."""
    digest = _fixture_hash()
    async with session_scope() as s:
        row = (await s.execute(text("SELECT config->>'hash' FROM sources WHERE id = 'eval_fixture'"))).scalar_one_or_none()
        if row == digest:
            return {"fixture": "up-to-date", "hash": digest}
        await s.execute(text("DELETE FROM documents WHERE source_id = 'eval_fixture'"))
    from app.ingestion.jobs import ensure_sources
    from app.ingestion.sources.osm_gazetteer import upsert_seed

    async with session_scope() as s:
        await ensure_sources(s)
        await upsert_seed(s)
    from app.ingestion.geocoding import refresh_geocoder

    async with session_scope() as s:
        await refresh_geocoder(s)
    out = await load_fixture_corpus()
    async with session_scope() as s:
        await s.execute(text("UPDATE sources SET config = jsonb_build_object('hash', CAST(:h AS text)) WHERE id = 'eval_fixture'"), {"h": digest})
    return {"fixture": "loaded", "hash": digest, **out}


def _config_for(ablation: str | None) -> RunConfig:
    t = Toggles()
    cfg = RunConfig(toggles=t, use_cache=False)
    if ablation == "no-rerank":
        t.rerank = False
    elif ablation == "no-hybrid":
        t.hybrid = False
    elif ablation == "no-geo-filter":
        t.geo_filter = False
    elif ablation == "no-freshness":
        t.freshness = False
    elif ablation == "no-question-index":
        t.question_index = False
    elif ablation == "no-query-rewrite":
        cfg.use_rewrite = False
    elif ablation == "no-verification":
        cfg.use_verification = False
    return cfg


ABSTAIN_MARKERS = ("don't have enough", "do not have enough", "not enough reliable", "पर्याप्त", "no active advisories", "couldn't find", "could not find")


async def _doc_urls(chunk_ids: list[int]) -> list[str]:
    if not chunk_ids:
        return []
    async with session_scope() as s:
        rows = (await s.execute(text("SELECT c.id, d.url FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(:ids)"), {"ids": chunk_ids})).all()
    by_id = {r.id: r.url for r in rows}
    return list(dict.fromkeys(by_id[c] for c in chunk_ids if c in by_id))


async def _cluster_urls(gold: set[str]) -> set[str]:
    """Gold also covers dedup-cluster siblings (retrieval returns canonical members only)."""
    if not gold:
        return gold
    async with session_scope() as s:
        rows = await s.execute(text(
            """SELECT DISTINCT d2.url FROM documents d JOIN chunks c ON c.document_id = d.id
               JOIN chunks c2 ON c2.dedup_cluster_id = c.dedup_cluster_id JOIN documents d2 ON d2.id = c2.document_id
               WHERE d.url = ANY(:u)"""
        ), {"u": list(gold)})
        return gold | {r[0] for r in rows}


async def evaluate_question(q: dict[str, Any], cfg: RunConfig) -> dict[str, Any]:
    cfg.now = datetime.fromisoformat(q["eval_clock"])
    result = RunResult()
    started = time.perf_counter()
    async for _ in run(q["question"], [], cfg, result=result):
        pass
    elapsed = (time.perf_counter() - started) * 1000
    retrieved = await _doc_urls(result.retrieved_chunk_ids)
    gold = await _cluster_urls(set(q.get("gold_urls", [])))
    answer_l = result.answer.lower()
    abstained = result.abstained or any(m in answer_l for m in ABSTAIN_MARKERS)
    expect_abstain = bool(q.get("unanswerable") or q.get("out_of_scope"))
    verification = (result.internals or {}).get("verification") or {}
    claims = verification.get("claims") or []
    cited_doc_urls = [c.get("url") or "" for c in result.citations if c.get("kind") == "chunk"]
    row: dict[str, Any] = {
        "id": q["id"], "question": q["question"], "intent_expected": q["intent"], "intent": result.intent,
        "language": q["language"], "answer": result.answer[:1500], "retrieved": retrieved[:10],
        "abstained": abstained, "expect_abstain": expect_abstain,
        "abstention_correct": abstained == expect_abstain,
        "language_match": M.language_match(result.answer, q["language"]) if not q.get("out_of_scope") else True,
        "faithfulness": result.faithfulness if result.faithfulness is not None else (1.0 if abstained else None),
        "latency_ms": round(elapsed, 1), "first_token_ms": result.first_token_ms,
        "cost_usd": ((result.internals or {}).get("usage") or {}).get("cost_usd", 0.0),
        "tags": q.get("tags", []),
    }
    if gold:
        row.update({
            "recall@5": M.recall_at_k(retrieved, gold, 5), "recall@10": M.recall_at_k(retrieved, gold, 10),
            "mrr": M.mrr(retrieved, gold), "ndcg@10": M.ndcg_at_k(retrieved, gold, 10),
            "context_precision": M.context_precision(retrieved, gold),
            "citation_accuracy": M.citation_accuracy(cited_doc_urls, gold, [c["supported"] for c in claims if c.get("citations")]),
            "answer_relevancy": M.answer_relevancy(q["question"], result.answer),
        })
    if q.get("out_of_scope"):
        row["oos_correct"] = result.intent == "out_of_scope"
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ans = [r for r in rows if "recall@10" in r]
    def avg(key: str, pool: list[dict[str, Any]]) -> float:
        vals = [r[key] for r in pool if r.get(key) is not None]
        return round(M.mean(vals), 4)
    lat = [r["latency_ms"] for r in rows]
    ftl = [r["first_token_ms"] for r in rows if r.get("first_token_ms")]
    return {
        "n": len(rows), "n_answerable": len(ans),
        "recall@5": avg("recall@5", ans), "recall@10": avg("recall@10", ans), "mrr": avg("mrr", ans),
        "ndcg@10": avg("ndcg@10", ans), "context_precision": avg("context_precision", ans),
        "faithfulness": avg("faithfulness", rows), "citation_accuracy": avg("citation_accuracy", ans),
        "answer_relevancy": avg("answer_relevancy", ans),
        "abstention_accuracy": round(M.mean([1.0 if r["abstention_correct"] else 0.0 for r in rows]), 4),
        "unanswerable_abstain_rate": round(M.mean([1.0 if r["abstained"] else 0.0 for r in rows if r["expect_abstain"]]), 4),
        "language_match": round(M.mean([1.0 if r["language_match"] else 0.0 for r in rows]), 4),
        "intent_accuracy": round(M.mean([1.0 if r["intent"] == r["intent_expected"] else 0.0 for r in rows]), 4),
        "latency_p50_ms": M.percentile(lat, 50), "latency_p95_ms": M.percentile(lat, 95),
        "first_token_p50_ms": M.percentile(ftl, 50), "first_token_p95_ms": M.percentile(ftl, 95),
        "cost_per_query_usd": round(M.mean([r["cost_usd"] for r in rows]), 6),
    }


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=repo_root(), check=True).stdout.strip()
    except Exception:
        return None


async def _record_live(label: str, cfg_dict: dict[str, Any], agg: dict[str, Any], rows: list[dict[str, Any]], started: datetime) -> None:
    """Store the run in the live DB (admin dashboard reads it there)."""
    if not _LIVE_DB_URL:
        return
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_LIVE_DB_URL)
    try:
        async with engine.begin() as conn:
            run_id = (await conn.execute(text(
                """INSERT INTO eval_runs (started_at, finished_at, label, config, metrics, n_questions, git_sha)
                   VALUES (:s, now(), :l, CAST(:c AS jsonb), CAST(:m AS jsonb), :n, :g) RETURNING id"""
            ), {"s": started, "l": label, "c": json.dumps(cfg_dict), "m": json.dumps(agg), "n": len(rows), "g": _git_sha()})).scalar_one()
            for r in rows:
                await conn.execute(text(
                    """INSERT INTO eval_results (run_id, question_id, answer, retrieved_chunk_ids, metrics, latency_ms)
                       VALUES (:r, :q, :a, '{}', CAST(:m AS jsonb), :l)"""
                ), {"r": run_id, "q": r["id"], "a": r["answer"], "m": json.dumps({k: v for k, v in r.items() if k not in ("answer",)}, default=str), "l": r["latency_ms"]})
    except Exception as exc:
        log.warning("could not record eval run in live DB", extra={"err": str(exc)[:200]})
    finally:
        await engine.dispose()


def _baseline_path() -> Path:
    return repo_root() / "data" / "seed" / "eval_baseline.json"


def check_gate(agg: dict[str, Any], subset: str) -> dict[str, Any]:
    path = _baseline_path()
    if not path.exists():
        return {"passed": True, "reason": "no baseline yet"}
    baseline = json.loads(path.read_text()).get(subset)
    if not baseline:
        return {"passed": True, "reason": f"no baseline for subset {subset}"}
    margin = get_settings().eval_regression_margin
    failures = []
    for key in ("faithfulness", "recall@10"):
        if agg[key] < baseline[key] - margin:
            failures.append(f"{key} {agg[key]:.3f} < baseline {baseline[key]:.3f} - {margin}")
    return {"passed": not failures, "failures": failures, "baseline": {k: baseline[k] for k in ("faithfulness", "recall@10")}}


async def run_eval(
    subset: str = "full", label: str = "full", gate: bool = False, update_baseline: bool = False,
    ablation: str | None = None, write_report: bool = True,
) -> dict[str, Any]:
    if _LIVE_DB_URL is None:
        use_eval_db()
    fixture = await ensure_fixture()
    questions = load_questions()
    if subset == "smoke":
        questions = [q for q in questions if "smoke" in q.get("tags", [])]
    elif subset == "retrieval":
        questions = [q for q in questions if q.get("gold_urls")][:50]
    cfg = _config_for(ablation)
    started = datetime.now().astimezone()
    rows = []
    for i, q in enumerate(questions, start=1):
        try:
            rows.append(await evaluate_question(q, _config_for(ablation)))
        except Exception as exc:
            log.error("eval question failed", extra={"id": q["id"], "err": f"{type(exc).__name__}: {exc}"[:300]})
        if i % 10 == 0:
            log.info("eval progress", extra={"done": i, "total": len(questions)})
    agg = aggregate(rows)
    cfg_dict = {"subset": subset, "ablation": ablation, "toggles": asdict(cfg.toggles), "use_rewrite": cfg.use_rewrite,
                "use_verification": cfg.use_verification, "llm": get_settings().llm_enabled,
                "embedding": get_settings().embedding_provider, "reranker": get_settings().reranker_provider}
    report = {"label": label, "config": cfg_dict, "fixture": fixture, "metrics": agg}
    if gate:
        report["gate"] = check_gate(agg, subset)
    if update_baseline:
        path = _baseline_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        data[subset] = agg
        path.write_text(json.dumps(data, indent=2) + "\n")
        report["baseline_updated"] = True
    if write_report:
        out_dir = Path(__file__).resolve().parents[2] / "eval_reports"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{started:%Y%m%d-%H%M%S}-{label}.json").write_text(json.dumps({**report, "rows": rows}, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        _write_markdown_section("main", label, {label: agg}, cfg_dict)
    await _record_live(label, cfg_dict, agg, rows, started)
    return report


async def run_ablations(names: list[str], subset: str = "full") -> dict[str, Any]:
    use_eval_db()
    if names == ["all"] or "all" in names:
        names = ABLATIONS
    results: dict[str, dict[str, Any]] = {}
    results["full system"] = (await run_eval(subset=subset, label=f"{subset}-baseline", write_report=False))["metrics"]
    for name in names:
        results[name] = (await run_eval(subset=subset, label=f"{subset}-{name}", ablation=name, write_report=False))["metrics"]
    _write_markdown_section("ablation", subset, results, {"subset": subset, "llm": get_settings().llm_enabled,
                            "embedding": get_settings().embedding_provider, "reranker": get_settings().reranker_provider})
    return {"ablations": results}


TABLE_KEYS = ["recall@5", "recall@10", "mrr", "ndcg@10", "faithfulness", "citation_accuracy", "abstention_accuracy", "language_match", "latency_p95_ms"]


def _write_markdown_section(kind: str, label: str, results: dict[str, dict[str, Any]], cfg: dict[str, Any]) -> None:
    path = repo_root() / "docs" / "evaluation.md"
    content = path.read_text(encoding="utf-8") if path.exists() else "# Evaluation\n"
    start, end = f"<!-- eval:{kind}:start -->", f"<!-- eval:{kind}:end -->"
    header = "| Configuration | " + " | ".join(TABLE_KEYS) + " |\n|---|" + "---|" * len(TABLE_KEYS) + "\n"
    rows = "".join(
        f"| {name} | " + " | ".join(f"{m.get(k, 0):.3f}" if isinstance(m.get(k), float) and k != "latency_p95_ms" else str(m.get(k)) for k in TABLE_KEYS) + " |\n"
        for name, m in results.items()
    )
    stamp = f"_Last run: {datetime.now():%Y-%m-%d %H:%M} · label `{label}` · config `{json.dumps(cfg)}` · git `{_git_sha()}`_\n\n"
    block = f"{start}\n{stamp}{header}{rows}{end}"
    if start in content and end in content:
        pre, rest = content.split(start, 1)
        _, post = rest.split(end, 1)
        content = pre + block + post
    else:
        content += f"\n{block}\n"
    path.write_text(content, encoding="utf-8")

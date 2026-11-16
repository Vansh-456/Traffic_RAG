# Evaluation

Reproducible, offline evaluation of retrieval, answers and abstention. Tables between the
`<!-- eval:* -->` markers are rewritten by the runner; the prose is maintained by hand.

## Setup

- **Corpus ("DB snapshot")**: `data/seed/eval_fixture.yaml`, 32 *synthetic* documents modelled on
  real Delhi Traffic Police advisory patterns (official advisories in English and Hindi, news
  reports, TomTom-style incident texts), loaded into an **isolated database** (`EVAL_DATABASE_URL`,
  default `copilot_eval`) through the normal ingestion pipeline. The live app database is never touched.
- **Questions**: `data/seed/eval_questions.jsonl`, 153 questions (99 English, 25 Hindi,
  29 Hinglish) covering all 7 intents, location-specific and time-relative wording ("kal",
  "last Tuesday evening", "6 pm Friday"), compound questions, 14 unanswerable questions (expected
  abstention) and 8 out-of-scope questions. The first 30 (tag `smoke`) form the CI subset.
- **Frozen clock**: every question carries its `eval_clock` (e.g. Tue 10 Nov 2026 09:00 IST), and
  the semantic cache is disabled, so runs are reproducible.
- **Gold**: document URLs (`fixture://dNN`), expanded with each gold document's dedup cluster
  (retrieval returns canonical members only).

```bash
copilot eval run --subset smoke --gate          # CI gate (fails on regression vs baseline)
copilot eval run --subset full                  # full report → backend/eval_reports/*.json + tables below
copilot eval run --subset full --ablation all   # ablation table below
copilot eval generate-questions                 # LLM-drafted questions from *live* data → admin review → export
```

## Metrics

| Metric | Definition |
|---|---|
| Recall@5 / @10, MRR, nDCG@10 | over retrieved document URLs vs gold (answerable questions) |
| faithfulness | share of answer claims judged supported by the verifier (LLM judge, lexical without a key) |
| citation_accuracy | share of cited documents that are gold documents (gold-based, so ablations are comparable) |
| abstention_accuracy | abstained ⇔ question is unanswerable/out-of-scope |
| language_match | answer language matches the question (Hinglish → simple English allowed) |
| intent_accuracy | detected intent equals the labelled intent |
| latency p50/p95, first-token p50/p95, cost/query | measured per question |
| threshold_sweep | abstention accuracy for candidate `RELEVANCE_THRESHOLD` values (in the JSON report) |

## Results

<!-- eval:main:start -->
_Last run: 2026-11-16 18:17 · label `full-llm` · config `{"subset": "full", "ablation": null, "toggles": {"rerank": true, "hybrid": true, "geo_filter": true, "freshness": true, "question_index": true}, "use_rewrite": true, "use_verification": true, "llm": true, "embedding": "hash", "reranker": "lexical"}` · git `3ba6cbc`_

| Configuration | recall@5 | recall@10 | mrr | ndcg@10 | faithfulness | citation_accuracy | abstention_accuracy | language_match | latency_p95_ms |
|---|---|---|---|---|---|---|---|---|---|
| full-llm | 0.873 | 0.887 | 0.879 | 0.862 | 0.781 | 0.577 | 0.895 | 0.922 | 60067.7 |
<!-- eval:main:end -->

### Ablations (full set, 153 questions)

<!-- eval:ablation:start -->
_Last run: 2026-11-15 18:10 · label `full` · config `{"subset": "full", "llm": false, "embedding": "hash", "reranker": "lexical"}` · git `485e0b7`_

| Configuration | recall@5 | recall@10 | mrr | ndcg@10 | faithfulness | citation_accuracy | abstention_accuracy | language_match | latency_p95_ms |
|---|---|---|---|---|---|---|---|---|---|
| full system | 0.889 | 0.911 | 0.875 | 0.858 | 1.000 | 0.324 | 0.889 | 0.974 | 143.0 |
| no-rerank | 0.895 | 0.919 | 0.899 | 0.878 | 1.000 | 0.309 | 0.889 | 0.837 | 111.5 |
| no-hybrid | 0.847 | 0.899 | 0.853 | 0.835 | 1.000 | 0.316 | 0.889 | 0.974 | 104.5 |
| no-geo-filter | 0.878 | 0.899 | 0.864 | 0.842 | 1.000 | 0.323 | 0.889 | 0.974 | 98.8 |
| no-freshness | 0.892 | 0.912 | 0.887 | 0.865 | 1.000 | 0.325 | 0.895 | 0.967 | 122.6 |
| no-query-rewrite | 0.889 | 0.911 | 0.875 | 0.858 | 1.000 | 0.324 | 0.889 | 0.974 | 121.9 |
| no-question-index | 0.889 | 0.911 | 0.875 | 0.858 | 1.000 | 0.324 | 0.889 | 0.974 | 111.8 |
| no-verification | 0.889 | 0.911 | 0.875 | 0.858 | 1.000 | 0.324 | 0.889 | 0.974 | 120.9 |
<!-- eval:ablation:end -->

### With the LLM vs without (full set, 153 questions)

LLM run: Groq `gpt-oss-120b` (answers), `gpt-oss-20b` (understanding/enrichment), `qwen3.8-27b`
(routing/verification), Gemini 3.5 Flash fallback, `REGENERATE_UNSUPPORTED=false`, free tiers.

| Metric | No LLM (extractive) | With LLM |
|---|---|---|
| Recall@5 / @10 | 0.886 / 0.911 | 0.873 / 0.887 |
| MRR / nDCG@10 | 0.912 / 0.887 | 0.879 / 0.862 |
| faithfulness (verifier) | 1.000 (verbatim, vacuous) | **0.781** (real LLM judge) |
| citation_accuracy (gold-based) | 0.332 | **0.577** |
| abstention_accuracy | 0.909 | 0.895 |
| unanswerable abstain rate | 0.545 | 0.500 |
| language_match | 0.876 | **0.922** |
| intent_accuracy | 0.726 | **0.791** |
| first-token p50 / p95 | 0.08 s / 0.12 s | 6.2 s / 35.9 s (see below) |
| cost per query | $0 | $0.00035 |

What this shows:
- **The LLM improves the answer layer**: selective citations (+0.25 citation accuracy), better
  language matching and intent detection. Faithfulness 0.78 is a real score: the verifier still
  finds roughly one unsupported claim in five, which is then hedged or removed.
- **Retrieval is slightly lower with LLM rewrites** (MRR −0.033). Retrieval was tuned on the
  heuristic keyword rewrite, and LLM rewrites add paraphrases that pull in near-miss chunks. A good next
  ablation is to keep only the original query and the English rewrite for dense search.
- **Abstention is the weakest area.** Half the unanswerable questions ("diversion at Rohini Sector 3")
  still get an answer. The LLM usually says that nothing covers that place, but it doesn't use the
  phrasing the metric counts as abstaining, and some city-wide advisories without geometry clear the
  gate. A few Hindi questions abstain wrongly when the English rewrite fails. The threshold sweep for
  this configuration peaks at **0.20** (0.937 vs 0.909 at 0.08), because max-over-rewrites raises
  scores, so `.env` sets `RELEVANCE_THRESHOLD=0.20` when an LLM key is present.
- **Latency here is a stress figure, not a user figure.** The runner fires 153 questions
  back-to-back, which saturates the free-tier 8k tokens/min quotas. Paced like a real user (one
  question per ~20 s), the live app measured first token p50 ≈ 2 s (1.6–4.2 s) and full answers in
  3–6 s; see [deployment.md](deployment.md#5-running-on-free-llm-tiers-groq--gemini).

### Reading the numbers (zero-key configuration)

These runs use the **zero-key configuration** that CI uses: hashing embeddings, the lexical
reranker, and no LLM (heuristic query understanding and enrichment, extractive answers, lexical
verification). That makes several rows uninformative, and the caveats are listed here:

- **Hybrid retrieval** (full-text + trigram lists) is the clearest win: Recall@5 0.847 → 0.889.
- **Geo filter**: Recall@10 0.899 → 0.911.
- **Freshness**: computing Δt against the *asked window* (no decay while validity overlaps it)
  raised MRR from 0.861 to 0.875 and abstention accuracy from 0.876 to 0.889. With freshness
  fully off, MRR is slightly higher still on this corpus, because most fixture questions name an
  explicit date. Freshness matters most for undated live questions, which the fixture under-represents.
- **Lexical reranker** is slightly worse than RRF alone (MRR 0.875 vs 0.899). It's a dependency-free
  fallback; re-run with `RERANKER_PROVIDER=local` (bge-reranker-v2-m3) or `cohere` to measure
  the real cross-encoder.
- **Query rewrite, question index and verification** show no effect here. Without an LLM, rewrites
  are only a keyword variant, hypothetical questions are templates, and extractive answers are
  verbatim (faithfulness is trivially 1.0). Measure them with `GROQ_API_KEY` set.
- **citation_accuracy ≈ 0.32**: extractive answers cite all top-5 evidence items, including
  non-gold neighbours. LLM answers cite selectively.
- **Relevance threshold**: the sweep peaks at **0.08** (abstention accuracy 0.901 vs 0.873 at
  0.15) for this configuration, so `RELEVANCE_THRESHOLD` defaults to 0.08. Re-calibrate after
  changing embedder or reranker, because their score scales differ.

### CI gate

`.github/workflows/ci.yml` runs the 30-question smoke subset with `--gate` and fails the build if
faithfulness or Recall@10 drops more than `EVAL_REGRESSION_MARGIN` (0.05) below
`data/seed/eval_baseline.json`. Update the baseline deliberately with `--update-baseline`.

### Feedback loop

Thumbs-down answers enter the review queue. Admins promote them to draft eval questions
(`POST /api/admin/review-queue/{id}/promote`), approve them on the admin page and export with
`copilot eval export`.

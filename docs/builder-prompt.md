# Builder Prompt — Delhi Traffic Copilot (End-to-End Advanced RAG Web App)

> Paste everything below into your AI coding agent (Claude Code, Cursor, etc.). It is written so the agent can build the whole system phase by phase, from data ingestion to a deployed website, without needing further clarification.

---

## 0. Your role and working rules

You are a senior full-stack + ML engineer. Build a **production-quality, deployable** web application called **Delhi Traffic Copilot**: a citizen-facing assistant that answers natural-language questions about Delhi traffic (diversions, closures, incidents, congestion causes, best time to travel) using an **advanced Retrieval-Augmented Generation (RAG)** pipeline over continuously ingested official advisories, news, live traffic data and weather.

Working rules:
1. Build in the **phases listed in Section 14**, in order. At the end of each phase, run the tests, make sure `docker compose up` works, and print a short summary of what was built and what is next.
2. Never leave TODO stubs in core paths. If something needs an API key the user has not provided, implement it fully, make it config-driven, and degrade gracefully (feature disabled with a clear log message and UI notice) when the key is missing.
3. Everything configurable via environment variables (`.env.example` must list every one with comments).
4. Type everything (Python type hints + Pydantic; TypeScript strict mode).
5. Write clean, modular code with docstrings. Every module has unit tests; key flows have integration tests.
6. Respect source websites: obey robots.txt, set a descriptive User-Agent, rate-limit (≥2 s between requests per domain), cache raw downloads, always store and display the source URL.
7. Prefer simple, explainable designs. Each advanced RAG technique must be **visible and explainable** in the "RAG Internals" panel (Section 9.4).
8. **Commit small and often to GitHub**, following Section 16 exactly. Never batch a whole phase into one commit, and never commit secrets.

---

## 1. Product summary

Users can ask, in English or Hindi (or Hinglish):
- "Any diversions near ITO today?"
- "Why is Ring Road slow right now?"
- "Kal Red Fort ke paas traffic kaisa rahega?"
- "Which roads are closed for the Republic Day rehearsal?"
- "Is 6 pm Friday a bad time to drive to IGI Airport?"
- "What happened on Outer Ring Road last Tuesday evening?"

The app responds with:
- A streamed, grounded answer with **inline numbered citations** linking to the original sources.
- A **map** highlighting affected locations/roads and live congestion.
- **Current conditions cards** (live speed vs free-flow, weather, active incidents) when relevant.
- An explicit **"I don't have enough reliable information"** response when evidence is weak.
- An optional **RAG Internals** drawer showing how the answer was produced.

Additional features: live advisories feed, saved routes with alerts, feedback buttons, admin dashboard (ingestion health, eval scores, traces).

---

## 2. Tech stack (use exactly this unless a component is unavailable)

**Backend**
- Python 3.11+, **FastAPI** (async), Uvicorn, Pydantic v2, SQLAlchemy 2.0 (async) + Alembic migrations.
- **PostgreSQL 16** with extensions: `pgvector`, `postgis`, `pg_trgm`, `unaccent`. Full-text search via `tsvector` (use `simple` config so Hindi tokens are kept, plus `english` config column for English).
- **Redis**: semantic cache, rate limiting, job locks, pub/sub for live updates.
- **Worker**: separate container running **APScheduler** (or Celery beat + worker) for ingestion jobs.
- LLM access via **LiteLLM** so the provider is switchable (Anthropic Claude / OpenAI / Gemini / local Ollama) by env var. Use a stronger model for answer generation and a cheaper/faster model for extraction, query rewriting and verification.
- Embeddings: provider abstraction with two implementations:
  - `local`: `BAAI/bge-m3` via `sentence-transformers` (multilingual, 1024-dim).
  - `api`: a hosted multilingual embedding API (configurable).
- Reranker: provider abstraction:
  - `local`: `BAAI/bge-reranker-v2-m3` cross-encoder.
  - `api`: Cohere Rerank (multilingual) if key provided.
- PDF/OCR: `pymupdf` for text PDFs; `pytesseract` with `hin+eng` language packs for scanned PDFs; optional LLM-vision fallback for bad Hindi encodings.
- HTTP: `httpx` (async) with retries (`tenacity`).
- Feeds: `feedparser`. HTML extraction: `trafilatura`.
- Evaluation: `ragas` (or a custom equivalent) + custom metrics.
- Observability: **Langfuse** (self-hosted or cloud) for LLM traces; structured JSON logging; Prometheus metrics endpoint.

**Frontend**
- **Next.js 14+ (App Router)**, TypeScript, Tailwind CSS, shadcn/ui.
- Map: **MapLibre GL** (or Leaflet) with OpenStreetMap tiles; optional TomTom traffic flow tile overlay.
- Streaming chat via Server-Sent Events.
- i18n: English + Hindi UI strings.

**Infra**
- Docker + `docker-compose.yml` for local (services: `db`, `redis`, `api`, `worker`, `web`, optional `langfuse`).
- Deployment targets documented: API + worker on Railway/Render/Fly.io, DB on Neon/Supabase (must support pgvector + PostGIS) or a managed Postgres VM, web on Vercel.
- GitHub Actions CI: lint (ruff, mypy, eslint), tests, eval regression gate, Docker build.

---

## 3. Repository structure

```
delhi-traffic-copilot/
├── README.md                     # setup, architecture diagram, screenshots, deployment
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py               # FastAPI app factory
│   │   ├── config.py             # pydantic-settings
│   │   ├── db/                   # models, session, migrations helpers
│   │   ├── ingestion/
│   │   │   ├── sources/          # dtp_advisories.py, google_news.py, gdelt.py, tomtom.py, weather.py, osm_gazetteer.py, events.py
│   │   │   ├── parsing/          # pdf.py, ocr.py, html.py, language.py
│   │   │   ├── chunking.py
│   │   │   ├── enrichment.py     # LLM metadata extraction
│   │   │   ├── geocoding.py
│   │   │   ├── dedup.py
│   │   │   ├── embedding_job.py
│   │   │   └── pipeline.py       # orchestrates a document end to end
│   │   ├── rag/
│   │   │   ├── query_understanding.py
│   │   │   ├── router.py         # agentic tool routing
│   │   │   ├── retrieval/        # dense.py, sparse.py, fusion.py, filters.py, rerank.py, freshness.py
│   │   │   ├── tools/            # live_traffic.py, weather.py, incident_sql.py, advisory_search.py, route_check.py
│   │   │   ├── generation.py
│   │   │   ├── verification.py   # claim-level grounding check
│   │   │   ├── cache.py          # semantic cache
│   │   │   └── orchestrator.py   # full answer flow
│   │   ├── providers/            # llm.py, embeddings.py, reranker.py
│   │   ├── api/                  # routers: chat, advisories, map, routes, alerts, feedback, admin, health
│   │   ├── alerts/               # route matching + notification (email / web push)
│   │   ├── eval/                 # dataset, runner, metrics, report
│   │   ├── observability/        # logging, langfuse, metrics
│   │   └── worker.py             # scheduler entrypoint
│   └── tests/
├── frontend/
│   ├── app/                      # pages: / (chat+map), /advisories, /routes, /admin, /about
│   ├── components/
│   ├── lib/
│   └── messages/                 # en.json, hi.json
├── data/
│   ├── seed/                     # gazetteer seed, eval questions
│   └── raw/                      # cached downloads (gitignored)
└── docs/
    ├── architecture.md
    ├── rag-design.md             # explains every technique and why
    └── evaluation.md
```

---

## 4. Data sources and ingestion

Create a `Source` registry table so each source can be enabled/disabled, has a schedule, and records last run status.

### 4.1 Delhi Traffic Police advisories (primary, highest trust)
- Crawl the official site `https://traffic.delhipolice.gov.in` advisory/diversion listing pages; discover PDF links (pattern seen: `/sites/default/files/Traffic Diversions/Advisory/*.pdf`) and any HTML advisory pages.
- **Backfill job** (run once): download all historical advisories available.
- **Incremental job**: every 30 min, check listing pages for new items (compare by URL + content hash).
- Parse PDFs with pymupdf; if extracted text is empty/garbled (heuristic: low ratio of valid Devanagari/Latin characters), run OCR (`hin+eng`); if still poor and an LLM vision model is configured, use it to transcribe.
- Detect language per document (`en`, `hi`, mixed). Link Hindi and English versions of the same advisory when titles/dates match.
- `trust_level = official`.

### 4.2 News via RSS (secondary, timeliness)
- **Google News RSS search** feeds, configurable list of queries, e.g.:
  - `delhi traffic advisory`, `delhi traffic diversion`, `delhi road closed`, `delhi waterlogging`, `delhi traffic jam`, `delhi accident road`, `दिल्ली ट्रैफिक`, `दिल्ली जाम`
  - URL format: `https://news.google.com/rss/search?q=<query>&hl=en-IN&gl=IN&ceid=IN:en` (and `hl=hi&ceid=IN:hi` for Hindi).
- Configurable direct RSS feeds of Delhi city sections from major outlets (list in config).
- Every 30 min. Store title, summary, publisher, published time, link. Fetch full text with `trafilatura` only where robots.txt allows; otherwise use title + summary.
- **GDELT backfill** (optional job): last 6–12 months of Delhi traffic-related articles.
- `trust_level = news`.

### 4.3 Live traffic — TomTom Traffic API (numeric + incidents)
- Requires `TOMTOM_API_KEY`. Free tier is ~2,500 non-tile requests/day — implement a **request budget manager** in Redis that tracks daily usage and adapts polling frequency (e.g., every 15 min daytime, every 60 min 23:00–06:00) so it never exceeds `TOMTOM_DAILY_BUDGET` (default 2,300).
- **Flow**: poll a configurable list of ~25–40 key segments (Ring Road, Outer Ring Road, NH-48, DND, ITO, AIIMS, Dhaula Kuan, Ashram, Mehrauli-Badarpur Rd, Vikas Marg, Rohtak Rd, GT Karnal Rd, Barapullah, etc.) defined as seed points in `data/seed/segments.yaml`. Store `current_speed`, `free_flow_speed`, `current_travel_time`, `confidence`, `road_closure` in a time-series table `traffic_flow_obs`.
- **Incidents**: poll the Incidents API for the Delhi NCR bounding box. Store structured incidents in `traffic_incidents` (type, delay, from/to, geometry, start/end). Also **render each incident into a one-sentence text document** (e.g., "Accident on Ring Road near Moolchand towards Ashram, 2 lanes blocked, delay ~12 min, since 18:20") and push it through the normal chunk pipeline with `valid_to` = incident end (auto-expire).
- Frontend may use TomTom traffic flow **tiles** (separate 50k/day tile quota) as a map overlay if the key is present.
- `trust_level = sensor`.

### 4.4 Weather — Open-Meteo (no key)
- Hourly job: current + forecast (next 48 h) and recent history for 6–8 Delhi zone centroids: precipitation, rain, visibility, temperature, weather code. Table `weather_obs`.
- Derive flags: `heavy_rain`, `fog_low_visibility`, `extreme_heat`.

### 4.5 Static knowledge — gazetteer (load once, refresh monthly)
- From **OpenStreetMap Overpass API**: Delhi junctions/flyovers, major roads (with geometries), metro stations, stadiums, event venues (Bharat Mandapam, Ramlila Maidan, Red Fort, JLN Stadium, Arun Jaitley Stadium, IGI Stadium), hospitals, markets.
- Table `places` with `name`, `aliases` (English, Hindi, common abbreviations: "AIIMS", "ITO", "CP"/"Connaught Place", "DND", "NH-48"), `type`, `geom` (PostGIS), `trigram index` on names.
- Seed a hand-curated alias file `data/seed/aliases.yaml` for Delhi-specific names and Hindi spellings.

### 4.6 Events (optional)
- Ingest event mentions extracted from advisories/news (Section 5) into an `events` table (event name, venue, start/end). Optionally a configurable static calendar (festivals, Republic Day rehearsals, cricket fixtures) in `data/seed/events.yaml`.

---

## 5. Processing pipeline (per document)

Implement `pipeline.process(document)` with these stages; each stage is idempotent and logged:

1. **Fetch & cache raw** → `raw_documents` (url, source_id, fetched_at, content_hash, raw_path, http_status).
2. **Parse & clean** → normalized text, language detection, removal of boilerplate (headers/footers, "follow us on…" lines).
3. **Structure-aware chunking** (`chunking.py`):
   - Short advisory/news (< ~800 tokens): **one chunk**; never split a diversion list from its heading.
   - Long advisories (e.g., Republic Day, G-20): split by detected sections ("Restrictions", "Diversions", "Parking", "Metro", "Alternate routes", "Do's and Don'ts") using heading heuristics + numbered-list detection; target 300–700 tokens, 10–15% overlap only within a section.
   - **Contextual chunk headers**: prepend to every chunk a header with document title, issuing authority, date, and validity window so each chunk is self-contained (store both `text` and `text_with_context`; embed `text_with_context`).
   - TomTom incidents: one chunk each.
4. **LLM metadata enrichment** (`enrichment.py`, cheap model, JSON-schema constrained output, retries on invalid JSON):
   - `event_type` ∈ {vip_movement, protest, festival, procession, sports_event, roadwork, construction, waterlogging, accident, breakdown, closure, general_advisory, other}
   - `locations`: list of place mentions (raw text)
   - `roads`: list of road names
   - `valid_from`, `valid_to` (resolve relative dates against `published_at`, IST timezone)
   - `severity` ∈ {low, medium, high}
   - `diversion_points`, `restricted_roads`, `alternate_routes` (lists)
   - `summary_en` (1–2 sentences, also for Hindi docs), `summary_hi` (optional)
   - `hypothetical_questions`: 3–5 questions this chunk answers (used for an additional HyDE-style "question index")
5. **Geocoding** (`geocoding.py`): resolve each location mention against `places` using alias lookup → trigram fuzzy match → (optional) Nominatim fallback with caching, constrained to Delhi NCR bbox. Store `chunk_locations` (chunk_id, place_id, geom, confidence). Compute a chunk-level `geom_union` (MultiPoint/LineString buffer).
6. **Deduplication** (`dedup.py`): near-duplicate detection across sources within a ±48 h window using MinHash/SimHash on text + cosine similarity on embeddings (threshold configurable, default 0.92). Group into `dedup_cluster_id`; choose a **canonical** member (prefer official > news; then longest/most complete). Retrieval returns canonical chunks but citations can list "also reported by N sources".
7. **Embedding** (`embedding_job.py`): batch-embed `text_with_context` and each hypothetical question. Store vectors in `chunk_embeddings` (HNSW index, cosine) and `question_embeddings`.
8. **Index for sparse search**: `tsvector` columns (english + simple) with GIN index; trigram index on text.
9. **Mark ready** and publish a Redis event `new_chunk` (used by alerts + live feed).

### 5.1 Database schema (core tables)
`sources`, `raw_documents`, `documents` (title, authority, url, language, published_at, trust_level, linked_document_id), `chunks` (document_id, ordinal, text, text_with_context, event_type, severity, valid_from, valid_to, dedup_cluster_id, is_canonical, metadata JSONB, tsv_en, tsv_simple), `chunk_embeddings` (chunk_id, embedding vector(1024)), `question_embeddings`, `places`, `chunk_locations`, `traffic_flow_obs`, `traffic_incidents`, `weather_obs`, `events`, `segments`, `users`, `saved_routes`, `alerts_sent`, `conversations`, `messages`, `feedback`, `eval_runs`, `eval_results`, `ingestion_runs`, `semantic_cache` (optional; or Redis only).

Indexes: HNSW on vectors, GIN on tsv columns and metadata, GiST on geometries, BRIN on time-series timestamps, btree on `valid_from/valid_to`, `published_at`.

---

## 6. Query-time RAG pipeline (`orchestrator.py`)

For each user message:

### 6.1 Semantic cache check
- Embed the normalized query; look up Redis semantic cache (cosine ≥ 0.95, same resolved location & time bucket). TTL depends on intent: live-conditions queries 5 min, today's advisories 30 min, historical 24 h. Return cached answer (marked "cached" in internals).

### 6.2 Query understanding (`query_understanding.py`, cheap LLM, structured output)
Produce:
- `language` (en/hi/hinglish) — answer in the user's language.
- `intent` ∈ {live_conditions, advisory_lookup, cause_explanation, plan_trip, historical_lookup, general_info, out_of_scope}
- `locations` (mentions) → geocoded to places + search radius (default 1.5 km for a junction, road geometry buffer for a road).
- `time_window`: resolve "now", "today", "tonight", "tomorrow", "last Tuesday", "this weekend", "6 pm Friday" in **Asia/Kolkata** against current time.
- `rewritten_queries`: 2–3 rewrites (one English, one Hindi if relevant, one keyword-style).
- `sub_questions` for compound queries (query decomposition).
- Use conversation history to resolve follow-ups ("and what about tomorrow?", "is there any alternative?").
- `out_of_scope` → polite refusal + what the app can do.

### 6.3 Agentic routing (`router.py`)
Use LLM tool-calling (max 4 tool calls per turn, with timeouts) over these tools:
- `search_advisories(query, locations, time_window, event_types?)` → hybrid retrieval (6.4)
- `get_live_traffic(locations|segments)` → latest flow obs: current vs free-flow speed, congestion ratio, trend over last 60 min
- `get_active_incidents(locations, radius)` → from `traffic_incidents`
- `get_weather(location, time_window)`
- `query_incident_history(location, time_window, event_type?)` → safe, parameterized SQL templates (no free-form SQL) for questions like "how many closures on Ring Road last month"
- `check_route(origin, destination, depart_time?)` → finds advisories/incidents/weather/congestion intersecting a buffered corridor between two geocoded places (straight-line or OSRM route if configured)
- `typical_conditions(segment, weekday, hour)` → historical average from `traffic_flow_obs` (for "is 6 pm Friday bad?")

Routing rules (also enforced as defaults if the LLM omits them):
- `live_conditions` / `cause_explanation` → live traffic + incidents + advisories + weather.
- `advisory_lookup` → advisories.
- `plan_trip` → check_route + typical_conditions + advisories for that time.
- `historical_lookup` → advisories + incident history with the historical time window.

### 6.4 Hybrid retrieval (`retrieval/`)
1. **Hard filters**: time-window overlap (`valid_from ≤ window_end AND (valid_to IS NULL OR valid_to ≥ window_start)`, falling back to `published_at` within window ± 1 day when validity is unknown); optional spatial filter `ST_DWithin(chunk geom, query geom, radius)` — if spatial filter yields < k results, relax radius ×2 once, then drop it and rely on scoring.
2. **Dense retrieval** on chunk embeddings for each rewritten query (top 30 each) + **question-index retrieval** on hypothetical questions (top 20).
3. **Sparse retrieval**: Postgres full-text (`ts_rank_cd`) on both tsv columns + trigram similarity for place/road names (top 30).
4. **Reciprocal Rank Fusion** (k=60) across all lists; collapse by `dedup_cluster_id` to canonical chunks.
5. **Cross-encoder rerank** top 40 → top 8.
6. **Final scoring** = rerank score × freshness decay × trust weight × spatial proximity boost:
   - freshness: `exp(-Δt / τ)`, τ depends on intent (live: 6 h; advisory: 3 days; historical: disabled).
   - trust: official 1.0, sensor 0.95, news 0.85.
   - proximity: `1 + α·(1 - d/radius)` within radius.
7. Return chunks with all scores at each stage (for the internals panel).
8. **Relevance gate**: if best final score < threshold (configurable, calibrate on eval set) and no tool returned useful data → abstain path.

### 6.5 Generation (`generation.py`)
- Strong LLM, streaming. System prompt requirements:
  - Answer **only** from the provided evidence (numbered `[1]..[n]`) and tool results; cite every factual sentence with `[n]`.
  - Lead with the direct answer, then specifics (roads, diversion points, timings), then alternatives/tips.
  - For cause explanations: list likely causes ranked with supporting evidence and state uncertainty ("likely", "reported") — never invent causes.
  - State timestamps of live data ("as of 18:40 IST").
  - Respond in the user's language (Hindi in Devanagari if user wrote Hindi; Hinglish → simple English with Hindi place names OK).
  - If evidence is insufficient: say so clearly, give what is known, point to official channels (Delhi Traffic Police website/helpline).
- Output also includes structured JSON (via a final tool call or tagged block): `map_features` (points/lines to highlight with labels), `cards` (live traffic, weather, incident), `citations` (id → url, title, source, published_at), `follow_up_suggestions` (3).

### 6.6 Verification (`verification.py`)
- After generation, a cheap model checks each sentence/claim against cited chunks (supported / not supported). Unsupported claims are removed or rewritten with hedging; if > 30% unsupported, regenerate once with stricter instructions. Store the faithfulness score with the message. Run verification on the full text after streaming; if changes are required, send a `revision` SSE event that replaces the answer in the UI (show a subtle "verified" badge when passed).

### 6.7 Memory & conversations
- Persist conversations/messages (anonymous session id via cookie; optional login). Use the last 6 turns for query understanding.

---

## 7. API (FastAPI)

- `POST /api/chat` → SSE stream events: `status` (e.g., "Searching advisories…"), `token`, `citations`, `map_features`, `cards`, `internals`, `revision`, `done`, `error`.
- `GET /api/advisories?active=true&near=lat,lon&radius=&type=&page=` → live advisory feed (canonical chunks/documents).
- `GET /api/map/live` → current segment congestion (GeoJSON), active incidents, active advisory geometries.
- `GET /api/places/search?q=` → autocomplete (aliases, Hindi).
- `POST/GET/DELETE /api/routes` → saved routes (origin, destination, typical departure time, days of week, notification channel).
- `POST /api/feedback` → thumbs up/down + optional comment + message id.
- `GET /api/admin/*` (protected by admin token): ingestion runs & source health, document counts, TomTom budget usage, eval results, recent low-score answers, cache hit rate.
- `GET /health`, `GET /ready`, `GET /metrics` (Prometheus).
- Rate limiting per IP (Redis), CORS configured for the frontend domain, input length limits, prompt-injection hygiene (treat retrieved text as data; strip instructions in documents from being followed).

---

## 8. Alerts ("notify me about my route")

- Users (optional email login via magic link, or anonymous with web-push subscription) save routes.
- On `new_chunk` events and every TomTom poll, the alert service checks if any **new active advisory/incident** intersects a saved route corridor (buffer 300 m) within its active days/time window, or if live congestion on route segments exceeds a threshold before the user's usual departure.
- Send via **Web Push** (VAPID keys) and/or email (SMTP / Resend), with dedup so the same event never alerts twice (`alerts_sent`).
- Morning digest option: at a user-chosen time, a short summary of today's advisories on their routes.

---

## 9. Frontend (Next.js)

### 9.1 Home (`/`): chat + map split view (stacked on mobile)
- Chat panel: streaming answers, citation chips `[1]` with hover preview (title, source, time, snippet) and click-through, "verified" badge, feedback buttons, follow-up suggestion chips, language toggle (EN/हिं), example questions on empty state.
- Map panel: MapLibre with OSM tiles; layers: live congestion on monitored segments (green/amber/red), active incidents (icons), active advisory areas, and answer-specific highlights from `map_features` (auto-fit bounds). Layer toggles. Optional TomTom traffic flow overlay.
- Cards above/below the answer: live speed vs free-flow, weather, incidents.

### 9.2 Advisories (`/advisories`)
- Live and upcoming advisories, filter by area/type/date, each with source link, validity window, and a mini-map. Hindi/English versions toggle when linked.

### 9.3 My Routes (`/routes`)
- Create route via place autocomplete or map click, set days/time, enable push/email, see current status of each route.

### 9.4 RAG Internals drawer (toggle on each answer) — **must be implemented**
Show, step by step:
1. Cache hit/miss
2. Query understanding output (intent, language, resolved locations on mini-map, resolved time window, rewrites, sub-questions)
3. Tools called with arguments, latency and results summary
4. Retrieval: candidates from dense / question-index / sparse lists with ranks → RRF score → rerank score → final score with freshness/trust/proximity factors; which were dropped by filters or dedup
5. Final context passed to the LLM (token count)
6. Verification results per claim
7. Total latency breakdown and token/cost estimate
8. Link to Langfuse trace (admin only)

### 9.5 Admin (`/admin`, token protected)
- Source health table (last run, status, docs ingested, errors), ingestion volume chart, TomTom budget gauge, eval scorecards over time, worst-scoring recent answers with feedback, cache hit rate, p50/p95 latency.

### 9.6 About (`/about`)
- How it works (architecture diagram), data sources with attribution and disclaimers ("Not an official service; always follow on-ground instructions and official Delhi Traffic Police channels").

UX: responsive, accessible (keyboard nav, ARIA), dark mode, skeleton loaders, graceful error states, PWA manifest + service worker (for push).

---

## 10. Evaluation (`backend/app/eval/`)

- Build `data/seed/eval_questions.jsonl` with **≥150 questions**, generated semi-automatically from ingested advisories (LLM drafts question + expected answer + gold chunk ids from a sampled document) then human-reviewed via a simple admin review page. Cover: each intent, English/Hindi/Hinglish, location-specific, time-relative ("tomorrow" relative to a fixed eval clock), compound questions, out-of-scope, and **unanswerable** questions (expected abstain).
- Eval runner uses a **frozen clock** and a DB snapshot so results are reproducible.
- Metrics:
  - Retrieval: Recall@5/10, MRR, nDCG@10 against gold chunks.
  - Generation: faithfulness, answer relevancy, context precision/recall (RAGAS-style), citation accuracy (cited chunk actually supports sentence), abstention accuracy on unanswerable set, language-match rate.
  - System: latency p50/p95, cost per query.
- **Ablation mode**: run with toggles (`--no-rerank`, `--no-hybrid`, `--no-geo-filter`, `--no-freshness`, `--no-query-rewrite`, `--no-question-index`, `--no-verification`) and produce a comparison table in `docs/evaluation.md` + admin dashboard.
- CI gate: fail the build if faithfulness or Recall@10 drops more than a configurable margin vs the stored baseline (run on a 30-question smoke subset in CI).
- User feedback (thumbs down) automatically adds the message to a "review queue" that can be promoted into the eval set.

---

## 11. Observability, reliability, security

- Langfuse tracing for every LLM/embedding/rerank call with the query id; spans for each pipeline stage.
- Structured logs (JSON) with request id; Prometheus metrics: request latency, retrieval stage latencies, cache hit rate, ingestion counts/errors, TomTom budget usage, LLM token usage.
- Retries with exponential backoff on all external calls; circuit breaker for LLM provider with fallback model.
- Ingestion jobs use Redis locks to avoid overlap; failures don't crash the worker.
- Secrets only from env; no keys in frontend except public map tile key if used (restrict by domain).
- Data retention: purge raw HTML/news full text older than N days (configurable), keep chunks/metadata; flow observations downsampled after 30 days (15-min → hourly).

---

## 12. Configuration (`.env.example` must include at least)

```
APP_ENV, APP_BASE_URL, FRONTEND_URL, ADMIN_TOKEN, TIMEZONE=Asia/Kolkata
DATABASE_URL, REDIS_URL
LLM_PROVIDER, LLM_MODEL_ANSWER, LLM_MODEL_FAST, ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, OLLAMA_BASE_URL
EMBEDDING_PROVIDER=local|api, EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_DIM=1024
RERANKER_PROVIDER=local|cohere, RERANKER_MODEL, COHERE_API_KEY
TOMTOM_API_KEY, TOMTOM_DAILY_BUDGET=2300, NEXT_PUBLIC_TOMTOM_TILES_KEY
NEWS_QUERIES (comma-separated), EXTRA_RSS_FEEDS, ENABLE_GDELT_BACKFILL
NOMINATIM_ENABLED, OVERPASS_URL
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, SMTP_*/RESEND_API_KEY
CACHE_SIM_THRESHOLD=0.95, DEDUP_SIM_THRESHOLD=0.92, RELEVANCE_THRESHOLD, RATE_LIMIT_PER_MIN
SCHEDULE_* (cron strings per job)
```

---

## 13. Scheduler jobs (`worker.py`)

| Job | Default schedule |
|---|---|
| DTP advisories incremental crawl | every 30 min |
| DTP advisories backfill | once (CLI command) |
| Google News RSS | every 30 min |
| Extra RSS feeds | every 30 min |
| GDELT backfill | once (CLI, optional) |
| TomTom flow poll | adaptive (budget manager), ~15 min day / 60 min night |
| TomTom incidents poll | every 15 min day / 60 min night |
| Weather | hourly |
| Gazetteer refresh (OSM) | monthly + CLI |
| Expire chunks past `valid_to` (mark inactive, keep for history) | every 15 min |
| Alert matcher & morning digests | event-driven + every 5 min |
| Flow downsampling / retention | daily 03:00 IST |
| Nightly eval smoke run | daily 04:00 IST |

Provide CLI commands (Typer): `ingest backfill-dtp`, `ingest backfill-news --days 180`, `ingest gazetteer`, `ingest run-once <source>`, `eval run [--ablation ...]`, `eval generate-questions`, `db seed`.

---

## 14. Build phases (do them in order; each ends with green tests, a working `docker compose up`, and the phase merged to `main` as described in Section 16)

1. **Foundation**: repo skeleton, Docker Compose (Postgres+pgvector+PostGIS, Redis), config, DB models + Alembic migrations, provider abstractions (LLM, embeddings, reranker) with tests, health endpoints, CI.
2. **Gazetteer & geocoding**: OSM import, alias seeds, place search API, geocoder with tests (ITO, AIIMS, CP, Hindi names resolve correctly).
3. **Ingestion I — advisories**: DTP crawler, PDF/OCR parsing, structure-aware chunking with contextual headers, LLM enrichment, geocoding, embedding, sparse indexing, backfill CLI. Verify on ≥50 real advisories.
4. **Ingestion II — news, dedup, live data**: Google News/RSS, trafilatura, dedup clustering with canonical selection, TomTom flow + incidents with budget manager, Open-Meteo, scheduler worker, chunk expiry, ingestion_runs logging.
5. **Retrieval core**: query understanding, hard filters, dense + question index + sparse, RRF, rerank, final scoring, relevance gate. Unit tests + a retrieval-only eval on a first 50-question set.
6. **Agent + generation**: tools, router, streaming generation with citations and structured outputs, verification pass, semantic cache, conversation memory, `/api/chat` SSE.
7. **Frontend core**: chat + map home, citations, cards, follow-ups, language toggle, advisories page, live map layers.
8. **RAG Internals + Admin**: internals drawer fully populated; admin dashboard; Langfuse integration; Prometheus metrics.
9. **Alerts**: saved routes, corridor matching, web push + email, morning digest, routes page.
10. **Evaluation**: question generation + review page, full eval runner, ablations, CI gate, `docs/evaluation.md` with results tables.
11. **Hardening & deployment**: rate limiting, error handling, security review, retention jobs, PWA, accessibility pass, production Dockerfiles, deployment guides for Railway/Render/Fly + Vercel + Neon/Supabase, seed script so a fresh deployment is usable within minutes (backfill + gazetteer).
12. **Docs & polish**: README with architecture diagram (Mermaid), screenshots/GIF, `docs/rag-design.md` explaining each technique, the reason for it and its measured effect (from ablations).

---

## 15. Acceptance criteria (the project is "done" only when all pass)

- [ ] `docker compose up` on a clean machine + `make seed` gives a working app at `localhost:3000` with real ingested data.
- [ ] Ingestion runs on schedule; admin page shows healthy sources; new DTP advisories appear in the feed within 30–60 min of publication on the source.
- [ ] Asking "Any diversions near ITO today?" returns a cited answer with map highlights when an active advisory exists, or a clear "no active advisories found" with live conditions otherwise.
- [ ] Asking "Why is Ring Road slow right now?" combines live flow data, incidents, advisories and weather with timestamps and hedged, cited causes.
- [ ] Hindi and Hinglish questions are understood and answered in the right language.
- [ ] Unanswerable/out-of-scope questions trigger a correct abstention.
- [ ] RAG Internals drawer shows every stage listed in 9.4 with real values.
- [ ] Saved route alert fires (web push or email) when a matching new advisory/incident appears (testable with a simulated event command).
- [ ] Eval report exists with metrics and ablation table; CI gate is active.
- [ ] p95 end-to-end latency to first token < 4 s and full answer < 12 s on the deployed setup (document actual numbers).
- [ ] TomTom usage never exceeds the configured daily budget.
- [ ] Deployed instance reachable over HTTPS with documented steps to reproduce.

---

## 16. Git and GitHub workflow (micro commits)

### 16.1 One-time setup (do this before any code)
1. The GitHub repo **already exists**: `https://github.com/Vansh-456/Traffic_RAG`. Do **not** create a new repo. Clone it (`git clone https://github.com/Vansh-456/Traffic_RAG.git && cd Traffic_RAG`), or if working in an existing folder, `git remote add origin https://github.com/Vansh-456/Traffic_RAG.git` and `git pull origin main`. If `gh`/git is not authenticated, stop and ask the user to run `gh auth login`.
2. Keep any files already in the repo (README, LICENSE, `.gitignore`, this prompt file at `docs/builder-prompt.md`); extend them rather than overwrite. Make sure the default branch is `main`.
3. Add or extend a thorough `.gitignore` (Python, Node, `.env*` except `.env.example`, `data/raw/`, model caches, `__pycache__`, `.next/`, `node_modules/`, `*.log`, eval artifacts), plus a README stub, `.env.example` and LICENSE (MIT unless one exists). Commit as `chore: initialize project scaffolding` and push.
4. Install **pre-commit** hooks: `ruff` (lint + format), `mypy` (backend), `eslint` + `prettier` (frontend), `detect-secrets` or `gitleaks` (blocks keys), trailing whitespace / end-of-file fixers, a size check (block files > 5 MB). Commit: `chore: add pre-commit hooks`.
5. Add `.github/pull_request_template.md` and a `CHANGELOG.md` (Keep a Changelog format).

### 16.2 Branching
- One branch per phase: `phase-01-foundation`, `phase-02-gazetteer`, … `phase-12-docs`.
- Optional sub-branches for large features inside a phase (`feat/hybrid-retrieval`), merged into the phase branch.
- Never commit directly to `main` after the initial setup commits.

### 16.3 What a micro commit is
- **One logical change per commit**: a model + its migration, a single source connector, one retrieval stage, one UI component, one test file for a module, one config change. Aim for roughly **20–200 changed lines**; split anything larger.
- Each commit must **leave the project in a working state**: pre-commit hooks pass, and the tests for the touched module pass. Run `pytest <touched tests>` / `npm test -- <touched>` before committing.
- Commit code and its tests together (or the test immediately after, in the next commit).
- Never commit generated data, downloaded PDFs, model weights, `.env`, API keys or large fixtures (keep test fixtures small; put larger ones behind a download script).

### 16.4 Commit message format (Conventional Commits)
```
<type>(<scope>): <imperative summary, ≤ 72 chars>

<optional body: what and why, wrapped at 72 chars>
```
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `build`, `ci`, `style`.
- Scopes: `db`, `config`, `providers`, `ingestion`, `dtp`, `news`, `tomtom`, `weather`, `gazetteer`, `chunking`, `enrichment`, `geocoding`, `dedup`, `embeddings`, `retrieval`, `rerank`, `router`, `tools`, `generation`, `verification`, `cache`, `api`, `alerts`, `eval`, `observability`, `web`, `map`, `chat`, `admin`, `infra`, `ci`, `docs`.
- Examples:
  - `feat(db): add chunks and chunk_embeddings tables with HNSW index`
  - `feat(dtp): crawl advisory listing pages and discover PDF links`
  - `feat(chunking): split long advisories by section headings`
  - `test(chunking): cover Republic Day advisory section splitting`
  - `feat(retrieval): add reciprocal rank fusion across dense and sparse lists`
  - `feat(tomtom): add Redis daily request budget manager`
  - `fix(geocoding): resolve Hindi spelling of ITO`
  - `feat(web): stream chat tokens over SSE`
  - `docs(rag-design): explain freshness decay and trust weighting`

### 16.5 Push and merge rhythm
- **Push after every 2–5 commits**, and always at the end of a working session, so GitHub always has recent progress.
- At the end of each phase:
  1. Run the full test suite, linters and `docker compose up` smoke check.
  2. Update `CHANGELOG.md` (`docs(changelog): phase N summary`).
  3. Open a PR with `gh pr create --base main --head phase-NN-... --title "Phase N: <name>" --body-file <generated body>`. The body lists what was built, how to test it, screenshots for UI phases, and any known limitations.
  4. Wait for CI to pass (`gh pr checks --watch`). Fix failures with new micro commits (no force-push to shared branches).
  5. Merge with **squash disabled** (use `gh pr merge --merge` or `--rebase`) so the micro-commit history is preserved on `main`, then delete the branch.
  6. Tag the milestone: `git tag -a v0.N.0 -m "Phase N: <name>" && git push --tags`. Tag `v1.0.0` after Phase 11 (deployed and acceptance criteria met).
- If the user asks you not to open PRs, merge phase branches locally with `git merge --no-ff` and push `main`; keep everything else the same.

### 16.6 Expected commit granularity per phase (guide, not a quota)
| Phase | Typical micro commits |
|---|---|
| 1 Foundation | ~15–25 (compose services one by one, config, each model group + migration, each provider abstraction, health endpoints, CI) |
| 2 Gazetteer | ~8–12 |
| 3 Advisories ingestion | ~15–25 (crawler, PDF parser, OCR fallback, language detection, chunker, contextual headers, enrichment schema, enrichment call, geocoding hook, embedding job, tsvector indexing, backfill CLI, tests for each) |
| 4 News / dedup / live | ~15–25 |
| 5 Retrieval core | ~12–20 (one commit per stage + tests) |
| 6 Agent + generation | ~12–20 |
| 7 Frontend core | ~15–25 (one per component/page) |
| 8 Internals + admin | ~10–15 |
| 9 Alerts | ~8–12 |
| 10 Evaluation | ~10–15 |
| 11 Hardening + deploy | ~10–15 |
| 12 Docs | ~5–10 |

### 16.7 Safety rules
- Before every commit, run `git diff --staged` and check for secrets, debug prints and accidental large files.
- If a secret is ever committed, stop, tell the user, rotate the key, and remove it from history (`git filter-repo`) before pushing further.
- Never use `git push --force` on `main`. Never rewrite history that has already been pushed, except to remove a leaked secret, and only with the user's approval.
- At the end of each phase, print `git log --oneline main..HEAD` (before merging) so the user can see the micro-commit trail.

Start with **Section 16.1 (repository setup)**, then **Phase 1**. Before writing code, print a short architecture overview and the list of files and planned micro commits for Phase 1.

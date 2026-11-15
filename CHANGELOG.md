# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-11-15 — Phase 7: Frontend core

### Added
- Public API: live/upcoming advisory feed with filters, advisory detail, live map GeoJSON (segments, incidents, advisory areas), feedback
- Next.js 14 app (strict TS, Tailwind, shadcn-style primitives), EN/हिं UI strings, dark mode, skip link
- Chat + map split home: streamed answers, citation chips with hover previews, verified/revised badges, feedback, follow-ups, cards
- MapLibre map with OSM tiles, live congestion/incident/advisory layers, answer highlights with auto-fit, optional TomTom tiles
- Advisories page with filters, mini-maps and Hindi/English toggle; About page; standalone web Docker image

## [0.6.0] - 2026-11-15 — Phase 6: Agent + generation

### Added
- Tools: live traffic (60-min trend), active incidents, weather, typical conditions, incident-history SQL templates, corridor route check
- Agentic router: LLM tool-calling (max 4, timeouts) merged with enforced per-intent defaults
- Streaming grounded generation with numbered citations, timestamps, language matching, follow-ups
- Deterministic map features, live/weather/incident cards and citation metadata
- Claim-level verification (LLM or lexical) with hedging, strict regeneration above 30% unsupported, revision SSE event
- Redis semantic cache (cosine >= 0.95, same place + time bucket, intent TTLs); conversation memory; POST /api/chat SSE

## [0.5.0] - 2026-11-15 — Phase 5: Retrieval core

### Added
- Relative time resolution in IST (now/today/tonight/kal/last Tuesday/6 pm Friday/this weekend, Hindi + Hinglish)
- Query understanding: LLM structured output + heuristic path, follow-up resolution, rewrites, sub-questions
- Hard filters (validity overlap, published fallback, spatial radius with relax x2 then drop)
- Dense + hypothetical-question index + english/simple full-text + trigram name lists fused with RRF (k=60)
- Cross-encoder rerank, final score = rerank x freshness x trust x proximity (with location-mismatch decay)
- Per-stage traces and timings for the internals panel; verified on live DTP + news data

## [0.4.0] - 2026-11-15 — Phase 4: Ingestion II — news, dedup, live data

### Added
- Google News RSS (en/hi queries), publisher RSS with robots-aware trafilatura full text, GDELT backfill
- TomTom flow (36 seeded segments) + incidents with an atomic Redis daily budget and adaptive day/night cadence
- Incidents rendered as auto-expiring one-sentence documents through the normal pipeline
- Open-Meteo weather for 8 Delhi zones with heavy_rain / fog / extreme_heat flags
- Job runner with Redis locks, ingestion_runs and source health; APScheduler worker; chunk expiry and retention jobs
- Typer CLI: ingest backfill-dtp / backfill-news / gazetteer / run-once, db seed
- Verified on live data: 584 weather observations, 89 news articles, DTP advisories

## [0.3.0] - 2026-11-15 — Phase 3: Ingestion I — advisories

### Added
- DTP crawler: RSS (en/hi), taxonomy listings, node pages with structured news/end dates, attached PDFs; node-id backfill discovery
- Polite fetcher (robots.txt, >=2 s/domain, retries, descriptive UA) and raw download cache
- PDF text layer -> Tesseract hin+eng OCR -> Gemini vision fallback, garbled Hindi font detection
- Structure-aware chunking with section splitting, in-section overlap and contextual headers
- Schema-validated LLM enrichment (event type, places, IST validity, summaries, hypothetical questions) with heuristic fallback
- Geocoded chunk geometry, embeddings + question index, SimHash/cosine dedup with canonical selection, Hindi/English linking
- Note: the backfill CLI command ships with the shared jobs runner in Phase 4

## [0.2.0] - 2026-11-15 — Phase 2: Gazetteer & geocoding

### Added
- Curated Delhi gazetteer seed (~100 places, 22 road geometries, Hindi and Hinglish aliases)
- OSM Overpass import of junctions, flyovers, roads, metro, venues, hospitals, markets
- Geocoder: alias lookup, longest-match mention finder, pg_trgm fuzzy match, bounded/cached Nominatim fallback
- GET /api/places/search autocomplete (English, Hindi, aliases)

## [0.1.0] - 2026-11-15 — Phase 1: Foundation

### Added
- Docker Compose with Postgres 16 + PostGIS + pgvector and Redis
- Typed pydantic settings, JSON logging, Prometheus metrics, Langfuse wrapper
- SQLAlchemy 2 async models for all core tables + 6 Alembic migrations (HNSW, GIN, GiST, BRIN)
- LiteLLM provider (Groq answer/fast models, Gemini fallback, RPM limiter, circuit breaker, JSON-schema retry)
- Embedding (bge-m3 / API / hashing) and reranker (bge-reranker / Cohere / lexical) abstractions
- /health, /ready, /metrics endpoints; backend Dockerfile with hash-pinned lockfile; CI workflow

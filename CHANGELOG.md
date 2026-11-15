# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

# Deployment

Production layout: **API + worker** on Railway / Render / Fly.io (same image, two processes),
**Postgres with pgvector + PostGIS** on Neon / Supabase (or a managed VM), **Redis** (Upstash,
Railway or Render), **web** on Vercel.

## 1. Database

Both Neon and Supabase support `vector` and `postgis`; `pg_trgm` and `unaccent` are standard.

```sql
-- run once as the owner (migration 0000 also does this)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
```

Use the async driver URL: `postgresql+asyncpg://USER:PASS@HOST/DB` (Neon: append
`?ssl=require`; Supabase: use the direct connection on port 5432 for migrations).
For evaluation create a second database and set `EVAL_DATABASE_URL`.

## 2. API + worker (Docker image `backend/Dockerfile`, build context = repo root)

The image runs `alembic upgrade head` on start, then uvicorn on `$PORT`.

### Fly.io
```bash
fly launch --no-deploy --dockerfile backend/Dockerfile --name dtc-api
fly secrets set DATABASE_URL=... REDIS_URL=... ADMIN_TOKEN=... GROQ_API_KEY=... GEMINI_API_KEY=... \
  TOMTOM_API_KEY=... FRONTEND_URL=https://<your-vercel-app>.vercel.app APP_ENV=production
fly deploy
# worker as a second process group: in fly.toml
#   [processes]
#   app = "sh -c 'alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers'"
#   worker = "python -m app.worker"
fly scale count app=1 worker=1
```

### Railway / Render
Create two services from the repo using `backend/Dockerfile` (root directory `/`):
- **api**: default command; health check path `/ready`.
- **worker**: start command `python -m app.worker`, no public port.
Set the same environment variables on both (see `.env.example`). Add a Redis add-on and copy its URL.

Optional build args: `INSTALL_LOCAL_ML=1` (bge-m3 + bge-reranker; ~4.5 GB image, ≥ 4 GB RAM),
`APT_MIRROR`, `PIP_INDEX_URL` (installs stay hash-verified via `requirements.lock`).

## 3. Web on Vercel

- Import the repo, **Root Directory** `frontend`, framework Next.js.
- Env: `NEXT_PUBLIC_API_URL=https://<api-host>`, optional `NEXT_PUBLIC_TOMTOM_TILES_KEY`
  (restrict it to your domain in the TomTom dashboard) and `NEXT_PUBLIC_VAPID_PUBLIC_KEY`.
- Set `FRONTEND_URL` on the API to the Vercel URL (CORS + links in alerts).
- Cookies: the session cookie is `SameSite=Lax; Secure` in production. If web and API are on
  different registrable domains, serve the API from a subdomain of the web domain
  (e.g. `api.example.in`) so the cookie is first-party.

## 4. First data (usable within minutes)

```bash
# from a shell in the API container (fly ssh console / railway run / render shell)
python -m app.cli db seed --backfill-limit 400   # gazetteer seed, segments, events, weather, recent DTP + news
python -m app.cli ingest gazetteer               # + OSM Overpass import (a few minutes)
python -m app.cli ingest backfill-dtp            # optional: full historical crawl (≈ 50 min at 2 s/request)
```
After that the worker keeps everything fresh (DTP and news every 30 min, TomTom adaptive, weather hourly).

## 5. Keys & features

| Variable | Feature | Without it |
|---|---|---|
| `GROQ_API_KEY` (+ `GEMINI_API_KEY` fallback) | LLM understanding, enrichment, generation, verification | heuristic + extractive mode, UI banner |
| `TOMTOM_API_KEY` | live flow + incidents (budget `TOMTOM_DAILY_BUDGET`) | advisories/news/weather only |
| `VAPID_*` | web push alerts | in-app + email alerts |
| `RESEND_API_KEY` or `SMTP_*` | email alerts + magic link | in-app + push alerts |
| `LANGFUSE_*` | LLM traces (link in internals for admins) | JSON logs + Prometheus only |

Generate VAPID keys: `npx web-push generate-vapid-keys`.

## 6. Verification checklist

- `GET https://<api>/ready` → `{"status":"ok"}`
- Admin page (`/admin`, ADMIN_TOKEN) shows sources with `ok` status after the first worker cycle.
- Ask "Any diversions near ITO today?"; open **How this answer was made**.
- `python -m app.cli alerts simulate --lat 28.6129 --lon 77.2295` with a saved route through
  India Gate → alert appears under **My Routes**.
- Latency: admin dashboard shows p50/p95 total and first-token latency from real traffic.

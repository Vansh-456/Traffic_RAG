# Delhi Traffic Copilot — common tasks (run from the repo root).
COMPOSE ?= docker compose
EXEC_API = $(COMPOSE) exec api

.PHONY: up down logs seed seed-full migrate test test-web lint eval eval-smoke ablations simulate-alert

up:            ## build and start db, redis, api, worker, web
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api worker

migrate:
	$(EXEC_API) alembic upgrade head

seed:          ## make a fresh deployment usable in minutes: gazetteer, segments, weather, recent advisories + news
	$(EXEC_API) python -m app.cli db seed --backfill-limit 400

seed-full:     ## full historical backfill (slow: polite 2 s/request crawl) + OSM gazetteer
	$(EXEC_API) python -m app.cli ingest gazetteer
	$(EXEC_API) python -m app.cli ingest backfill-dtp
	$(EXEC_API) python -m app.cli ingest backfill-news --days 180

test:
	cd backend && pytest

test-web:
	cd frontend && npm test && npm run lint && npm run typecheck

lint:
	cd backend && ruff check . && ruff format --check . && mypy app

eval-smoke:
	$(EXEC_API) python -m app.cli eval run --subset smoke --gate

eval:
	$(EXEC_API) python -m app.cli eval run --subset full

ablations:
	$(EXEC_API) python -m app.cli eval run --subset full --ablation all

simulate-alert: ## inject a closure near India Gate and fire matching route alerts
	$(EXEC_API) python -m app.cli alerts simulate --lat 28.6129 --lon 77.2295 --title "Simulated closure near India Gate"

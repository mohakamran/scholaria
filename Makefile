.PHONY: setup start stop restart status test migrate

setup:      ## Check prerequisites, create venv, install deps, migrate DB
	./scripts/setup.sh

start:      ## Start Ollama (if needed), API and UI
	./scripts/start.sh

stop:       ## Stop only processes started by this project
	./scripts/stop.sh

restart: stop start

status:     ## Show service health
	@curl -fsS http://127.0.0.1:8000/api/system/status | .venv/bin/python -m json.tool || echo "API not running"

test:       ## Run the automated test suite (no LLM required)
	.venv/bin/python -m pytest

migrate:    ## Apply database migrations
	cd backend && ../.venv/bin/alembic upgrade head

PY ?= .venv/bin/python

.PHONY: install dev migrate test lint format demo up down logs

install:            ## Create venv and install dependencies
	python3 -m venv .venv && $(PY) -m pip install -r requirements-dev.txt

migrate:            ## Apply database migrations
	$(PY) -m alembic upgrade head

dev: migrate        ## Run the API locally (SQLite + inline checks, no Docker needed)
	$(PY) -m uvicorn app.main:app --reload

test:               ## Run tests with coverage
	$(PY) -m pytest --cov --cov-report=term-missing

lint:               ## Lint and format-check
	$(PY) -m ruff check . && $(PY) -m ruff format --check .

format:
	$(PY) -m ruff format . && $(PY) -m ruff check . --fix

demo:               ## Seed a demo source + rules and trigger a run (API must be running)
	$(PY) scripts/seed_demo.py

up:                 ## Start the full stack in Docker
	docker compose up --build -d

down:
	docker compose down -v

logs:
	docker compose logs -f api worker beat

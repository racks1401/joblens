# Docker-first targets. On Windows without make, use the PowerShell commands in README.md.
.PHONY: up down migrate ingest evals test lint verify-companies train-scam

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	docker compose run --rm api alembic upgrade head

ingest:
	docker compose run --rm api python -m app.ingest.run

evals:
	docker compose run --rm api python -m evals.run --suite all --report README_EVALS.md

train-scam:
	docker compose run --rm api python -m ml.train_scam_classifier

verify-companies:
	python scripts/verify_companies.py

test:
	python -m pytest -q

lint:
	ruff check . && mypy app evals ml

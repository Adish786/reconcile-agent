# ============================================================
# Reconcile Agent – Makefile
# ============================================================
# This Makefile provides common development and operations tasks.
# Use `make help` to see available commands.
# ============================================================

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
POETRY := poetry
PYTEST := $(POETRY) run pytest
UVICORN := $(POETRY) run uvicorn
DOCKER_COMPOSE := docker-compose

# ----------------------------------------------------------------------
# Phony targets (targets that don't produce files)
# ----------------------------------------------------------------------
.PHONY: help install install-dev test lint format check run \
        docker-build docker-up docker-down docker-logs docker-shell \
        seed clean db-reset

# ----------------------------------------------------------------------
# Help
# ----------------------------------------------------------------------
help:
	@echo "Available commands:"
	@echo ""
	@echo "  install          Install all dependencies (including dev)."
	@echo "  install-prod     Install only runtime dependencies (for production)."
	@echo "  test             Run tests with coverage."
	@echo "  lint             Run linters (ruff, mypy)."
	@echo "  format           Auto‑format code with black and isort."
	@echo "  check            Run lint, format check, and tests (full CI)."
	@echo "  run              Run FastAPI locally with hot reload."
	@echo ""
	@echo "  docker-build     Build the Docker image."
	@echo "  docker-up        Start all services in the background."
	@echo "  docker-down      Stop and remove containers."
	@echo "  docker-logs      Tail logs from the API service."
	@echo "  docker-shell     Open a shell inside the API container."
	@echo ""
	@echo "  seed             Seed the database with synthetic data."
	@echo "  clean            Remove cache files and Python artifacts."
	@echo "  db-reset         Drop and recreate the database (dangerous!)."

# ----------------------------------------------------------------------
# Installation
# ----------------------------------------------------------------------
install:
	$(POETRY) install

install-prod:
	$(POETRY) install --no-dev

# ----------------------------------------------------------------------
# Testing & Quality
# ----------------------------------------------------------------------
test:
	$(PYTEST) src/tests/ -v --cov=src --cov-report=term --cov-report=html

lint:
	$(POETRY) run ruff check src/
	$(POETRY) run mypy src/

format:
	$(POETRY) run black src/ tests/
	$(POETRY) run isort src/ tests/

check: lint test
	@echo "All checks passed."

# ----------------------------------------------------------------------
# Local development
# ----------------------------------------------------------------------
run:
	$(UVICORN) src.main:app --host 0.0.0.0 --port 8000 --reload

# ----------------------------------------------------------------------
# Docker
# ----------------------------------------------------------------------
docker-build:
	$(DOCKER_COMPOSE) build

docker-up:
	$(DOCKER_COMPOSE) up -d

docker-down:
	$(DOCKER_COMPOSE) down

docker-logs:
	$(DOCKER_COMPOSE) logs -f api

docker-shell:
	$(DOCKER_COMPOSE) exec api bash

# ----------------------------------------------------------------------
# Data & maintenance
# ----------------------------------------------------------------------
seed:
	$(DOCKER_COMPOSE) exec api python scripts/seed_data.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov .coverage

db-reset:
	$(DOCKER_COMPOSE) exec api python scripts/reset_db.py  # if you have one
	@echo "Database reset. Use 'make seed' to populate."
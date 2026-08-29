.PHONY: help install test lint run docker-build docker-up seed

help:
	@echo "Available commands:"
	@echo "  install      Install dependencies with Poetry"
	@echo "  test         Run pytest"
	@echo "  lint         Run ruff and mypy"
	@echo "  run          Run FastAPI locally"
	@echo "  docker-build Build Docker image"
	@echo "  docker-up    Start services with Docker Compose"
	@echo "  seed         Seed the database with synthetic data"

install:
	poetry install

test:
	poetry run pytest src/tests/ -v

lint:
	poetry run ruff check src/
	poetry run mypy src/

run:
	poetry run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

seed:
	docker-compose exec api python scripts/seed_data.py
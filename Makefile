COMPOSE = docker compose -f infra/docker/docker-compose.dev.yml

.PHONY: dev migrate test seed down

dev:
	$(COMPOSE) up --build

migrate:
	uv run alembic upgrade head

test:
	uv run pytest

seed:
	uv run python scripts/dev_seed.py

down:
	$(COMPOSE) down -v

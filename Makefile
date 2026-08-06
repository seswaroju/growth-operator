COMPOSE = docker compose -f infra/docker/docker-compose.dev.yml

.PHONY: dev migrate db-roles bootstrap test seed down grant-admin revoke-admin

dev:
	$(COMPOSE) up --build

# Create/refresh the non-superuser app_rw role so RLS is enforced for the app (MVP-016).
# Idempotent; needed once per DB (initdb handles a fresh volume automatically).
db-roles:
	$(COMPOSE) exec -T postgres psql -U growth_operator -d growth_operator < infra/db/roles.sql

migrate:
	uv run alembic upgrade head

# One-shot local bring-up of the data layer: app_rw role + schema at head.
bootstrap: db-roles migrate

test:
	uv run pytest

seed:
	uv run python scripts/dev_seed.py

# Grant a user the Growth Operator operator role (cross-tenant support console).
# The user must have logged in once (OTP) first. Usage: make grant-admin EMAIL=you@example.com
# Optional auto-expiry: uv run python scripts/grant_platform_admin.py you@example.com --days 30
grant-admin:
	uv run python scripts/grant_platform_admin.py $(EMAIL)

# Revoke a user's operator role immediately. Usage: make revoke-admin EMAIL=you@example.com
revoke-admin:
	uv run python scripts/revoke_platform_admin.py $(EMAIL)

down:
	$(COMPOSE) down -v

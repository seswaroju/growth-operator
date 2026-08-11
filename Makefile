COMPOSE = docker compose -f infra/docker/docker-compose.dev.yml

.PHONY: dev migrate db-roles bootstrap test seed down grant-admin revoke-admin make-owner secret-scan glitchtip backup restore backup-drill hooks schema-doc verify-anchor

# Enable the local CI-guardrail git hooks (run once per clone). A HARD line: pre-commit blocks a
# commit on ruff/guard failure; pre-push blocks a push unless the full CI mirror (ruff + guards +
# mypy + tests/unit) passes — so main never goes red from a careless push (founder 2026-08-10).
hooks:
	git config core.hooksPath .githooks
	@echo "hooks enabled (.githooks): pre-commit = ruff+guards; pre-push = full CI mirror"

dev:
	$(COMPOSE) up --build

# Create/refresh the non-superuser app_rw role so RLS is enforced for the app (MVP-016).
# Idempotent; needed once per DB (initdb handles a fresh volume automatically).
db-roles:
	$(COMPOSE) exec -T postgres psql -U growth_operator -d growth_operator < infra/db/roles.sql

migrate:
	uv run alembic upgrade head

# Regenerate the schema doc from the live DB (needs `brew install libpq`). Migrations are the source
# of truth; this snapshot keeps docs/06-database/schema.sql (the vault) in sync — run after a migration.
schema-doc:
	./scripts/dump_schema.sh docs/06-database/schema.sql

# Verify the live audit chains against the most recent anchor (MVP-071 tamper-evidence).
# Exit 0 = intact, 1 = tamper detected, 2 = not configured. Needs GROWTH_OPERATOR_AUDIT_ANCHOR_PATH.
verify-anchor:
	uv run python scripts/verify_audit_anchor.py

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

# Local dev: make an email the owner of a new store (to demo the customer app).
# Usage: make make-owner EMAIL=you@store.com STORE="Ratna Gold"
make-owner:
	uv run python scripts/dev_make_owner.py $(EMAIL) "$(STORE)"

down:
	$(COMPOSE) down -v

# Scan the full git history + working tree for committed secrets (security-hardening S1).
# Mirrors the CI secret-scan job. --redact so a finding never prints the secret value.
# Requires a local gitleaks (`brew install gitleaks`). CI pins its own copy.
secret-scan:
	gitleaks detect --source . --config .gitleaks.toml --redact --no-banner --verbose

# Run a local self-hosted GlitchTip error-tracking dashboard (security S2) at http://localhost:8888.
# See infra/docker/GLITCHTIP.md for first-time setup + wiring the DSN. App is inert without a DSN.
glitchtip:
	docker compose -f infra/docker/docker-compose.glitchtip.yml up -d

# Backup / restore / restore-drill (security S3, audit #16e). See infra/db/BACKUP_RESTORE.md.
# backup + restore need host pg tools (or run on the DB server). backup-drill runs the drill INSIDE
# the dev Postgres container, so it works locally with just Docker — no host pg client needed.
backup:
	./scripts/db_backup.sh

restore:
	./scripts/db_restore.sh $(DUMP) $(TARGET)

backup-drill:
	$(COMPOSE) exec -T -e GROWTH_OPERATOR_DATABASE_MIGRATOR_URL=postgresql://growth_operator:growth_operator@localhost:5432/growth_operator postgres bash -s < scripts/db_restore_drill.sh

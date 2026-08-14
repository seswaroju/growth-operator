#!/bin/bash
# Production runtime-role bootstrap (PILOT-1A). Mounted into the Postgres container's
# /docker-entrypoint-initdb.d/ INSTEAD of roles.sql, which is a development artifact.
#
# roles.sql creates `app_rw` with the literal password `app_rw` — correct for a laptop, and exactly
# the credential the startup validator refuses in production. Mounting it on a real host would
# create the runtime role with a password published in this repository, and while the application
# would then refuse to boot (core/common/safety.py), the weak role would still exist on the
# database. A guard that stops the app is not a reason to create the hole.
#
# The password comes from the environment, which the deploy script fills from the SOPS secrets
# file. It is never echoed: initdb output goes to the container log.
#
# Runs ONCE, on a fresh data volume, like every initdb script. `scripts/deploy-prod.sh` verifies
# afterwards that the role exists, so a volume that already had data does not silently skip this.
set -euo pipefail

: "${APP_RW_PASSWORD:?APP_RW_PASSWORD must be set to create the runtime role}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	DO \$\$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_rw') THEN
	    -- NOBYPASSRLS is the property every tenant-isolation guarantee rests on: with BYPASSRLS,
	    -- row-level security stops applying and one store can read another's data.
	    CREATE ROLE app_rw LOGIN PASSWORD '${APP_RW_PASSWORD}'
	      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
	  END IF;
	END
	\$\$;

	-- Noisy-neighbour guard (multi-tenant-rls.md): cap statement time for the app role.
	ALTER ROLE app_rw SET statement_timeout = '5s';

	GRANT USAGE ON SCHEMA public TO app_rw;
	GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw;
	GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
	-- Future tables created by migrations inherit the same grants; without this every migration
	-- would need a manual GRANT and one forgotten line becomes a production 500.
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	  GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
SQL

echo "app_rw created (NOBYPASSRLS, statement_timeout 5s)"

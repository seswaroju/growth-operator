-- Application database role — app_rw (MVP-016, unblocks BLOCKERS #11).
--
-- The RUNTIME app/worker/scheduler connect as `app_rw`: a NON-superuser, NON-BYPASSRLS
-- role, so the row-level-security policies added from migration 002 onward are actually
-- ENFORCED (a superuser silently bypasses RLS). DDL/migrations keep running as the owner
-- (`growth_operator`) via GROWTH_OPERATOR_DATABASE_MIGRATOR_URL — app_rw has no DDL rights.
--
-- Idempotent: safe to re-run on every environment bring-up. Roles are cluster-global, so
-- this lives in infra (not a schema migration).
--
-- DEV password below is a throwaway local credential (mirrors the plaintext
-- growth_operator/growth_operator already in docker-compose.dev.yml) — NOT a secret.
-- In staging/prod, create app_rw with a password sourced from SOPS and point
-- GROWTH_OPERATOR_DATABASE_URL at it; never commit a real password.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_rw') THEN
    CREATE ROLE app_rw LOGIN PASSWORD 'app_rw'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;

-- Noisy-neighbor guard (multi-tenant-rls.md): cap statement time for the app role.
ALTER ROLE app_rw SET statement_timeout = '5s';

-- Privileges on existing objects.
GRANT USAGE ON SCHEMA public TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
-- EXECUTE on functions — e.g. the SECURITY DEFINER resolve_api_key() auth lookup (MVP-018).
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO app_rw;

-- Privileges on FUTURE objects created by the migrator (owner). This is what keeps every
-- later migration's tables/functions reachable by app_rw without a manual re-grant.
ALTER DEFAULT PRIVILEGES FOR ROLE growth_operator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE growth_operator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE growth_operator IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO app_rw;

-- audit_log is append-only (MVP-024): keep UPDATE/DELETE revoked even though the blanket
-- grant above hands them out. The BEFORE UPDATE/DELETE trigger is the ultimate guard; this
-- is defense-in-depth. Guarded so roles.sql still runs before the table exists.
DO $$
BEGIN
  IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'audit_log') THEN
    REVOKE UPDATE, DELETE ON audit_log FROM app_rw;
  END IF;
END
$$;

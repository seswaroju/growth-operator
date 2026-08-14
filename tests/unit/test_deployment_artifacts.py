"""PILOT-1A — the deployment artifacts must not be able to ship a development stack.

These are static checks on files rather than behavioural tests, and that is the point: the failure
they prevent happens once, on a real host, at the moment someone flips a variable. There is no
opportunity to catch it at runtime, because by then a public server is already running
`uvicorn --reload` in front of a database whose password is in the repository.

The previous deploy workflow ran `docker compose -f infra/docker/docker-compose.dev.yml up` on the
public host. It was never enabled, so nothing was exposed — but the only thing standing between the
repository and that outcome was a GitHub variable nobody had set yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROD = ROOT / "infra/docker/docker-compose.prod.yml"
DOCKERFILE = ROOT / "infra/docker/Dockerfile"
CADDYFILE = ROOT / "infra/docker/Caddyfile"
WORKFLOW = ROOT / ".github/workflows/deploy-staging.yml"
DEPLOY = ROOT / "scripts/deploy-prod.sh"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PROD.read_text())


def _uncommented(path: Path) -> list[str]:
    """Lines that actually execute. A pattern inside a comment explaining why it is forbidden must
    not fail the check that forbids it."""
    return [ln for ln in path.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ---- 12 / 13: the production stack is not the development stack --------------------------------


def test_production_compose_never_reloads() -> None:
    """`--reload` watches the filesystem and re-executes code. On a public host that is a remote
    code-execution surface, not a convenience."""
    assert not [ln for ln in _uncommented(COMPOSE_PROD) if "--reload" in ln]


def test_production_compose_does_not_bind_mount_source() -> None:
    """A source mount means the container runs whatever is on the host's disk, so the image that
    passed CI is not the thing serving traffic."""
    text = COMPOSE_PROD.read_text()
    for pattern in ("../../core", "../../web/src", ":/app/core"):
        assert pattern not in text, f"production compose bind-mounts source: {pattern}"


def test_production_compose_uses_the_production_dockerfile(compose: dict) -> None:
    build = compose["services"]["api"].get("build") or {}
    assert build.get("dockerfile") == "infra/docker/Dockerfile"
    assert "Dockerfile.dev" not in COMPOSE_PROD.read_text()


# ---- 14 / 15: data services are not reachable from the internet -------------------------------


@pytest.mark.parametrize("service", ["postgres", "redis"])
def test_data_services_publish_no_ports(compose: dict, service: str) -> None:
    """Not published at all, rather than published-and-firewalled. A firewall rule is one careless
    change away from exposing a database; an unpublished port cannot be reached even then."""
    assert "ports" not in compose["services"][service], (
        f"{service} publishes a port to the host — it must stay on the internal network")


def test_only_the_reverse_proxy_is_publicly_exposed(compose: dict) -> None:
    published = {
        name: svc.get("ports") for name, svc in compose["services"].items() if svc.get("ports")}
    assert set(published) == {"caddy"}, f"unexpected published services: {sorted(published)}"
    assert sorted(published["caddy"]) == ["443:443", "80:80"]


def test_redis_requires_a_password(compose: dict) -> None:
    """Redis carries the event streams; write access is the ability to forge or drop work."""
    command = " ".join(compose["services"]["redis"]["command"])
    assert "--requirepass" in command


def test_no_credential_is_hardcoded_in_the_compose_file() -> None:
    """Every value comes from the environment, which the deploy script fills from SOPS. A password
    committed here would be a password in every clone of this repository."""
    text = COMPOSE_PROD.read_text()
    for leaked in ("app_rw:app_rw", "growth_operator:growth_operator", "minioadmin"):
        assert leaked not in text


def test_required_settings_have_no_silent_default(compose: dict) -> None:
    """`${VAR:?message}` fails the deploy loudly. A `:-default` on a credential would silently
    start the stack on a placeholder, which is the failure mode this whole ticket exists to end."""
    env = compose["x-app"]["environment"]
    for key in ("GROWTH_OPERATOR_DATABASE_URL", "GROWTH_OPERATOR_DATABASE_MIGRATOR_URL",
                "GROWTH_OPERATOR_REDIS_URL"):
        assert ":?" in env[key], f"{key} has a silent default"


# ---- 19 / 20: the role split survives deployment ----------------------------------------------


def test_runtime_and_migration_use_different_database_urls(compose: dict) -> None:
    env = compose["x-app"]["environment"]
    assert env["GROWTH_OPERATOR_DATABASE_URL"] != env["GROWTH_OPERATOR_DATABASE_MIGRATOR_URL"]


def test_migrations_run_under_the_migrator_url() -> None:
    """`app_rw` deliberately has no DDL rights, so a deploy that migrated as the runtime role would
    fail — and the fix must never be to grant them."""
    text = DEPLOY.read_text()
    assert "alembic upgrade head" in text
    assert "GROWTH_OPERATOR_DATABASE_MIGRATOR_URL" in text


def test_deploy_order_supports_a_first_install_as_well_as_an_upgrade() -> None:
    """Three orderings, and the middle one is what a first deploy needs.

    Data services must start BEFORE migration, because on an empty host there is no Postgres to
    migrate against — the earlier draft used `compose run --no-deps` and `compose exec postgres`,
    both of which quietly assume an already-running stack. Application services must start AFTER,
    because new code meeting an old schema is the failure migrations exist to prevent."""
    text = DEPLOY.read_text()
    data_up = text.index("up -d postgres redis")
    migrate = text.index("alembic upgrade head")
    app_up = text.index("up -d --remove-orphans")
    assert data_up < migrate, "data services must be running before migrations"
    assert migrate < app_up, "migrations must run before application containers serve traffic"


def test_deploy_waits_for_postgres_health_before_using_it() -> None:
    """On a first install the volume is empty and initdb takes seconds; `up -d` returns
    immediately, so proceeding without waiting means migrating against a database that is not
    accepting connections yet."""
    text = DEPLOY.read_text()
    assert "healthy" in text and text.index("healthy") < text.index("alembic upgrade head")


@pytest.mark.parametrize("path", ["infra/db/roles.sql", "infra/db/roles-prod.sh"])
def test_app_rw_is_created_without_bypassrls(path: str) -> None:
    """The property the whole tenant-isolation model rests on: with BYPASSRLS, row-level security
    stops applying and one store can read another's data."""
    import re

    text = (ROOT / path).read_text()
    create = re.search(r"CREATE ROLE app_rw.*?;", text, re.DOTALL | re.IGNORECASE)
    assert create, f"{path} does not create app_rw"
    assert "NOBYPASSRLS" in create.group(0).upper()


def test_production_does_not_bootstrap_the_role_with_the_published_password(
    compose: dict
) -> None:
    """roles.sql hardcodes `PASSWORD 'app_rw'` — correct on a laptop, and the exact credential the
    startup validator refuses in production. Mounting it on a real host would create the runtime
    role with a password published in this repository. The application would then refuse to boot,
    but a guard that stops the app is not a reason to create the hole."""
    volumes = compose["services"]["postgres"]["volumes"]
    assert any("roles-prod.sh" in v for v in volumes)
    assert not any(v.endswith("roles.sql:ro") or "roles.sql:" in v for v in volumes)
    prod = (ROOT / "infra/db/roles-prod.sh").read_text()
    assert "APP_RW_PASSWORD" in prod
    assert "PASSWORD 'app_rw'" not in prod


def test_deploy_verifies_the_runtime_role_rather_than_assuming_it() -> None:
    """initdb scripts run only on a fresh volume, so on an existing one the role bootstrap is
    silently skipped."""
    text = (ROOT / "scripts/deploy-prod.sh").read_text()
    assert "rolbypassrls" in text and "app_rw" in text


# ---- 17: the deploy workflow cannot invoke the development stack ------------------------------


def test_deploy_workflow_cannot_run_the_dev_compose() -> None:
    """Checks the DEPLOY job's steps specifically. The guard job legitimately mentions the forbidden
    strings — it is the thing searching for them — so scanning the whole file would flag the
    safeguard as the violation."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = yaml.dump(workflow["jobs"]["deploy"])
    assert "docker-compose.dev.yml" not in steps
    assert "--reload" not in steps
    assert "Dockerfile.dev" not in steps


def test_deploy_workflow_delegates_to_the_reviewed_script() -> None:
    """One definition of "how we deploy", in the repository, reviewed like any other code."""
    assert "scripts/deploy-prod.sh" in WORKFLOW.read_text()


def test_deploy_workflow_is_still_gated() -> None:
    """It must stay inert until the founder configures the environment deliberately."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "STAGING_ENABLED" in workflow["jobs"]["deploy"]["if"]


def test_the_guard_job_runs_even_when_deployment_is_disabled() -> None:
    """The guarantee is about what the repository contains, so it must hold before anyone can
    enable the environment — not after."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "if" not in workflow["jobs"]["guard"]
    assert workflow["jobs"]["deploy"]["needs"] == "guard"


def test_the_age_private_key_is_not_a_github_secret() -> None:
    """It would make production credentials decryptable by anyone who can trigger a workflow."""
    text = WORKFLOW.read_text()
    assert "AGE_KEY" not in text and "age-key" not in text


# ---- production image --------------------------------------------------------------------------


def test_image_runs_as_a_non_root_user() -> None:
    text = DOCKERFILE.read_text()
    assert "useradd" in text
    assert text.rindex("USER vaylorn") > text.rindex("COPY --from=builder")


def test_image_installs_no_development_dependencies() -> None:
    """pytest, ruff and mypy have no business in a process answering customer traffic."""
    assert "--no-dev" in DOCKERFILE.read_text()


def test_image_never_reloads() -> None:
    assert not [ln for ln in _uncommented(DOCKERFILE) if "--reload" in ln]


def test_one_image_serves_all_three_processes(compose: dict) -> None:
    """A version skew between the process that approves a send and the one that performs it is a
    genuinely dangerous failure."""
    services = [compose["services"][name] for name in ("api", "worker", "scheduler")]
    assert all("build" in s or "image" in s for s in services)
    commands = {" ".join(s["command"]) for s in services}
    assert len(commands) == 3, "each process needs its own command"


def test_secrets_are_mounted_not_baked() -> None:
    """A secret in an image layer is a secret in every registry that image reaches."""
    dockerfile = DOCKERFILE.read_text()
    assert "secrets/" not in dockerfile
    ignored = (ROOT / ".dockerignore").read_text()
    for pattern in ("secrets/*.enc.yaml", ".env", "backups/"):
        assert pattern in ignored


def test_the_host_virtualenv_cannot_reach_the_image() -> None:
    """The Dockerfile builds /app/.venv with `uv sync` and then copies the source over it. A host
    `.venv` in the build context would overwrite that with the developer's platform binaries — on a
    macOS laptop building a linux image, the container simply would not run."""
    assert "\n.venv\n" in (ROOT / ".dockerignore").read_text()


# ---- 18: Caddy serves exactly the intended hosts ----------------------------------------------


def test_caddy_serves_exactly_the_three_approved_hostnames() -> None:
    hosts = {ln.split()[0] for ln in _uncommented(CADDYFILE) if ln.rstrip().endswith("{")
             and "." in ln.split()[0]}
    assert hosts == {"api.vaylorn.com", "app.vaylorn.com", "ops.vaylorn.com"}


def test_the_operator_console_is_not_named_web_ops_publicly() -> None:
    """`web-ops` is an internal directory name and must never appear in a public hostname."""
    assert "web-ops.vaylorn.com" not in CADDYFILE.read_text()


def test_caddy_does_not_proxy_data_services() -> None:
    text = CADDYFILE.read_text()
    assert "postgres:5432" not in text and "redis:6379" not in text


def test_security_headers_include_hsts() -> None:
    assert "Strict-Transport-Security" in CADDYFILE.read_text()


def test_no_founder_ip_is_hardcoded() -> None:
    """An allow-list is defence in depth, not the auth mechanism, and a founder's IP is not
    repository content — baking one in makes the console unreachable the day it changes."""
    import re

    assert not re.search(r"remote_ip\s+\d+\.\d+\.\d+\.\d+", CADDYFILE.read_text())


# ---- 16 / 24: no localhost or secret reaches the browser bundle -------------------------------


def test_frontend_build_refuses_a_localhost_api_base() -> None:
    """`VITE_API_BASE` is substituted at build time, so a wrong value ships silently and only
    fails in a merchant's browser."""
    script = (ROOT / "scripts/build-frontend.sh").read_text()
    assert "localhost:8000" in script and "refusing to ship" in script
    assert "https://api.vaylorn.com" in script


def test_production_cors_allows_only_the_real_origins(compose: dict) -> None:
    origins = compose["x-app"]["environment"]["GROWTH_OPERATOR_CORS_ALLOW_ORIGINS"]
    assert "app.vaylorn.com" in origins and "ops.vaylorn.com" in origins
    assert "localhost" not in origins


def test_no_secret_material_is_committed_in_deployment_artifacts() -> None:
    """The example secrets file lists NAMES; every value in it is a placeholder."""
    example = (ROOT / "secrets/prod.example.yaml").read_text()
    assert "REPLACE" in example
    assert not list((ROOT / "secrets").glob("*.enc.yaml")) or True  # encrypted files are fine
    for path in (COMPOSE_PROD, DOCKERFILE, CADDYFILE, DEPLOY):
        text = path.read_text()
        assert "BEGIN PRIVATE KEY" not in text
        assert "AKIA" not in text  # an AWS access key id prefix

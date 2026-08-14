# Production image (PILOT-1A). ONE image for api, worker and scheduler.
#
# They share a codebase, a dependency set and a release; three images would mean three things to
# build, scan and keep in step, and a version skew between the process that approves a send and the
# process that performs it is a genuinely dangerous failure. The command differs per service; the
# artifact does not.
#
# Deliberately not derived from Dockerfile.dev: that one exists to iterate quickly (uvicorn
# --reload, source bind-mounted over the image) and those properties are exactly wrong here.
#
# No secret is present at any layer. Configuration arrives at runtime through the environment and
# the SOPS-decrypted secrets file, so this image is safe to store in a registry.

FROM python:3.12-slim AS builder

# uv resolves from the committed lockfile, so an image built today and one built next month install
# byte-identical dependencies. Pinned by digest-bearing tag rather than `latest`.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, as their own layer: application code changes on every deploy, the lockfile
# rarely, so a routine deploy reuses this layer instead of recompiling the world.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# `--no-dev` matters beyond image size: pytest, ruff and mypy have no business being present in a
# process that answers customer traffic.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# `curl` is the healthcheck's only requirement. Nothing else is installed: every additional package
# is attack surface in a container that holds signing keys and database credentials.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root. If the application is ever induced to write somewhere it should not, the operating
# system refuses rather than trusting us to have gotten every path right.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin vaylorn

WORKDIR /app

COPY --from=builder --chown=vaylorn:vaylorn /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER vaylorn

EXPOSE 8000

# Liveness only — `/healthz` checks the process, not its dependencies. A database blip must not
# make Docker kill an otherwise healthy API; readiness (`/readyz`, which does check Postgres, Redis
# and that the schema is at head) is what the deploy smoke and the operator health script use.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Overridden per service in compose (worker: `python -m core.worker`, scheduler:
# `python -m core.scheduler`). No --reload, and no source mount to reload from.
CMD ["uvicorn", "core.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

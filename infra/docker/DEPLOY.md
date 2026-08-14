# Deploying Vaylorn (PILOT-1A)

Two paths. `scripts/deploy-prod.sh` handles both — the difference is entirely in what already
exists on the host, not in what you run.

The first install is the one nobody gets to practise, so it is the one written down.

---

## First install — empty Linux VPS

### 1. Host preparation (founder, once)

```bash
# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh

# A non-root deploy user
adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy

# Firewall: only 80, 443 and SSH. Postgres and Redis publish no ports at all, so they are
# unreachable from outside the Docker network even if this were misconfigured.
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable

apt-get install -y age sops postgresql-client   # decryption + backup tooling
```

### 2. Repository and secrets

```bash
git clone <repo> /opt/vaylorn && cd /opt/vaylorn

# The age PRIVATE key, copied by hand from the founder's machine. Never a GitHub secret:
# that would let anyone who can trigger a workflow decrypt production credentials.
install -Dm600 /path/to/keys.txt ~/.config/sops/age/keys.txt

sops -d secrets/prod.enc.yaml >/dev/null   # prove decryption works BEFORE deploying
```

Set the host environment (`/etc/vaylorn.env`, mode `0600`) with the values `docker-compose.prod.yml`
requires — `POSTGRES_USER`, `POSTGRES_PASSWORD`, `APP_RW_PASSWORD`, `REDIS_PASSWORD`, `ACME_EMAIL`,
and the three `GROWTH_OPERATOR_*` URLs. Each is declared `${VAR:?}`, so a missing one fails the
deploy loudly rather than starting on a placeholder.

### 3. Frontends

```bash
scripts/build-frontend.sh      # bakes VITE_API_BASE=https://api.vaylorn.com
```

The script greps the emitted bundle and refuses to finish if a localhost base survived — that value
is substituted at build time, so a wrong one ships silently and only fails in a merchant's browser.

### 4. Deploy

```bash
scripts/deploy-prod.sh prod
```

Which runs, in this order and for these reasons:

| Step | Why this position |
|---|---|
| decrypt secrets | a container without them refuses to boot; failing here is cheapest |
| build image | one immutable artifact for api/worker/scheduler |
| **start Postgres + Redis, wait healthy** | on an empty host there is nothing to migrate against yet |
| verify `app_rw` | created by an initdb script that runs only on a fresh volume — verified, never assumed |
| migrate as **owner** | `app_rw` has no DDL rights, and the fix must never be to grant them |
| start api/worker/scheduler | new code must not meet an old schema |
| start Caddy | requests TLS certificates once DNS resolves |
| health check | readiness, not "docker says running" |

### 5. DNS and TLS

Point `api.` / `app.` / `ops.vaylorn.com` at the droplet. Caddy obtains certificates automatically
on first request. **TLS issuance is a real-host acceptance item** — it cannot be proven in CI,
because it requires public DNS and a reachable port 80.

### 6. Backups

```bash
crontab -e
15 3 * * *  cd /opt/vaylorn && scripts/backup-nightly.sh >> /var/log/vaylorn-backup.log 2>&1
```

---

## Repeat deploy

```bash
cd /opt/vaylorn && git fetch --all && git reset --hard origin/main
scripts/build-frontend.sh        # only when web/ or web-ops/ changed
scripts/deploy-prod.sh prod
```

Identical script. `up -d postgres redis` is a no-op on a running stack; the role check passes; new
migrations apply; containers are recreated only where the image changed. Consumers are idempotent
and send claims are durable (PILOT-1C), so an unclean restart replays safely rather than
double-sending.

---

## What was verified without a server

Proven locally against genuinely empty containers, `tests/unit/test_deployment_artifacts.py`, and
`tests/unit/test_startup_safety.py`:

* production image builds from a clean context, runs as uid 10001, contains no dev dependencies,
  no host virtualenv and no secrets
* `docker compose config` validates with dummy values; only Caddy publishes ports (80/443)
* an empty Postgres + `roles-prod.sh` produces `app_rw` with `bypassrls=false`, `superuser=false`
* the published development password `app_rw` is **rejected**; the real one authenticates
* `alembic upgrade head` applies all 70 migrations as the owner, reaching head `d53fdc8c9b82`
* `app_rw` is refused DDL (`permission denied for schema public`) and permitted normal DML
* RLS bites on a freshly bootstrapped database: 65 of 92 tables FORCE RLS, and an org-scoped read
  with no tenant context returns 0 rows while the owner sees 1
* `caddy validate` passes and the Caddyfile is in canonical `caddy fmt` form
* both frontends build clean of `localhost:8000`, `:5173`, `:5174` and `127.0.0.1`

**Not verifiable without a host:** real DNS, Let's Encrypt issuance, actual droplet firewall
behaviour, and cron execution. Those are acceptance items for the first real deploy.

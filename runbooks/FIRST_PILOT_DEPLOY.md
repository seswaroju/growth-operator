# First pilot deploy — founder runbook

**In this repository, not `docs/`.** `docs/` is a symlink to the Obsidian vault and is read-only
from here (CLAUDE.md §4). A runbook you follow while a merchant waits should also live with the
scripts it invokes, so the two cannot drift apart.

Target: **fresh VPS → healthy Vaylorn in ≤ 30 minutes**, excluding DNS propagation and certificate
issuance, which are not ours to control.

No architecture knowledge required. Work top to bottom. Every command shows what success looks
like, so you can tell "it worked" from "it printed something".

> **No secret values appear in this file.** Where a value is needed it is named, never shown.

---

## Before the VPS exists

These have waiting time and do not need a server. Do them early.

- [ ] **age key** — `age-keygen -o ~/.config/sops/age/keys.txt`. Prints a **public** recipient
      (`age1…`). Back the file up somewhere you would not lose in a laptop failure. It is the only
      thing that can decrypt production secrets, and it is never committed.
- [ ] **`.sops.yaml`** — replace the placeholder recipient with your public key.
- [ ] **`secrets/prod.enc.yaml`** — copy `secrets/prod.example.yaml`, replace every value
      (generation commands are in its comments), then:
      `sops --encrypt /tmp/prod.plain.yaml > secrets/prod.enc.yaml && rm /tmp/prod.plain.yaml`.
      Commit the **encrypted** file; that is what it is for.
- [ ] **Meta** — business account and verification. The long pole; start first.
- [ ] **WhatsApp template** — submit `pilot_recovery_check_in`. Approval is not instant.
- [ ] **SMTP** — account plus SPF/DKIM on `vaylorn.com`, or merchant OTPs land in spam.
- [ ] **LLM key** — one is enough to start.

---

## Cutover — the 30 minutes

### 1. Create the droplet · ~3 min
- [ ] DigitalOcean **BLR1**, Basic, **2 vCPU / 4 GiB**, Ubuntu LTS, your SSH key.
- [ ] Note the IPv4 address.

### 2. Prepare the host · ~4 min

```bash
ssh root@<ip>
curl -fsSL https://raw.githubusercontent.com/<repo>/main/scripts/bootstrap-host.sh \
  | SSH_ALLOW=<your.ip> bash
```

Installs Docker, Compose, git, age, sops and `postgresql-client`; creates the `deploy` user and
`/opt/vaylorn`; sets the firewall to **your SSH only, plus 80 and 443**.

✅ ends with `Host is ready.` and a `ufw status` table showing 22 (from your IP), 80, 443.

### 3. Repository and key · ~2 min

```bash
sudo -iu deploy
git clone <repo> /opt/vaylorn && cd /opt/vaylorn
install -Dm600 /path/to/keys.txt ~/.config/sops/age/keys.txt
sops -d secrets/prod.enc.yaml >/dev/null && echo "decryption OK"
```

✅ `decryption OK`. **If this fails, stop** — every later step depends on it, and the failure is
much cheaper here than half-deployed.

### 4. Host environment · ~2 min

Create `/etc/vaylorn.env`, mode `0600`, defining: `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`APP_RW_PASSWORD`, `REDIS_PASSWORD`, `ACME_EMAIL`, `GROWTH_OPERATOR_DATABASE_URL` (as `app_rw`),
`GROWTH_OPERATOR_DATABASE_MIGRATOR_URL` (as the owner), `GROWTH_OPERATOR_REDIS_URL`.

Each is declared `${VAR:?}` in the compose file, so a missing one **fails the deploy loudly**
rather than starting on a placeholder.

```bash
sudo install -m600 /dev/null /etc/vaylorn.env && sudo -e /etc/vaylorn.env
set -a && . /etc/vaylorn.env && set +a
```

### 5. Frontends · ~3 min

```bash
scripts/build-frontend.sh
```

✅ `ok: no localhost API base in web/dist` (and `web-ops/dist`). The API base is baked in at build
time, so a wrong one ships silently and only fails in a merchant's browser — hence the check.

### 6. Deploy · ~10 min (mostly the image build)

```bash
scripts/deploy-prod.sh prod
```

Runs, in order: decrypt secrets → build image → **start Postgres/Redis and wait healthy** →
verify `app_rw` → migrate as the owner → start api/worker/scheduler → start Caddy → health check.

✅ `postgres healthy` · `app_rw present, NOBYPASSRLS confirmed` · migrations to head · `ready after
N attempt(s)` · then the health check runs automatically.

If it stops at **`app_rw is missing`**: the volume already existed, so the one-time role bootstrap
did not re-run. Create it with `infra/db/roles-prod.sh` (needs `APP_RW_PASSWORD`) and re-run.

### 7. Verify · ~1 min

```bash
scripts/pilot-health-check.sh
```

✅ `pilot looks healthy` — API up and ready, TLS date, all six containers running, Postgres/Redis
reachable, schema at head.

### 8. DNS · ~2 min, then waiting

- [ ] `A  api.vaylorn.com  → <ip>`
- [ ] `A  app.vaylorn.com  → <ip>`
- [ ] `A  ops.vaylorn.com  → <ip>`

Then Caddy issues certificates automatically on first request. **Propagation and issuance are
outside the 30 minutes** — they depend on your registrar and Let's Encrypt, not on us.

```bash
curl -sI https://api.vaylorn.com/healthz | head -1     # HTTP/2 200
```

### 9. Activation

- [ ] **OTP** — `otp_email_enabled=true` with SMTP set; log in at `app.vaylorn.com`. No developer
      shortcut exists outside dev, by design.
- [ ] **LLM** — key present; a real provider call succeeds.
- [ ] **Meta** — callback `https://api.vaylorn.com/webhooks/whatsapp`, verify token matching your
      secrets file, `messages` subscribed, then `POST /v1/channels/whatsapp/connect`.
- [ ] **Backups**
      ```bash
      crontab -e
      15 3 * * *  cd /opt/vaylorn && scripts/backup-nightly.sh >> /var/log/vaylorn-backup.log 2>&1
      ```

---

## Repeat deploys

```bash
cd /opt/vaylorn && git fetch --all && git reset --hard origin/main
scripts/build-frontend.sh          # only when web/ or web-ops/ changed
scripts/deploy-prod.sh prod
```

Same script. Starting data services is a no-op on a running stack. Consumers are idempotent and
send claims are durable (PILOT-1C), so an unclean restart replays safely rather than double-sending.

---

## If something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `refusing to start: N unsafe setting(s)` | a value is still a repository default | supply it in the secrets file — the message names each setting |
| `no decrypted secrets file` | age key missing or wrong | re-check step 3 |
| `app_rw is missing` | existing volume skipped the one-time bootstrap | run `infra/db/roles-prod.sh` |
| `app_rw has BYPASSRLS` | role created wrongly | **stop** — tenant isolation is not enforced; recreate the role |
| deploy times out at readiness | migrations or a dependency | `docker compose -f infra/docker/docker-compose.prod.yml logs api` |
| no certificate | DNS not resolving yet | wait; check `dig api.vaylorn.com` |

Nothing is rolled back automatically. A half-finished deploy that stopped is far easier to reason
about than one that silently reverted underneath you.

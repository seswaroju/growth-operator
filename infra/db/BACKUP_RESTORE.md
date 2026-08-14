# Database backup & restore (security-hardening S3, audit #16e)

Audit finding #16e was **"backups that have never been restored."** The fix is not just *making*
backups — an untested backup is a false sense of safety. The centerpiece here is a **restore drill**
that proves a backup actually restores, run **continuously in CI** and on demand locally.

## TL;DR

```bash
make backup        # write a compressed dump into ./backups (needs host pg tools / run on server)
make backup-drill  # PROVE a backup restores — dump → restore into scratch → verify → drop scratch
make restore DUMP=backups/growth_operator-YYYYmmdd-HHMMSS.dump TARGET=growth_operator_restore
```

`./backups/` and `*.dump` are **gitignored** — dumps contain real customer data and must never be
committed.

## The restore drill (the important part)

`scripts/db_restore_drill.sh` dumps the live DB, restores it into a throwaway scratch database
(`<db>_restore_drill`), and verifies the restore matched the source on three axes:

- **table count** (the schema came back),
- **`alembic_version`** (restored at the same migration head),
- **`organizations` row count** (data round-tripped).

It then drops the scratch DB and prints `PASS`/`FAIL`, exiting non-zero on any mismatch. It only ever
creates and drops its **own** scratch DB and never writes to the source.

- **In CI:** the `migrate` job runs the drill on every push, right after `alembic upgrade head`. So
  the restore path is proven continuously, not assumed. (This is what closes #16e.)
- **Locally:** `make backup-drill` pipes the drill into the dev Postgres container, so it works with
  just Docker — no host `pg_dump`/`psql` needed.

Verified locally against pg16: 71 tables, alembic head `9f9334d2999a`, org rows round-tripped → PASS.

## Backup

`scripts/db_backup.sh` writes `backups/<db>-<timestamp>.dump` (pg_dump custom format, compressed,
`--no-owner --no-privileges` for portable restores). Connection comes from
`GROWTH_OPERATOR_DATABASE_MIGRATOR_URL` (the owner role) or the local dev default. Needs
`pg_dump` on PATH — run it on the DB host/server for real backups.

## Restore

`scripts/db_restore.sh <dump> <target-db> [--force]` drops + recreates `<target-db>` and restores the
dump into it. **Guardrails:** it refuses any target whose name contains `prod`, and refuses to
overwrite the primary database (the one in the connection URL) unless you pass `--force`. Restore into
a scratch DB first, inspect, then promote — don't restore straight over a live database.

## Not yet automated (production, deferred to infra)

This ticket delivers the mechanism + continuous proof. The production hardening is tracked in
`project-management/PRODUCTION_DEPTH_BACKLOG.md`:

- **Scheduled** backups (cron/managed) with **retention** (e.g. daily 7d + weekly 4w).
- **Off-site + encrypted** storage (object storage, encryption at rest; a dump is sensitive data).
- **Point-in-time recovery** (WAL archiving) for RPO ≈ minutes, and a **scheduled** restore drill
  against real backups (not just the CI schema check).
- A documented **RTO/RPO** target for the pilot.

---

## Pilot production schedule (PILOT-1A)

`scripts/backup-nightly.sh` wraps the mechanism above with the three things a bare dump lacks:
encryption before the file leaves the host, an S3-compatible off-site copy, and pruning.

```cron
15 3 * * *  cd /opt/vaylorn && scripts/backup-nightly.sh >> /var/log/vaylorn-backup.log 2>&1
```

Environment (set on the host, not in git):

| Variable | Purpose |
|---|---|
| `BACKUP_DIR` | local dump directory (default `/var/backups/vaylorn`) |
| `BACKUP_AGE_RECIPIENT` | the founder's **public** age key — same one SOPS uses |
| `BACKUP_S3_BUCKET` | off-site bucket |
| `BACKUP_S3_ENDPOINT` | S3-compatible endpoint (Spaces, B2, MinIO); omit for AWS |
| `BACKUP_KEEP_DAYS` | local retention, default 7 |

**Encrypted before it leaves the host.** A dump is every customer phone number, every conversation
and every price the store has quoted. Object storage is durable, not confidential, and a bucket
that is public by accident is a routine incident. Reusing the SOPS age recipient means one key to
protect rather than two. If the recipient is unset the backup still runs and warns loudly — a
missing key must never mean no backup at all.

**No provider is hard-coded.** Any S3-compatible endpoint works, so changing providers is a
configuration change rather than a code change.

### Retention

* Host: 7 daily (`BACKUP_KEEP_DAYS`).
* Off-site: set a bucket lifecycle rule — 30 daily + 6 monthly is a reasonable pilot default.

### Weekly restore verification

A backup nobody has restored is a hypothesis. Weekly, on the host:

```bash
scripts/db_restore_drill.sh          # same drill CI runs, against a real dump
```

Restore a specific dump into a scratch database with `scripts/db_restore.sh`. Decrypt first if
encrypted: `age -d -i ~/.config/sops/age/keys.txt -o restore.dump backup.dump.age`.

### Deliberately out of scope

Point-in-time recovery. It means WAL archiving, more moving parts and a real operational burden;
the pilot's RPO of up to one day is an accepted decision, not an oversight. Revisit when a merchant's
data loss would cost more than the complexity.

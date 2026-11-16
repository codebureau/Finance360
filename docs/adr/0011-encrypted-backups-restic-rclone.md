# 0011. Encrypted backups: restic over rclone, local repo first

**Status:** Accepted
**Date:** 2026-10-17
**Issue:** — (serves the restore runbook)

## Context

ADR-0002/0007 and requirements §"Backup, recovery & durability" fix the shape: nightly (and
on-demand) client-side-encrypted backups, cloud storage off-site via rclone, a desktop copy, a key held
outside the system and findable by another household member. They leave open the tool, the contents, key handling,
retention, how the existing pre-migration copy and the pre-deploy backup fit, and how the desktop
copy arrives. `core/migrate.py` already makes a plaintext online-backup copy of the DB to
`<data>/backups/` before a migration. `jobs/` is an empty stub.

## Decision

**Tool: restic, not age (and not both).** restic gives client-side encryption, deduplicated
snapshots, retention (`forget --prune`), integrity checking (`check`) and a native rclone backend in
one binary. age encrypts a stream, so snapshots, retention and verification would be hand-built.
restic and rclone are installed in the image; `core/backup.py` drives restic as a subprocess.

**What is backed up (an allowlist, one snapshot):**
- the DB, as a SQLite online-backup copy (reusing `backup_database` from `core/migrate.py`) staged to
  `<data>/backups/staging/finance360.sqlite`. This includes `auth_event` (ADR-0009/0010);
- the `documents/` folder, in place;
- `manifest.json` staged beside the DB: schema revision, app version (`GIT_SHA`), UTC time, reason,
  contents list. No secrets, no key material.

Not backed up: `backups/` (no recursion), `setup-code`, `.env`, secrets files, the rclone config, the
backup key. Those are host configuration, recreated from the password manager. Settings live in the
DB, so "config" is covered by it. Snapshots use a fixed `--host finance360` (the container hostname
changes) and a `--tag` of `nightly`, `manual` or `pre-deploy`.

**Topology: a local restic repo on the host, copied to cloud storage.**
1. `restic backup` into the local repo `<data>/backups/restic`.
2. `restic copy` the new snapshot to the cloud storage repo (`-r rclone:<remote>:<path>`). A repo, not an
   `rclone sync` of loose files, so what lands in cloud storage is itself a valid, verifiable restic repo
   and a half-synced state cannot be mistaken for a backup. rclone only ever sees restic's
   encrypted blobs.
3. Both repos share the key and are created with `--copy-chunker-params`.

**Desktop copy: pulled by the desktop, not pushed by the host.** A desktop script in `scripts/`, run
by Windows Task Scheduler outside the app, does `restic copy --from-repo rclone:...` into a local
desktop repo. The desktop has its own rclone config and supplies the key itself. The host needs no
route to the desktop, and the result is a full, independent repo for fast restore. The app can't see
the desktop's health; its status shows the newest snapshot age in the cloud storage repo, and the runbook
says how to check the desktop one.

**Key management.** One restic repo password (the Backup key), 32+ random characters, generated once
by the owner. The app reads it from `FINANCE360_BACKUP_KEY_FILE` (a mounted secrets file; no plain-env
form) and gives it to restic via `RESTIC_PASSWORD_FILE` in the subprocess environment only. It is
never logged, never in the manifest, never beside the backup. It lives in the password manager in an
entry named for what it is, with a note pointing at the restore runbook (`docs/`); other household members
who may need to restore are given access to that entry. Key loss is data loss, and the runbook says so. restic supports
multiple key slots, so a recovery key can be added later without re-encrypting.

**rclone boundary.** The cloud storage remote is configured once on the host with `rclone config`, outside
the repo. The container mounts that directory (`FINANCE360_RCLONE_CONFIG_DIR`) and sets
`RCLONE_CONFIG`. The mount is **writable** because rclone rewrites the OAuth token on refresh: a
deliberate, documented exception to ADR-0007's "data volume is the only writable mount". The repo
holds only `.env.example` placeholders and a setup how-to.

**Retention (per repo, after each successful backup):**
`forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --keep-yearly 3 --group-by host,tags`,
with `pre-deploy` and `manual` snapshots limited to the latest 5 each. `prune` and `restic check`
run weekly on the local repo and on the cloud storage repo (slow over the network). Constants in code, not UI
settings.

**Pre-migration and pre-deploy.**
- The **pre-migration copy stays as is**: a plaintext, local, instant DB copy in `<data>/backups/`,
  needing no key, restic or network, so backup configuration can never block a migration. It is never
  shipped and is protected like the live DB (host volume, ADR-0002). Keep only the latest 5.
- The **pre-deploy backup is this job**, invoked by `deploy` as
  `docker compose exec finance360 python -m finance360.backup run --reason pre-deploy`, and
  blocking: if it fails, the deploy aborts.

**Triggers and execution.** One service function `run_backup(reason)` in `core/backup.py`. The
in-app scheduler (a single asyncio task in the app lifespan, nightly at `FINANCE360_BACKUP_TIME`,
default 02:00, catching up at startup if the last success is over 24h old), an owner-only on-demand
`POST` (behind auth) and the CLI all call it. One run at a time (lock). Exceptions are caught and
recorded, never propagated into the app.

**Status.** A `backup_run` table (id, reason, started_at, finished_at, status
`running|success|failed`, snapshot_id, size_bytes, shipped bool, sanitised short error) and an
authenticated `GET` returning the latest run and the latest successful run. `/health` stays
unauthenticated and unchanged. Status is flagged stale when no success is under 36h old, so a dead
scheduler shows. The status panel UI belongs to the status-panel work.

**Serving restore.** The snapshot contract above is the restore interface: restore pulls a
snapshot (latest or named; local, cloud storage or desktop repo) into a temp dir, runs `restic check`,
`PRAGMA integrity_check` and a manifest/schema-revision check, then moves `finance360.sqlite` and
`documents/` into the target. A wrong key or damaged repo fails before the target is touched.

## Consequences

- One tool does encryption, dedup, retention and verification. The design rests on one
  password-manager entry; losing the key loses everything.
- Each repo is independent, so corruption in one is not mirrored into the others.
- restic and rclone join the image; the container gains one writable mount.
- The desktop copy depends on a one-time scheduled task the app can't monitor.
- Pre-migration copies are plaintext on the host volume, with the same exposure as the live DB.
- Forecloses age as the backup format and rclone-sync-of-files as the transport.

## Resolved questions

The maintainer accepted all four on 2026-10-17:
1. **Recovery key:**
   - for M1, the household shares one restic repo password via a password-manager entry each member who may restore can open;
   - a second, revocable restic key slot can be added later, without re-encrypting.
2. **rclone config:**
   - it's a separate writable mount, because rclone rewrites the OAuth token on refresh;
   - this is a stated exception to ADR-0007's "the data volume is the only writable mount";
   - it is not kept in the data directory.
3. **Retention, per repo:**
   - keep 14 daily, 8 weekly, 12 monthly and 3 yearly;
   - keep the latest 5 `pre-deploy` and the latest 5 `manual` snapshots.
4. **Desktop copy:**
   - pulled daily by Windows Task Scheduler, with a catch-up run at logon;
   - the runbook says how to check that it's current.

## Not decided here / routine follow-on

- Minimal asyncio scheduler in `jobs/` (no generic job framework), `core/backup.py`, CLI,
  `backup_run` migration, authenticated endpoints, last-backup status display.
- Dockerfile (restic, rclone), compose mounts, one-time rclone setup doc, desktop pull script.
- `deploy` calling the pre-deploy backup; capping pre-migration copies at 5.
- Tests: local-folder remote, synthetic data dir, backup under concurrent writes, no key in logs.
- Backup after every Import (M2); restore tool and runbook; "last tested restore" field.

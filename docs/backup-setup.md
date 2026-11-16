# Backups: one-time setup

How to switch on encrypted Backups (ADR-0011). Do this once per host, and once per
desktop that keeps a copy. Restoring is a separate runbook: [`restore-runbook.md`](restore-runbook.md).
**Read "The Backup key" first: losing it loses every Backup.**

## What you get

Every night (and on demand), the app takes a restic snapshot of the database, the `documents/`
folder and a `manifest.json`, encrypted on the host with the **Backup key**, into a local repo
(`<data dir>/backups/restic`). If a remote is configured it then copies that snapshot to a second
restic repo on cloud storage, through rclone (any rclone backend). rclone only ever sees encrypted
blobs. The desktop pulls its own full copy from that remote (below).

The app's footer shows the last Backup: success, age, size and whether it reached the cloud storage remote. A
failure, or no success in 36 hours, is shown in red with the reason.

## 1. The Backup key

1. Generate a long random password (32 or more characters), once. For example, in PowerShell:
   `-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | % {[char]$_})`
2. Store it in the password manager as an entry named **"Finance360 Backup key"**, with this note:
   *"Encrypts every Finance360 backup. Without it the backups cannot be read. Restore runbook:
   `docs/restore-runbook.md` in the Finance360 repo."* Give any other household member who may need to restore access to that entry.
3. Put the same text in a file on the host, with no trailing text and permissions only the app can
   read. The container runs as uid 10001:
   ```
   install -m 0400 -o 10001 /dev/stdin /srv/finance360/secrets/backup-key
   ```
   The file stays outside the repo and outside the data directory.

## 2. The cloud storage remote (rclone)

On the host, configure rclone once, outside the repo:

1. Pick a config folder, e.g. `/srv/finance360/rclone`, owned by uid 10001 and writable (rclone
   rewrites the OAuth token when it refreshes, so this mount is writable on purpose; this is the
   one exception to "the data volume is the only writable mount").
2. Run `RCLONE_CONFIG=/srv/finance360/rclone/rclone.conf rclone config`, choose **n** (new remote),
   name it `offsite` (any name; the examples use this one), pick your cloud storage's type, and
   follow its sign-in. `rclone lsd offsite:` should list your folders.
3. Choose where the repo goes, e.g. `Finance360`. The restic repo is created there on the first
   Backup.

## 3. Switch it on

In the `.env` next to `docker-compose.yml` (see `.env.example`):

```
FINANCE360_BACKUP_KEY_FILE=/srv/finance360/secrets/backup-key
FINANCE360_BACKUP_RCLONE_REMOTE=rclone:offsite:Finance360
FINANCE360_RCLONE_CONFIG_DIR=/srv/finance360/rclone
# FINANCE360_BACKUP_TIME=02:00    # Household local time
```

On a host managed by `deploy`, put these lines in `deploy/target.local.env` on the desktop instead: `deploy`
rewrites the host's `.env` from it on every deploy ([`deploy.md`](deploy.md)).

Then `docker compose up -d`. Without `FINANCE360_BACKUP_KEY_FILE` the app runs as before and the
footer says "Backups are not set up on this instance." Without a remote it keeps the local repo
only ("local only" in the footer).

Check it: in the app click **Back up now**, or on the host run

```
docker compose exec finance360 python -m finance360.backup run --reason manual
```

The footer should say "Last backup: success ... copied off-site". `rclone lsd offsite:Finance360`
should now show a restic repo (`config`, `data`, `index`, `keys`, `snapshots`).

To look at the repos by hand inside the container (it has no home directory, so give restic a
cache and the key):

```
docker compose exec -e RESTIC_CACHE_DIR=/tmp/c -e RESTIC_PASSWORD_FILE=/run/secrets/finance360_backup_key finance360 restic -r /data/backups/restic snapshots
```

## 4. The desktop copy

The desktop pulls from the cloud storage remote; the host never needs a route to it.

1. Install [restic](https://restic.net) and [rclone](https://rclone.org) on the desktop.
2. Run `rclone config` there too (same `offsite` remote name, its own sign-in).
3. Save the Backup key to a file only you can read, e.g. `<key-file>` (a path only your user can read)
   (copy it out of the password manager).
4. In Task Scheduler create a task that runs
   `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <path-to-clone>\scripts\backup-pull.ps1 -KeyFile <key-file> -Remote rclone:offsite:Finance360 -Repo <desktop-repo-folder>`
   with two triggers: **Daily** (shortly after 03:00, after the nightly Backup) and **At log on**
   (so a day the desktop was off catches up). Tick *Run task as soon as possible after a scheduled
   start is missed*.
5. The first run creates the desktop repo, with the same chunker parameters, and copies every
   snapshot. Later runs copy only new ones.

**Is the desktop copy current?** The app cannot see it. On the desktop run
`restic -r <desktop-repo-folder> --password-file <key-file> snapshots --latest 1` and check the date,
or read `-LogFile`'s last lines (default `backup-pull.log` beside the repo).

## Housekeeping you do not need to do

Retention is automatic on the host's local repo and on the cloud storage remote: 14 daily, 8 weekly, 12 monthly,
3 yearly, and the latest 5 each of `manual` and `pre-deploy`. A weekly `prune` and `check` run on
both. The desktop script does not prune; to cap it run `restic forget --prune` with the same
policy occasionally, or leave it (restic deduplicates, so it grows slowly).

## Deploy

`deploy` must run, and require success from,
`docker compose exec finance360 python -m finance360.backup run --reason pre-deploy`
before replacing the container. Exit code 0 = a fresh Backup exists; 1 = it failed; 2 = Backups are
not set up; 3 = another backup is running. Any non-zero exit aborts the deploy.

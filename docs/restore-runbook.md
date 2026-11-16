# Restore runbook

How to rebuild Finance360 from an encrypted Backup (ADR-0011): on the same host after
damage or a bad change, on a fresh host after a disaster, or from the desktop copy. Setting Backups
up in the first place is [`backup-setup.md`](backup-setup.md).

## The Backup key: read this first

Every Backup is encrypted with the **Backup key**. **If the key is lost, every Backup is unreadable
and the data is gone.** There is no recovery and no back door.

- The key lives in the password manager, in the entry **"Finance360 Backup key"**. Every household
  member who may need to restore can open it.
- It is never in the Finance360 repo, never in the data directory and never beside a Backup.
- On a running host it is also in a file outside the data directory, e.g.
  `/srv/finance360/secrets/backup-key`, which the container reads through
  `FINANCE360_BACKUP_KEY_FILE`. On a fresh host you recreate that file from the password manager.

## What restore does

One command, run on the host with the app stopped:

```
docker compose run --rm finance360 python -m finance360.backup restore [--from local|remote|<repo>] [--backup latest|<id>]
```

1. It finds the Backup, the latest one unless `--backup` names an id, in the chosen repo:
   - `--from local` (the default) is the repo on this host, `<data dir>/backups/restic`;
   - `--from remote` is the cloud storage repo (`FINANCE360_BACKUP_RCLONE_REMOTE`);
   - `--from <path>` is any other restic repo, such as a copy of the desktop repo.
2. It restores the Backup into a work folder and checks it there:
   - `restic check` on the repo, and restic's own decryption checks;
   - `PRAGMA integrity_check` on the database;
   - the manifest, including a schema version this version of the app knows.
3. It migrates the database forward if the Backup is from an older version.
4. Only then does it touch the data directory. It moves the current `finance360.sqlite` and
   `documents/` aside into `backups/pre-restore-<UTC time>/` and moves the restored ones in. It
   never deletes the old data.

A wrong key, a missing Backup, a damaged repo or database, or a Backup from a newer version all fail
in steps 1 to 3, so the data directory is not touched.

Exit codes:

| Exit | Meaning |
|---|---|
| 0 | Restored. |
| 1 | Restore failed. The reason is printed; the data directory was not touched. |
| 2 | Backups are not set up: `FINANCE360_BACKUP_KEY_FILE` is not set. |
| 3 | The data directory is in use. The app is serving it, or another restore is running. Stop the app first. |

Restore refuses to run while the app is up. The app holds a lock on its data directory while it
serves, so `docker compose exec ... restore` against a running app exits 3. Use
`docker compose stop`, then `docker compose run --rm`, as below.

**Timing.** The restore itself takes seconds at today's size: about 6 s for the synthetic household
from either repo. Most of the hour budget is setting up a fresh host: Docker, the repo, the key
file and the cloud storage sign-in. Allow 30 to 60 minutes for a fresh host and 5 minutes on the same
host.

## A. Same host: damaged data or a bad change

The local repo is still there. In the folder with `docker-compose.yml`:

1. Stop the app: `docker compose stop`
2. Restore the latest Backup:
   `docker compose run --rm finance360 python -m finance360.backup restore`
   For an older one, add `--backup <id>` (see "Choosing a Backup" below).
3. Start the app: `docker compose up -d`
4. Verify it (see "After a restore").

If the local repo is damaged too, use `--from remote` in step 2.

## B. Fresh host: the old host or its disk is gone

You need the password manager (for the Backup key) and access to the cloud storage account.

1. Install Docker (with Compose) and `git`. Clone the Finance360 repo and check out the version
   you were running, or a newer one. A newer version migrates the Backup forward; an older one
   refuses it.
2. Recreate the key file from the password manager entry "Finance360 Backup key", exactly as in
   [backup-setup.md section 1](backup-setup.md#1-the-backup-key), step 3.
3. Set up rclone and sign in to the cloud storage again, as in
   [backup-setup.md section 2](backup-setup.md#2-the-cloud-storage-remote-rclone). Use the same remote
   name, `offsite`. Check it with `rclone lsd offsite:Finance360`, which should list `config`,
   `data`, `index`, `keys` and `snapshots`.
4. Write `.env` as in [backup-setup.md section 3](backup-setup.md#3-switch-it-on), with the same
   `FINANCE360_BACKUP_RCLONE_REMOTE`.
5. Restore from the remote into the new, empty data volume:
   `docker compose run --rm finance360 python -m finance360.backup restore --from remote`
6. Start the app with `docker compose up -d`, then verify it (see "After a restore"). The first
   nightly run creates a new local repo.

## C. From the desktop copy: the cloud storage is unreachable or its repo is damaged

The desktop repo (e.g. `<desktop-repo-folder>`) is a complete restic repo of its own.

1. Check that it is current (see the next section).
2. Copy the whole repo folder to the host, e.g. to `/srv/finance360/desktop-repo`, by USB drive or
   `scp`. Make it owned by the container user: `chown -R 10001 /srv/finance360/desktop-repo`.
3. If this is a fresh host, do steps 1 to 4 of B first. rclone is not needed for this path.
4. Stop the app if it is running, then restore from the copy:
   `docker compose run --rm -v /srv/finance360/desktop-repo:/restore-from finance360 python -m finance360.backup restore --from /restore-from`
5. Start the app with `docker compose up -d`, then verify it.

## Is the desktop copy current?

The app cannot see the desktop. On the desktop, run:

```
restic -r <desktop-repo-folder> --password-file <key-file> snapshots --latest 1
```

The newest time should be from last night. You can also read the last lines of `backup-pull.log`,
beside the repo, which should end in `Done.` with today's date. If the copy is stale, run
`scripts\backup-pull.ps1` by hand (setup: [backup-setup.md section 4](backup-setup.md#4-the-desktop-copy)).

## Choosing a Backup

To list the Backups in the local repo, run:

```
docker compose run --rm -e RESTIC_CACHE_DIR=/tmp/c -e RESTIC_PASSWORD_FILE=/run/secrets/finance360_backup_key finance360 restic -r /data/backups/restic snapshots
```

For the cloud storage remote, use `-r rclone:offsite:Finance360`. Each line has an id, a time and a tag
(`nightly`, `manual` or `pre-deploy`). Pass the id, or its first 8 characters, as `--backup`.

**Ids are per repo.** Copying a Backup off-site gives the copy a new id, so look the id up in the
repo you restore from.

## After a restore

1. **Health:** `curl http://localhost:<port>/health` should report `"status": "healthy"` and the
   current `schema_version`.
2. **Sign in.** The password is the one in use when the Backup was made.
3. **Position:** the totals should be what you expect for the Backup's date, and the history chart
   should run up to it.
4. **Audit trail:** the latest entries should be from just before the Backup's time.
5. **Backups:** the footer shows the Backup status. The run that made the restored Backup was still
   in progress when its database copy was taken, so the restored history shows it as
   "Interrupted". That is expected. The app starts a catch-up Backup by itself if the last success
   in the restored history is more than 24 hours old. If it doesn't, click **Back up now**.

**The previous data** is kept in `<data dir>/backups/pre-restore-<UTC time>/`, which holds
`finance360.sqlite` and `documents/` as they were before the restore. Restore never deletes it.

- **To undo the restore:** stop the app, move those files back over the restored ones, and start
  the app again.
- **Once you are satisfied,** delete that folder by hand. It is plaintext, like the live database.
- **If a restore was cut off mid-swap** (power loss, or the process was killed) and the data folder
  has no `finance360.sqlite`, or a partial one, then your previous data is in the newest
  `backups/pre-restore-<UTC time>/`. Keep the app stopped and move its `finance360.sqlite` (and any
  `-wal`/`-shm`) and `documents/` back into the data folder, replacing anything partial. Then
  either start the app on that data or run the restore again.

## When it fails

- **"Wrong Backup key":** the key file doesn't match the repo. Copy the key out of the password
  manager again. Check for a missing character, and make sure the file has no extra text.
- **"There is no Backup repository at ...":** there is no local repo on this host. Use
  `--from remote` or the desktop copy.
- **"No Backup '<id>'":** that id isn't in this repo (ids are per repo). List the Backups again.
- **"... which this version of Finance360 does not know":** the Backup was made by a newer version.
  Check out that version or a newer one, rebuild, and run the restore again.
- **"Checking the repository failed" or "Restoring failed":** the repo is damaged. Each repo is
  independent, so try another one: local, remote or the desktop copy.
- **"repository is locked":** a restic run on that repo didn't finish. Only when you're sure no
  Backup is running, clear the lock with
  `docker compose run --rm -e RESTIC_CACHE_DIR=/tmp/c -e RESTIC_PASSWORD_FILE=/run/secrets/finance360_backup_key finance360 restic -r <repo> unlock`.
- **Exit 3, "is in use":** the app is running (`docker compose stop`), or another restore is.

"""Host-side Backup CLI (ADR-0011).

``python -m finance360.backup run --reason pre-deploy`` takes a Backup. ``deploy`` uses it as
``docker compose exec finance360 python -m finance360.backup run --reason pre-deploy``, blocking:
a non-zero exit means "no fresh Backup", and the deploy must abort.
Exit codes: 0 success, 1 the backup failed, 2 Backups are not set up, 3 another run is live.

``python -m finance360.backup restore [--from local|remote|<repo>] [--backup latest|<id>]
[--target <data dir>]`` rebuilds a data directory from a Backup (runbook:
``docs/restore-runbook.md``). Stop the app first: restore refuses while the app serves the target.
Exit codes: 0 restored, 1 the restore failed and the target was not touched, 2 Backups are not
set up, 3 the target is in use (the app is serving it, or another restore is running).

Not imported by the app; it only calls ``core.backup``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finance360.config import Settings
from finance360.core import backup

REASONS = ("nightly", "manual", "pre-deploy", "maintenance")


def _run(reason: str) -> int:
    try:
        result = backup.run_backup(reason)
    except backup.BackupNotConfigured as err:
        print(f"backup: {err}", file=sys.stderr)
        return 2
    except backup.BackupBusy as err:
        print(f"backup: {err}", file=sys.stderr)
        return 3
    if not result.ok:
        print(f"backup: FAILED: {result.error}", file=sys.stderr)
        return 1
    where = "and copied off-site" if result.shipped else "(local repo only)"
    print(f"backup: {reason} succeeded {where}. Snapshot {result.snapshot_id or '-'}.")
    return 0


def _restore(source: str, which: str, target: str | None) -> int:
    settings = Settings.from_env()
    try:
        result = backup.restore(
            settings, source=source, snapshot=which, target=Path(target) if target else None
        )
    except backup.BackupNotConfigured as err:
        print(f"restore: {err}", file=sys.stderr)
        return 2
    except backup.DataDirBusy as err:
        print(f"restore: {err} Stop the app first (docker compose stop).", file=sys.stderr)
        return 3
    except backup.RestoreError as err:
        print(f"restore: FAILED: {err}", file=sys.stderr)
        return 1
    print(f"restore: restored Backup {result.snapshot_id[:8]} into {result.target}.")
    migrated = (
        f", migrated to {result.to_revision}" if result.to_revision != result.from_revision else ""
    )
    print(
        f"  Made {result.created_at} ({result.reason}) by app version {result.app_version}; "
        f"schema {result.from_revision}{migrated}."
    )
    if result.safety_copy is not None:
        print(f"  The previous database and documents are kept in {result.safety_copy}.")
    print("  Next: start the app (docker compose up -d) and check /health and the Position.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m finance360.backup",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="take a Backup now (or, with --reason maintenance, prune and check the repos)"
    )
    run.add_argument("--reason", choices=REASONS, required=True)
    restore = commands.add_parser(
        "restore", help="rebuild a data directory from a Backup (stop the app first)"
    )
    restore.add_argument(
        "--from",
        dest="source",
        default="local",
        metavar="local|remote|REPO",
        help="the local repo (default), the off-site repo, or a restic repo path or URL",
    )
    restore.add_argument(
        "--backup",
        dest="which",
        default="latest",
        metavar="latest|ID",
        help="the latest Backup (default) or one restic id, as listed by `restic snapshots`",
    )
    restore.add_argument(
        "--target", help="the data directory to restore into (default: FINANCE360_DATA_DIR)"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "restore":
        return _restore(args.source, args.which, args.target)
    return _run(args.reason)

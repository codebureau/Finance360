# 0007. Registry-free, tag-based deployment over an overlay VPN

**Status:** Accepted
**Amended by ADR-0012:** home-host access (LAN over HTTPS via a Caddy sidecar; an overlay VPN for mobile).
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §5d, §5e)

## Context

Real data must live only on hardware the owner controls, never be internet-exposed, and survive host
loss. The host is "any container host," for example a Linux box running Docker. An earlier
project's `deploy/redeploy.sh` (SSH, working-copy pull, rebuild) is a proven reference.

## Decision

- **The host builds from a git clone.** No container registry.
- **`deploy <tag>`** runs from the desktop over SSH (key-based) via the overlay VPN:
  - pre-deploy backup;
  - fetch and check out the **release tag**;
  - `docker compose up -d --build` (startup migrations take their own backup);
  - health check;
  - report.

  Rollback = deploy the previous tag, plus restore the pre-migration backup if the schema changed.
- **Release tags, not every merge.** The owner chooses when real data moves to a new version.
- **Carried over from that earlier `redeploy.sh`:**
  - gitignored per-host `deploy/target.local.env` with a fail-loudly placeholder check;
  - clean-working-tree check;
  - re-exec after pull, so a changed script runs its new version;
  - `GIT_SHA` exposed on `/health`;
  - writing `.env` for ad hoc `docker compose` commands.
- **An overlay VPN** on the host, desktop and phones, whose HTTPS front end provides a private
  HTTPS name for the host. Nothing is publicly exposed. Public or shared hosting (cPanel) never holds real
  data.
- **Container hardening:** non-root, the data volume as the only writable mount, secrets from a
  mounted file, a restart policy.
- **Backups:** the in-app job runs after each committed Import and nightly. Backups are client-side
  encrypted (restic or age) and sent to **cloud storage via rclone**, with a second copy on the desktop.
  Restore is tested as part of M1's Definition of Done.

## Consequences

- No registry quotas or tokens; deploying is one command; rollback is explicit.
- Builds happen on the host, which is slower than pulling an image but fine at this cadence.
- Key management matters: the backup key lives in the owner's password manager, findable by any other household member who may need to restore.

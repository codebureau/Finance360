# 0002. SQLite and exact money representation

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §5d, §5e)

## Context

One household, a small data volume, and episodic use. The NFRs ask for minimal moving parts,
trivial and encrypted backups, and **exact money arithmetic**. SQLite has no decimal type:
SQLAlchemy `Numeric` on SQLite silently stores floats.

## Decision

- **SQLite** in WAL mode, with a single writer process (the app, including its scheduled jobs).
- **SQLAlchemy 2 + Alembic** for the data layer and versioned migrations. Migrations run on startup,
  with an **automatic backup before each migration**. Postgres stays possible as an alternative
  backend, but isn't built or tested for now.
- **Money is integer minor units plus an ISO currency code**, via one shared custom column type and
  a domain `Money` value object. There are no bare amounts.
- **Unit quantities and prices are scaled integers or decimal strings**, with precision chosen per
  field (ETF prices need more than 2 decimal places).
- **Hypothesis property tests** enforce exact round-trips, and arithmetic rules such as rounding,
  done once, explicitly.
- **Encryption at rest comes from the host volume**, plus client-side-encrypted backups. SQLCipher
  only if a concrete threat later justifies its build friction.
- **Raw source documents are stored on the filesystem, content-addressed by hash**, beside the
  database, not as blobs.

## Consequences

- Backup is "copy one consistent file plus a documents folder," and encrypting and shipping it is
  trivial.
- Floating-point drift is eliminated by construction, but every money column must use the shared
  type. Review and tests must catch any bypass.
- Heavy concurrent writes are ruled out, which this scale never needs.

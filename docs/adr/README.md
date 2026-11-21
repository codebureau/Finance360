# Architecture Decision Records

Short, numbered records of decisions with real weight: new schema shapes, patterns, interfaces or
technology choices. Not every issue needs one. Routine, pattern-following work goes straight to a
Developer (see `AGENTS.md`).

ADRs 0001–0008 are **foundational**. They capture the technology and working-model decisions from
the elicitation sessions recorded in [`docs/requirements.md`](../requirements.md) (§5d, §5e), so
those decisions have a citable home. They predate the issue tracker.

## Format

`NNNN-kebab-title.md`: a four-digit sequential number and a kebab-case title.

```markdown
# NNNN. Title

**Status:** Proposed | Accepted | Superseded by NNNN
**Date:** YYYY-MM-DD
**Issue:** #N

## Context
What prompted this decision: the problem or constraint.

## Decision
What was decided.

## Consequences
What this makes easier, harder, or forecloses.
```

Keep it tight: enough to implement against without re-deriving the design, not a full spec.
Superseding is explicit: the new ADR names what it supersedes, and the old one's status is updated.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-python-backend-typescript-ui-single-image.md) | Python backend, TypeScript UI, single image | Accepted |
| [0002](0002-sqlite-and-exact-money-representation.md) | SQLite and exact money representation | Accepted |
| [0003](0003-canonical-data-layer-and-provider-plugins.md) | Canonical data layer and provider plug-ins | Accepted |
| [0004](0004-modular-monolith-plugin-registry-dependency-direction.md) | Modular monolith, plug-in registry, enforced dependency direction | Accepted |
| [0005](0005-ai-gateway-redaction-and-mcp.md) | AI gateway, redaction and MCP | Accepted |
| [0006](0006-pre-merge-review-slot-and-instance-isolation.md) | Pre-merge review slot and instance isolation | Accepted |
| [0007](0007-registry-free-tag-based-deployment.md) | Registry-free, tag-based deployment over an overlay VPN | Accepted |
| [0008](0008-agents-md-canonical-agent-instructions.md) | AGENTS.md as canonical agent instructions | Accepted |
| [0009](0009-core-schema-money-provenance-and-audit.md) | Core schema, Money, provenance and audit | Accepted |
| [0010](0010-single-user-authentication.md) | Single-user authentication: Sessions, argon2id, optional TOTP, auth seam | Accepted |
| [0011](0011-encrypted-backups-restic-rclone.md) | Encrypted backups: restic over rclone, local repo first | Accepted |
| [0012](0012-lan-https-sidecar-and-allowed-hosts.md) | LAN access with HTTPS: Caddy sidecar, allowed hosts, alongside an overlay VPN | Accepted |
| [0013](0013-m2-holdings-import-prices-and-live-data-migration.md) | M2 holdings, Import flow, prices and live-data-safe migration | Accepted |
| [0014](0014-frontend-ui-architecture.md) | Frontend UI architecture: routing, shell, page patterns, visual language | Accepted |
| [0015](0015-projection-engine-contract.md) | Projection engine contract | Proposed |
| [0016](0016-scenario-assumption-and-saved-run-storage.md) | Scenario, Assumption and saved projection run storage | Proposed |
| [0017](0017-income-streams-and-life-events.md) | Income streams and Life events | Accepted |
| [0018](0018-au-rule-pack-shape-and-versioning.md) | AU Rule pack shape and versioning | Accepted |
| [0019](0019-tax-capability-records-estimates-and-what-ifs.md) | Tax capability: record register, pure estimate engine, saved estimates and Tax what-ifs | Accepted |
| [0020](0020-whats-due-rules-interest-and-snoozes.md) | What's due: Due rules owned by each Capability, Effort levels, implicit interest, stored Snoozes | Proposed |
| [0021](0021-ask-panel-and-ai-gateway-first-slice.md) | The Ask panel and the AI gateway's first slice | Proposed |
| [0022](0022-captured-rule-figures-in-the-running-app.md) | Captured Rule figures: AI-guided capture in the running app | Accepted |
| [0023](0023-ask-levels-search-first-ai-optional.md) | Ask levels: search first, AI optional | Proposed |
| [0024](0024-document-intake-load-match-check-confirm.md) | Document intake: load, match, check, confirm | Proposed |
| [0025](0025-super-statement-record.md) | Super statement: an Account's year, recorded under Accounts | Accepted |
| [0026](0026-library-documents-links-and-prefill.md) | Library: one document store, generic links, and pre-fill beside the source | Proposed |
| [0027](0027-profile-authoring-through-claude-code-and-mcp.md) | Profile authoring through Claude Code and MCP: access tokens, a thin MCP server, proposed profiles | Proposed |
| [0028](0028-regular-deduction-from-super-line-debit.md) | Regular deduction from super: the Line debit | Proposed |
| [0029](0029-household-facts-and-age-pension-means-test-in-the-projection.md) | Household facts for the Age Pension, and how the means test reads the projection | Accepted |

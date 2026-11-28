# 0033. Notices: a small stored "what you added moved this" record, kept as Recent changes

**Status:** Accepted
**Date:** 2026-11-28
**Issue:** #102

## Context

Entering a Valuation gives nothing back (#102). The owner should see cause and effect: "You added a
Valuation for <Account>. Net position moved by $X (up)." plus a kept list on Home. ADR-0032 section 7
left a seam (a Question's Readiness can be computed before and after a write) and decided no store.
#102 needs one: a toast is gone in seconds, and Recent changes must survive a reload and a restore.

Constraints: the dependency direction (ADR-0004; Position sits below Questions' neighbours and cannot
import them), exact Money (ADR-0002), Readiness stays computed (ADR-0032), tolerant judgments
(AGENTS.md rule 3), and the backup already snapshots the whole SQLite file (ADR-0011).

## Decision

### 1. Vocabulary

A **Notice** is a short, stored, owner-facing record that one write moved the picture. **Recent
changes** is the list of Notices (a Home card, and a page). Both go in `CONTEXT.md`.

### 2. The store: one table, `notice`

Model in `core/models/notice.py` (all models live in core). Columns:

| Column | Meaning |
|---|---|
| `id` | primary key |
| `created_at` | UTC, naive like every timestamp here |
| `kind` | what was written, a short code: `valuation.added`, `valuation.corrected` (later `import.committed`, `super_statement.saved`) |
| `headline` | the sentence, built by code from `kind` and the Account's display name only: "You added a Valuation for <Account>." |
| `effect` | the unlocked line in words, nullable: "Net position moved by $1,200.00 (up)." and/or "Can we retire? went from Partly ready to Ready." |
| `delta_minor`, `delta_currency` | exact net-Position change as integer minor units plus currency (the Household's base currency); both null when only a Question changed |
| `link_path` | an in-app route (`/position`, never a full URL), chosen from a fixed set in code |
| `dismissed_at` | nullable |

No raw document content, no Account number, no provider reference, and no owner free text other than
the Account's display name. Headlines and effects are composed from code constants, that name and
Money, never from a note, a document or an identifier. A test asserts that a Notice for an Account
that has an identifier contains neither the identifier nor the provider.

A Notice is **not audited** (a derived convenience record, like an Answer rating, ADR-0032 section
6); the write's own audit entry records the change. It is **not a status history**: Readiness stays
computed (ADR-0032 section 2), and `effect` is words that are never read back as state.

### 3. Layering: model in core, producers in a new `notices` capability above Position

- `core/notices.py`: **pure**. `Picture` (net Money plus `{question key: Readiness}`), `diff(before,
  after)` and the Notice wording. No I/O.
- `capabilities/notices/service.py`: captures a `Picture` (net via `position.service.position_on`;
  Questions via `questions.service.evaluate`) and offers one wrapper per producer:
  `add_valuation(session, ctx, account_id, data)` and `correct_valuation(...)`. Each calls the
  existing Position valuation function, then records the Notice. The API's valuation routes call the
  wrappers instead of the Position functions.
- Position cannot import Questions (that inverts the layers) and Notices must see both, so Notices
  sits **above** them. import-linter: add `notices` as the top layer of the capability layers
  contract (`notices > projection > ...`). Only `api` imports it. No other contract changes, and core
  imports nothing above it.

This **amends ADR-0032 section 7**: its "no notices table" is superseded by this store; its "no
status history" stands. Section 7 gets a one-line pointer to this ADR.

### 4. The hook: same session, same transaction, never blocks the write

The wrapper (1) captures `before`, (2) calls the existing write, (3) flushes, (4) captures `after`
(the session sees its own uncommitted rows), (5) `diff`s and inserts the Notice. The request's single
commit makes the write and its Notice atomic: a failed write leaves no Notice. Steps 1, 4 and 5 run
in a savepoint: if capturing or recording raises, it is logged and the write proceeds with no Notice
(the same stance as Could not check). Cost is two Questions evaluations per write; acceptable for a
single owner and not optimised.

Position is read "on today" in the Household zone, so a back-dated Valuation that does not move
today's net makes no net line. That is correct.

### 5. What "moves the picture" means

A Notice is made when **either** holds:
- `abs(net after - net before) >= NOTICE_MIN_NET_CHANGE`, a named constant in `core/notices.py`,
  **100 minor units (one whole unit of the base currency)**, so a re-saved identical figure or
  rounding noise makes nothing; or
- at least one Question's Readiness differs between before and after (any direction, named by its
  wording).

Neither holds: no Notice, and the API reports none. The exact delta is stored; the tolerance only
decides whether to speak. `correct_valuation` already returns early when nothing changed; that path
makes no Notice.

### 6. Retention and dismissal

- Dismissal sets `dismissed_at`. Dismissed Notices stay on the Recent changes page (muted) and leave
  Home.
- Expiry: 30 days from `created_at` (constant). Expired rows are deleted lazily when the next Notice
  is written (no job); reads also filter them out, so a quiet instance never shows stale rows.
- Home shows the last **ten** undismissed, unexpired Notices, newest first.

### 7. API

- `GET /api/notices?include_dismissed=false&limit=10` returns `{items: [Notice]}`, newest first;
  `limit` max 50. A Notice is `{id, created_at, kind, headline, effect, delta: {minor, currency} |
  null, direction: "up" | "down" | null, link_path, dismissed}`; `direction` is derived from the sign.
- `POST /api/notices/{id}/dismiss` returns 204 and is idempotent. No bulk dismiss in this slice.
- The Valuation add and correct responses gain an additive nullable `notice` field (same shape), so
  the toast needs no polling and no race with a follow-up read. Regenerate the API client.
- Routes call the Notices service only. No MCP tool.

### 8. UI (shapes from ADR-0031)

- **Toast:** one line from `headline` and `effect`, an arrow and sign beside the delta (not colour
  alone), and a "Take a look" link to `link_path`. A toast is a transient surface, not a chart-kit
  shape.
- **Home card:** a `collection` in **list form only** (a Notice has no meaningful card grid, and the
  list leads with the headline, which is the understanding-first form). Each row: headline, effect,
  relative time, link, dismiss. Empty state: a quiet "Nothing has changed lately."
- **Page:** "Recent changes", the same list with dismissed rows muted.
- No new iconography (existing lucide arrow-up, arrow-down and `x`) and no new tokens. Add a
  one-paragraph pattern to `docs/ui-conventions.md`.

### 9. Backup

Nothing to build: the table lives in the SQLite file that ADR-0011 backs up whole. A test asserts the
migration creates it; the restore runbook needs no change. Restoring an older backup restores older
Notices, which is acceptable.

### 10. Later producers: a pattern, not a framework

One producer today. Each later write adds one wrapper in `capabilities/notices` that runs the same
capture, write, capture, diff, record steps with its own `kind` and headline template, and its API
route calls the wrapper. No registry and no discovery: a registry waits until a third producer would
justify it (AGENTS.md rule 5). Import commit and Super statement add their wrapper in their own
slices.

## Consequences

- Cause and effect is visible and survives reloads, at the cost of one table, one capability package
  and two Questions evaluations per Valuation write.
- Only owner-facing writes through the API get a Notice; a future direct call to the Position
  function (a job, MCP) gets none, by design.
- `notices` as the top capability layer means no other capability may ever import it.
- Notices are words plus one exact figure: they cannot be recomputed and are not a history of state.
- Forecloses email, push, digests, nags and a settings matrix (out of scope in #102), and reading
  Notices back as data for Readiness or the projection.

## Not decided here / routine follow-on

From #102's Outcome, left to the Developer (routine):
- Exact copy for headlines, effects, the empty state and the toast; a "Recent changes" section on the
  Guide's Home page (standing rule).
- The additive `notice` migration, the regenerated API client, and a "How to test" that adds a
  Valuation to the synthetic household (no Notices are pre-seeded, so the fixture needs no change).
- The toast component and its accessible live region; Home card placement; the `/recent-changes`
  route and its sidebar link.
- A Valuation **removal** Notice (the delete route returns 204 and the issue's first slice is
  "saving a Valuation").
- Tests: `diff` golden and property tests (tolerance edge, sign, no change returns none), the
  no-identifier assertion, atomic rollback, a failing capture not blocking the write, and expiry.

Explicitly not decided: the Import commit, Super statement, Tax record and Exposure profile lines
(follow-ups per #102); "a projection headline changed" (needs a cheap pure projection summary before
and after, designed with its own slice); any Notice about the Ask panel or Answer ratings.

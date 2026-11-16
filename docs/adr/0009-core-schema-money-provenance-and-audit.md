# 0009. Core schema, Money, provenance and audit

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** —

## Context

M1 slices S6 to S14 build the Household, Accounts, Valuations, FX rates, Position and
audit trail on one set of tables and value types. ADR-0002 fixed the principle (integer minor units
plus currency, no floats); ADR-0003 fixed canonical records with provenance; ADR-0004 fixed
placement rules. This ADR fixes the concrete shapes, so those slices don't re-derive them. It covers
M1 only and leaves room for M2 (derived Valuations, Imports, Holdings) without a rewrite.

## Decision

### 1. Money and numeric types (`core/`)

- **`Money`** is an immutable value object: `amount_minor: int` plus `currency: str` (ISO 4217,
  validated against a small static exponent table in `core`, e.g. AUD 2, JPY 0; unknown codes raise).
  Constructing from a `float` raises. The amount must fit a signed 64-bit integer.
- **Arithmetic:** `+`/`-` between different currencies raises. Multiplying by an integer is exact.
  There is no implicit conversion anywhere.
- **Column mapping:** every money amount is **two columns**, `<name>_minor INTEGER` and
  `<name>_ccy TEXT` (CHECK length 3), mapped to one `Money` attribute through one shared helper
  (SQLAlchemy `composite`). A CI test walks the metadata and fails on any `Numeric`/`Float` column.
- **`ScaledDecimal(scale)`** is a SQLAlchemy `TypeDecorator` over `INTEGER`, exposing
  `decimal.Decimal`. The scale is fixed per column. It rejects floats and rejects values with more
  precision than the scale (it never silently rounds on write). Used for FX rates now; for Instrument
  quantities and prices later, each with its own scale chosen when introduced.
- **Rounding lives in one module** (`core/money`), and nowhere else rounds:
  - mode is **round half to even**, applied **once** at the end of an operation, in integer
    arithmetic (no `float`, no premature `Decimal` quantising);
  - **FX conversion** (`Money` x rate -> `Money`): multiply, adjust for the two currencies' exponents,
    round once;
  - **allocation** (ownership splits, any "split N ways") uses the **largest-remainder method**,
    ties broken by ascending Member id, so parts always sum exactly to the whole;
  - the there-and-back FX error bound is derived and stated in the docstring (Developer proves it
    with Hypothesis).
- **Base currency in M1:** `AUD` is a constant in `core`, not a Household column. Position reports in
  AUD. An Account's currency defaults to AUD and may be overridden. Making Base currency
  configurable is a later change, not anticipated here.

### 2. Households, Members, Accounts, Ownership

- Plain tables with integer surrogate keys: `household` (one row), `member` (name, date of birth),
  `account`, `account_owner`, `tag`, `account_tag`, `category`, `account_type`.
- `account`: `category` and `account_type` references, `currency` (ISO), `opened_on` (nullable, null
  = open from the beginning), `closed_on` (nullable), notes. **Accounts are closed, never deleted.**
  A closed Account counts in a Position for date `d` iff `opened_on <= d` (when set) and
  `d < closed_on` (when set); `closed_on` is the first date it no longer counts. Its Valuations and
  history are kept. **Closing requires a zero balance:** the close action writes a final zero
  Valuation on `closed_on` (provenance "Account closed") and the closure in one audited transaction;
  a non-zero latest Valuation needs explicit confirmation (see Resolved questions).
- **Ownership:** `account.ownership` is `sole` or `joint`; `account_owner(account_id, member_id,
  share_bp)` holds the entries. Shares are **integer basis points** (10000 = 100%). **Joint is a mode
  flag, not a pseudo-Member**; the "Joint" Position view is "Accounts whose mode is joint". Rules,
  enforced in the service layer (SQLite cannot check a cross-row sum) and by a Hypothesis test:
  - sole: exactly one entry at 10000;
  - joint: at least two entries, each > 0, distinct Members, default an even split (50/50 = 5000/5000);
  - shares always sum to exactly 10000.
- A Member referenced by any `account_owner` row **cannot be hard-deleted** (the service blocks it).
- The Account type must belong to the Account's Category (service-validated).

### 3. Valuations and supersession

- `valuation`: `account_id`, `as_at` (date), amount (Money; its currency **must equal the Account's
  currency**), `source` (`manual | statement | derived | estimate`), `provenance_note` (required
  text for manual, statement and estimate), `import_id` (nullable integer, no FK until Imports
  exist), plus the supersession columns below.
- **Valuations are never updated or deleted.** A correction inserts a new row and marks the old one
  `superseded_at` / `superseded_by_id`. A removal is a supersession with no replacement. The earlier
  record stays retrievable. A partial unique index (`account_id, as_at, source` where
  `superseded_at IS NULL`) allows one current row per source per date.
- **Best available on or before `d`:** among current rows with `as_at <= d`, take the latest
  `as_at`; if several share it, take the highest-ranked source (`statement > manual > derived >
  estimate`). No row means "no Valuation", surfaced explicitly, never zero. It is a pure function in
  `core`. Observed and derived rows may coexist on a date, which is what later reconciliation needs.
- The same supersession columns (`recorded_at`, `superseded_at`, `superseded_by_id`) are a shared
  mixin, reused by `FxRate`. Account and Member edits are in-place updates; their history is the
  audit trail.

### 4. FxRate (canonical record, ADR-0003)

- `fx_rate`: `from_ccy`, `to_ccy`, `as_at`, `rate` (`ScaledDecimal`, scale 10), `source`
  (`manual` now; provider names later), `provenance_note`, supersession columns, and the same partial
  unique index per (`from_ccy, to_ccy, as_at, source`).
- **Direction:** `rate` is units of `to_ccy` per 1 unit of `from_ccy`. M1 stores foreign -> AUD.
- **Lookup:** the current rate for the **exact pair** with the latest `as_at <= d`. The result is
  either the rate (carrying its `as_at`, so staleness can be shown) or an explicit **`NoRate`**; never
  1.0, never zero. There is no maximum age in M1. Inverse pairs and triangulation are not derived
  (deferred to M2 with the automated feeds, along with cross-source precedence from ADR-0003).

### 5. Audit trail

- One append-only table `audit_event`: `id`, `occurred_at` (UTC), `entity_type`, `entity_id`,
  `action` (`create | update | delete | close | supersede`), `actor_kind` (`owner | import | agent |
  system`), `actor_ref`, `source` (e.g. `ui`, `api`, `fixture`, `import:<id>`), `before` and `after`
  (JSON snapshots, money as `{minor, ccy}`, never floats; null where not applicable), `summary`
  (readable one-liner). Migration adds triggers rejecting `UPDATE` and `DELETE` on it.
- **One shared writer** in `core` (`audit.record(...)`). Every mutating service function calls it
  **in the same transaction** as the change, so a change and its record commit or fail together.
  The caller passes an explicit `AuditContext` (actor, source) down from the API layer.
- **Rejected: an automatic SQLAlchemy flush listener.** It would catch bypasses, but it can't carry
  actor, source or a readable summary, and it entangles bulk operations (fixture loads,
  migrations). A guard test in the audit-trail slice covers bypass instead.
- Seeding the catalogue and migrations are **not** audited (system bootstrap, not a change to
  household data); the catalogue version is recorded instead (see below).

### 6. Seed data

- `category` (`key`, `name`, `class` = `asset | liability`) and `account_type` (`key`, `name`,
  `category`, `behaviour_key`) are ordinary rows with **stable string keys** and a `seeded` flag.
- An **idempotent startup seeder** inserts rows whose key is missing and **never overwrites an
  existing row**, so user edits survive upgrades. It records `catalogue_version` (an integer constant
  in code, bumped when the catalogue changes) in a small `meta` table. A catalogue change to an
  existing seeded row is therefore a deliberate migration, not an automatic push. The seeder, not a
  migration, is the single path for the initial seed, so fresh instances and upgrades behave alike.
- `behaviour_key` is a plain string resolved against the Behaviour registry (`behaviours/`, ADR-0004).
  `core` doesn't import `behaviours/`; a startup check and a test verify every `behaviour_key`
  resolves. M1 registers only `simple_balance`.

### 7. Placement (ADR-0004)

- **`core/`:** `Money`, `ScaledDecimal`, rounding, column helpers, all table models (including
  `FxRate`), the supersession mixin, the audit writer, the pure best-Valuation selector and the pure
  `convert(Money, rate)`.
- **`capabilities/household/`** (new Capability, the identity domain): the mutating services for
  Household and Member (each calling the audit writer). Table models stay in `core/`.
  - Shaped on the party model (people are first-class entities, groupings are relationships,
    Accounts link to people through ownership roles), but with the CONTEXT.md names. **Member is
    first-class**: it owns its facts (date of birth now; preservation age and pension eligibility
    later, derived or stated) and its own scope (a per-Member slice), and contributes to the
    Household. `Person`/`Party` are not identifiers.
  - Thin: no organisation parties and no generic relationship graph. A non-household party (trust,
    SMSF) is a possible future extension, **not built now**.
- **`capabilities/position/`:** the mutating services for Account, ownership (`account_owner`,
  referencing Member), Tag and Valuation (each calling the audit writer), plus the pure Position
  calculation. Position depends on `household`; `household` never imports `position`.
- **`capabilities/reference/`:** the `FxRate` service and the "latest on or before" lookup.
- `api/` calls only these services.
- **Dependency direction holds:** `position` -> `household` -> `core` and `reference` -> `core`; no
  capability imports `plugins`, and `core` imports nothing above it. Capability-to-capability imports
  are permitted only in this one direction, so import-linter gets a layers contract
  (`position` above `household`); the "Member referenced by ownership can't be deleted" rule is
  therefore checked by a guard that `position` registers with `household` (or the delete service
  asks a `core` query), not by `household` importing `position`.

## Resolved questions (the maintainer, 2026-10-16)

1. `closed_on` stays half-open, but **closed means a zero balance**: closing records a final zero
   Valuation (source `manual`, provenance note "Account closed") dated `closed_on`, in the same
   audited transaction. If the latest Valuation before closing is non-zero, the service requires
   explicit confirmation (e.g. a balance moved elsewhere by a super rollover). Totals never silently
   drop money.
2. The latest `as_at` beats source rank; rank only breaks same-date ties.
3. Half-even rounding accepted.
4. Seeding is not audited; only `catalogue_version` is recorded.
5. Household and Member live in `capabilities/household/`, as above.

## Consequences

- Money drift is impossible by construction, and a CI metadata test makes bypass visible.
- Corrections never lose data: Valuations and FxRates keep their history in-table, the rest in the
  audit trail. Cost: queries must filter `superseded_at IS NULL` (a Developer-owned query helper).
- Joint as a mode flag keeps Position's Member, Joint and Household views simple, and leaves room
  for non-50/50 joint splits since shares are stored anyway.
- Half-to-even rounding and largest-remainder allocation make hand-calculated golden answers
  deterministic; exact ties are the only place a reader might expect half-up.
- Forecloses: floats anywhere, hard-deleting Accounts, Valuations or FxRates, per-Household Base
  currency in M1, and implicit inverse or triangulated FX.
- Adding the Imports table (M2) means a foreign key on `valuation.import_id`, a migration, not a
  redesign.

## Not decided here / routine follow-on

- Exact column names, indexes, `Date` vs text storage, the Alembic baseline and ORM mapping details.
- Exact ISO 4217 table contents, and the proven FX round-trip bound (S6 states and tests it).
- API shapes, validation messages, UI, and the fixture contents (S7 to S14).
- The `AuditContext` plumbing and the audit read API (S14); the per-table `summary` wording.
- The query helper that excludes superseded rows.
- Dated loan terms, redraw, Holdings, Transactions, Imports and Coverage tables (M2 or later).
- Configurable Base currency, triangulated FX, FX provider precedence.
- Creating the `capability:household` GitHub label and the import-linter contract for
  `capabilities/household`; the AGENTS.md label list is updated in this PR. Retagging the affected issues is for
  Product Manager.
- Member facts beyond date of birth (preservation age, pension eligibility): later, with the Rule
  packs.
- No new UI concept or iconography; views are the existing Position screens.

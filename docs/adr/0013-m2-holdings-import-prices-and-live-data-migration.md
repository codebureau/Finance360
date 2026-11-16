# 0013. M2 holdings, Import flow, prices and live-data-safe migration

**Status:** Accepted
**Date:** 2026-10-19
**Issue:** — (epic: M2 Investment platform import)

## Context

M2 derives an investment platform Account's value and history from imported Transactions and daily
Prices. ADR-0003 fixed the idempotency model (file hashing, Coverage per Stream with supersession,
staged diff, cross-Stream reconciliation, fees once); ADR-0009 fixed Money, `ScaledDecimal`,
Valuations, FX and audit and left room for Holdings and Imports; ADR-0004 fixed placement. This ADR
implements those, not redesigns them.

**New constraint: a deployment now holds real household data.** Existing Accounts,
Valuations, FX rates and audit rows must survive every redeploy untouched.

## Decision

### 1. Live-data rule for all M2 schema changes

- **Additive only.** M2 migrations may `CREATE TABLE`, `CREATE INDEX`, `ADD COLUMN` (nullable or with
  a default) and insert seed rows. They may not drop, rename, retype or rebuild (Alembic batch mode)
  any existing table: `household`, `member`, `account*`, `valuation`, `fx_rate`, `audit_event`,
  `category`, `account_type`, auth and backup tables.
- **Any exception needs an explicit data migration plus a test that upgrades a populated database**
  and asserts every pre-existing row is byte-identical afterwards. The existing pre-migration online
  backup (`core/migrate.py`) and nightly Backup (ADR-0011) remain the safety net, not the plan.
- **A CI test for this ADR:** build a database at the previous head from the synthetic household,
  upgrade to head, assert row counts and content of the pre-existing tables are unchanged and the
  audit triggers still hold. Each M2 migration slice extends it.
- **No FK is added to `valuation.import_id`** (ADR-0009 anticipated one). SQLite would need a table
  rebuild of an append-only, trigger-guarded table with live rows. It stays a plain integer, checked
  in the service layer. M2 persists no derived Valuations (section 3), so it stays unused.
- **`fx_rate`, `valuation` and `audit_event` need no schema change.** `fx_rate.source` and
  `audit_event.entity_type` are free text; `actor_kind='import'` already exists.

### 2. Schema (all new tables, `core/models/`; supersession mixin where stated)

Numeric types: quantity `ScaledDecimal(6)`; unit Price `ScaledDecimal(6)` plus an ISO currency
column (a unit price is not minor-unit Money: ETF prices have 4 d.p. and unlisted unit prices more);
all cash and fee amounts are `Money` (two columns). No floats, no `Numeric`.

- **`instrument`**: `id`, `name`, `kind` (`etf | share | fund`), `currency` (holding currency),
  `notes`. Never deleted. Created or edited by the owner or through a confirmed Import resolution,
  audited. No seed rows.
- **`instrument_identifier`** (the crosswalk): `instrument_id`, `scheme` (`ticker | yahoo | isin |
  vendor_id | name`), `value`, unique on (`scheme`, `value`) after normalisation (casefold,
  collapsed whitespace). Product-name aliases are `scheme='name'` rows: one table, no separate alias
  table. One Instrument has many identifiers; an identifier maps to exactly one Instrument.
- **`price`** (supersession mixin): `instrument_id`, `as_at`, `value` (`ScaledDecimal(6)`), `ccy`
  (must equal the Instrument's currency), `source` (`yfinance | manual`, extensible),
  `provenance_note` (required; for providers, the symbol used). Unique current row per (instrument,
  `as_at`, source). Lookup mirrors FX: latest `as_at <= d`, ties broken by configured source
  precedence with `manual` first; result is the Price carrying its `as_at`, or explicit `NoPrice`.
- **`account_identifier`** (section 5a) and **`import_source`** (section 5b).
- **`import`**: `id`, `import_source_id`, `stream` (`trade | cash`), `file_sha256`,
  `file_name` (display only), `content` (the raw file as a BLOB, so it is inspectable, re-parsable
  and covered by the existing DB Backup with no new path), `coverage_from`, `coverage_to` (this *is*
  the Coverage: source × Account × Stream × range, no separate table), `status` (`staged | committed
  | discarded`), `added_at`, `committed_at`. Unique (`file_sha256`, `import_source_id`, `stream`) among
  non-discarded rows: re-adding the same file returns the existing Import and changes nothing.
  An Import whose records are all superseded is "superseded" by derivation (no current rows), not a
  status.
- **`staged_row`**: `import_id`, `row_no`, `record` (JSON of the canonical record, money as
  `{minor, ccy}`, quantities as decimal strings), `instrument_ref` (raw identifier as read),
  `instrument_id` (null until resolved). Kept after commit or discard for inspection.
- **`transaction`** (supersession mixin): `account_id`, `stream`, `import_id` (FK, **nullable** for
  manual Opening-position rows), `row_no`, `occurred_on`, `kind`, `instrument_id` (nullable),
  `quantity` (signed; buy positive, sell negative; trade Stream), `unit_price` + `unit_price_ccy`,
  `fee` (Money, trade Stream), `amount` (Money, signed, the cash effect; cash Stream). No uniqueness
  constraint: identical same-day rows are legitimate (requirements §5a).
  - Canonical `kind` (ADR-0003: adding one is an ADR matter): trade Stream `buy | sell |
    opening_balance`; cash Stream `deposit | withdrawal | trade_settlement | distribution | interest |
    fee | opening_balance`. Importers map into exactly these.
- **`fetch_run`**: one row per provider run per dataset (`prices | fx`): started, finished, outcome,
  per-Instrument or per-currency error text. Mirrors `backup_run`.
- **Holdings are not stored.** Units of an Instrument on `d` = sum of current trade `quantity` with
  `occurred_on <= d`; cash on `d` = sum of current cash `amount`. A stored Holdings table would need
  invalidating on every superseding Import; the derivation is small and trivially correct. (A
  Hypothesis property: Holdings never go negative given a valid history; section 6 handles the
  invalid case.)
- **Fees recorded once, by construction:** the trade row's `fee` is informational and the input to
  reconciliation and later cost base; **only the cash Stream's `fee` row moves cash.** Valuation
  never adds a trade `fee` to cash.

### 3. The holdings-based Behaviour and the Position

- `behaviours/holdings_based.py` exports a **pure** function, no I/O:
  `derive_value(account_currency, transactions, prices, fx, on) -> DerivedValue | NoDerivedValue`.
  - value = **sum over Instruments of (units on `on` × Price on or before `on`), converted to the
    Account currency by the exact-pair FX rate (`core.fx`), rounded once per Holding line half-even
    (`core.money`), plus the cash balance**. Lines are returned, so the Account screen can show
    units, price (with its `as_at`), FX rate and cash; the lines sum exactly to the total.
  - It returns `NoDerivedValue(reason)` and never a partial total when: a held Instrument has no
    Price, an FX rate is missing, or **either Stream lacks Coverage reaching `on`** (a trade-only or
    cash-only Account shows Holdings but no derived value, and says which Stream is missing or
    stale). A Price older than a configurable staleness (default 7 days) is shown as stale, not
    refused.
- **Derived values are computed, not stored.** For a history date `d` the Position service calls
  the Behaviour and adds a `ValuationPoint(as_at=d, source='derived')` to that Account's
  `valuations`. **`compute_position` / `compute_history` and `best_on_or_before` are unchanged**;
  manual-Valuation Accounts are unaffected because they never receive derived points. The Behaviour
  registry entry gains the optional derive hook; `core` still does not import `behaviours/`.
  Rejected: materialising derived Valuations. Daily price moves and every superseding Import would
  churn append-only rows and audit for data that is a pure function of its inputs.
- **Reconciliation (observed vs derived)** is the difference between the best observed Valuation and
  the derived value on a date, computed on read.

### 4. Switching a live *simple balance* investment platform Account to holdings-based

- **The Account type is what selects the Behaviour** (ADR-0009 section 6). The seeder never
  overwrites an existing row, so the seeded *Brokerage account* type stays *simple balance*. M2
  seeds a **new** Account type, `brokerage_holdings` ("Brokerage account (holdings)", Investments,
  `holdings_based`), by bumping `catalogue_version`. Switching an Account is the existing audited
  Account edit that changes its type. It is **explicit, per Account and reversible**.
- **Nothing is rewritten.** Manual Valuations and history stay. On the existing "best on or
  before" rule, **the latest `as_at` wins, and rank only breaks same-date ties**: a derived point on
  `d` beats a manual Valuation dated earlier; a `manual` or `statement` Valuation dated exactly `d`
  beats the derived one (`statement > manual > derived`), and the difference is shown as
  reconciliation. Before the first Transaction there is no derived point, so earlier manual history
  still draws. Switching back leaves all Transactions and Prices inert and restores the manual-only
  series.
- An Account with the holdings-based type and no imported Streams behaves exactly like *simple
  balance*: manual Valuations continue to work until the first Import is committed.

### 5. Import flow (`capabilities/imports/`, the investment platform Importer in its own package under `plugins/importers/`)

1. **Add file** (API → service `stage(bytes, import_source_id?)`): SHA-256 the bytes. An existing
   non-discarded Import with the same (hash, Import source, Stream) is returned as a no-op. With no
   Import source given, the Importer is detected and the Account resolved (section 5a); the Import
   source is created on confirmation.
2. **Detect and parse:** the service asks registered Importers to claim the file. The Importer
   returns a `ParsedFile`: Stream, Coverage (the export's stated period, else min to max row date),
   canonical rows, warnings. Parsing the platform's file is deterministic; the Importer reads Stream-
   specific quirks (descending `dd-MMM-yyyy` dates, signed vs unsigned amounts, name-only
   distribution rows).
3. **Stage:** store the Import (`staged`) and `staged_row`s. Resolve each `instrument_ref` through the
   crosswalk. **Unresolved references block commit** until the owner maps them to an existing
   Instrument or creates one (the mapping adds an `instrument_identifier`, audited, then rows
   re-resolve).
4. **Diff** against the current Transactions of the same Account and Stream inside Coverage, as a
   **multiset comparison** on the canonical fields (no row IDs exist): added, removed, unchanged.
   "Changed" is shown as removed plus added. Warnings appended to the diff: **cross-Stream
   reconciliation** (each trade's value and fee against the other Stream's current rows by date and
   amount, with the fee matched once), and **Opening-position detection** (section 7). Warnings do
   not block commit (the other Stream may not be imported yet); the owner confirms with them in view.
5. **Commit** (one DB transaction): insert the Transactions, set `superseded_at` / `superseded_by_id`
   on current rows of the same Import source and Stream with `occurred_on` inside this Coverage, mark the
   Import `committed`, and write **one audit event per commit** (`entity_type='import'`, actor
   `import`, source `import:<id>`, `after` = counts of added, removed, unchanged and warnings).
   Row-level history is the superseded rows themselves, linked by `import_id`. A partially overlapped
   earlier Import keeps its rows outside the overlap. **Discard** only marks the Import discarded.
6. **Idempotency:** same file → no-op (hash). Overlapping later file → supersession within Coverage
   (a fresh cash export touches only the cash Stream). Re-importing an older, narrower file after a
   broader one is an overlap like any other and shows as removed rows in its diff; the owner decides.

### 5a. Account identifiers

- **`account_identifier`**: `account_id` (FK), `kind` (`bsb_account | last4 | member_number |
  policy_number | vendor_account_id`, extensible), `value`, `institution` (nullable). Unique on
  (`kind`, `value`, `institution`) among current rows (supersession mixin, so a change supersedes).
  Optional reference data: an Account needs none.
- **Sensitive.** These are real values and live only in the instance DB (inside the volume and the
  encrypted Backups). Never in the repo, fixtures (synthetic placeholders only), logs or error text.
  The API and UI return them **masked** (`••• 1234`) by default, with an explicit reveal call. **Audit
  snapshots store the masked value.** Importers' errors and warnings never echo them.
- **Use in matching:** an Importer emits the file's external account reference on the `ParsedFile`.
  The service resolves it through `account_identifier` to an Account. No match, or more than one, and
  the owner picks the Account in staging; the choice can add the identifier (audited) for next time.

### 5b. Import source

- **Term: Import source** (CONTEXT.md). Three layers: the **Importer** (pure plug-in; declares a
  config schema, plus its Streams); the **Import source** (data); the **Import** (one added file).
- **`import_source`**: `account_id`, `importer_key`, `config` (JSON), `created_at`; unique on
  (`account_id`, `importer_key`). Created at first confirmed Import and reused. The service validates
  `config` against the Importer's declared schema (JSON Schema) on write; the Importer receives it
  as an input argument and hard-codes nothing per Account, so one Importer serves many Accounts.
  The investment platform Importer's schema is empty. A generic bank CSV Importer declares column mapping, date format and
  sign convention; its mapping UI is out of scope for M2. A saved Import profile (CONTEXT.md) is what
  later populates `config`.
- **Coverage and supersession, consistent with ADR-0003:** ADR-0003's "source × Account" *is* the
  Import source, so Coverage is **Import source × Stream × range**. An Import supersedes earlier
  current rows of the same Import source and Stream within its range. Two Import sources on one
  Account (say a CSV and a PDF) never supersede each other. Reconciliation still crosses Streams
  within the Account.

**Plug-in rules.** The Importer protocol and registry are defined in `capabilities/imports/`; the
plug-in imports it and registers at startup from the composition root (`main.py`), so `api/` and
`jobs/` never import `plugins` (existing import-linter contract). An Importer is a **pure function
from bytes to canonical records**: no session, no DB, no network, no filesystem, no audit, no
Instrument creation. Only the service writes. It receives its Import source `config` as an argument. A contract test feeds it synthetic fixtures with
no DB.

**Placement.** Table models in `core/`. `capabilities/imports/` (Import, staging, diff, commit,
reconciliation, Opening-position check) and `capabilities/reference/` (extended with Instrument,
crosswalk, Price and provider services). `imports` may import `reference` (new layers contract
`imports > reference`; `reference` still imports only `core`). `position` reads Transactions and
Prices through `core` queries and the Behaviour hook, as it reads FX today; it does not import
`imports`.

### 6. Reference-data providers (`plugins/providers/`)

- Protocols in `capabilities/reference/`; `yfinance` (prices) and `rba` (FX) register at startup. A
  provider returns canonical rows (`Price` / `FxRate` candidates); the **reference service writes**
  them with provenance (source, `recorded_at` as fetch time, note).
- **Fetching runs in `jobs/`**, which calls only the reference service (which looks up the provider
  registry). The existing single-loop scheduler gains **one more daily job** (default 18:30
  Household local time, with a start-up catch-up when the last success is over 24 hours old), not a
  framework. "Fetch now" is the same service call, run from the UI after a commit that introduced
  new Instruments.
- **Prices:** per Instrument, using its `yahoo` identifier. First run backfills from the earliest
  current trade date of that Instrument to today; later runs fetch from the day after the latest
  stored provider Price. **Raw `Close`, not adjusted close**: distributions arrive through the cash
  Stream, so adjusting would double count. Closes are immutable once stored; a revised provider
  value supersedes. A returned currency that differs from the Instrument's is rejected, not
  converted.
- **Failures and limits:** requests are sequential with a short delay, one range request per
  Instrument; on throttling or error, back off once, record the error in `fetch_run`, and stop that
  Instrument until the next run. **Nothing is fabricated or forward-filled in storage;** gaps surface
  through `as_at` and the stale flag. A fetch failure never blocks the app, and the UI shows the last
  success and last error. Unofficial-API breakage is expected (ADR-0003): swapping the provider is
  one plug-in plus a precedence setting.
- **Manual override:** `source='manual'` Prices (required note) and manual FX rates remain available,
  win same-date ties over any provider, and are the only route for Instruments no provider covers
  (unlisted funds).
- **FX (RBA):** M2 fetches only currencies actually used by an Account or Instrument. RBA publishes
  foreign units per 1 AUD; ADR-0009 forbids implicit inverses, so the plug-in **inverts once at
  ingestion** (round half-even to scale 10) to the stored foreign → AUD direction and records the
  published figure in `provenance_note`. Source `rba`. Frankfurter is a later fallback in the
  precedence list.
- **How fetched FX coexists with manual FX:** existing manual rows are never touched, superseded or
  deleted by a feed. Lookup keeps the ADR-0009 shape: exact pair, **latest `as_at <= d`, same-date
  ties broken by precedence with `manual` first**. Consequence, stated plainly: once the feed is
  on, a foreign-currency Position on `d` may use a newer provider rate than the older manual one
  it used before, so those figures can change, with no data lost. Position lines already show the
  rate's `as_at`; M2 adds its source.

### 7. Opening-position detection (decision level)

- The Importer never guesses. After staging, the service replays current trade quantities (and cash)
  by date including the staged rows and **flags any date on which an Instrument's Holding, or the
  cash balance, would go negative** as "possible missing Opening position", naming the Instrument and
  date. A negative derived Holding or cash is never silently shown as a value.
- The remedy is an explicit **Opening position**: dated Transactions of kind `opening_balance`
  (`import_id` null, manual, audited), dated before the first imported row. They sit outside any
  Coverage, so imports never supersede them. If a later Import's Coverage reaches back past an
  Opening position's date, staging warns of probable double counting. The entry UI and exact
  wording are routine follow-on.

### 8. Deferred (unchanged from the epic)

Cost base, CGT, performance, corporate actions (splits, consolidations, DRP: provider Closes are
split-adjusted while imported units are not, so a split needs a Transaction kind and an ADR), and
recognising transfers between Household Accounts.

## Resolved questions (the maintainer, 2026-10-19)

1. Switching is a per-Account change to the new `brokerage_holdings` Account type, not a flip of the
   seeded type.
2. Once an FX feed is on, a newer provider rate beats an older manual one; Position shows the
   rate's source.
3. With one Stream only, no derived value is shown and the missing Stream is flagged.
4. A Price is stale after 7 days (shown, not refused).

## Consequences

- Position, history and the pure calculation are untouched; holdings-based accounts are just
  Accounts whose valuation points come from a pure Behaviour function.
- Live data is safe by rule and test, not by care: no existing table is rebuilt, no destructive step
  exists, and switching is per Account and reversible.
- Computing derived values per history date reads all Transactions and Prices for the Account each
  time. At household scale this is trivial; if history gets slow, persisting derived Valuations is a
  later additive change.
- Storing the raw file in SQLite grows the DB by kilobytes per Import and rides the existing Backup.
- Forecloses: stored Holdings, a Valuation FK to `import`, partial or zero-filled derived values,
  adjusted-close prices, implicit inverse FX, Importers that write.
- Adds two seams the epic's later milestones reuse: the Importer registry (M5 Super) and the
  provider registry (CPI, M4 composition).

## Not decided here / routine follow-on

- Account identifier and Import source management UI, masking/reveal API, JSON Schema validation library choice.
- Exact column names, indexes, Alembic revisions, ORM mapping details and the populated-DB upgrade test harness.
- The investment platform column mapping and parser details, from the requirements' description and synthetic fixtures only.
- API shapes, staged-diff and reconciliation UI, Instrument and crosswalk management UI, Opening-position entry UI, Holdings display on the Account screen, Stream stale/missing indicators. No new iconography is anticipated; any new status badges follow the existing design language.
- Import-linter contracts for `imports`, `reference` and `plugins/importers|providers`.
- The fetch job's settings (time, staleness days) and `fetch_run` read API.
- Extending `fixtures/sample_household/` with an Instrument, a synthetic trade and cash export pair, and Prices (standing rule).
- A catalogue-version bump seeding `brokerage_holdings`, and the documented switching step in the review/deploy notes.

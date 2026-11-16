# 0016. Scenario, Assumption and saved projection run storage

**Status:** Proposed
**Date:** 2026-10-25
**Issue:** — (epic: M3 First projection)

## Context

ADR-0015 made the engine a pure function over a fully resolved, versioned, serialisable
`ProjectionInput`, and left to this ADR: where Assumptions and Scenarios live, how they layer, where
the Drawdown policy lives, and what a saved run records. The B1 request (`ProjectionRequest`:
inflation, returns by Category, retirement ages by Member, annual spending, annual saving, horizon
age) is already the shape of a resolved Assumption bundle; B3 turns its fields into stored
Assumptions. M3's Definition of Done requires saved runs to be reproducible. ADR-0009 fixes the
audit approach (explicit writer, same transaction, seeding not audited) and ADR-0013 the live-data
migration rules (additive only).

## Decision

### 1. Tables (`core/models`, additive migration, schema only)

- **`scenario`**: `id`, `key` (nullable, unique; `best` / `likely` / `worst` for seeded rows),
  `name` (unique), `notes`, `drawdown_order` (nullable JSON text, see section 3), `created_at`.
- **`assumption`**: one override per row.
  - `id`, `scenario_id` (nullable FK; **null is the Household default layer**), `key`
    (Assumption key, e.g. `inflation`, `return_rate`, `retirement_age`), `scope_type`
    (`household | category | member`), `scope_ref` (text: `""`, a Category key, or a Member id).
  - The value is in exactly one of three typed columns, chosen by the key's registered kind (a
    CHECK enforces exactly one non-null): `value_rate_ppm INTEGER` (a `Rate`, ADR-0015 section 4,
    parts per million, so no float and no `Numeric`), `value_int INTEGER` (ages, whole years), or
    the Money composite (`value_minor` / `value_ccy`, ADR-0009).
  - Unique on `(scenario_id, key, scope_type, scope_ref)`, as two partial unique indexes (SQLite
    treats nulls as distinct).
- **Assumption keys are a code registry**, as `behaviour_key` is (ADR-0009 section 6): each key
  declares its value kind and the scope types it allows. The service validates against it. M3 keys
  are the B1 request fields: `inflation` (household), `return_rate` (category), `retirement_age`
  (member), `annual_spending`, `annual_saving`, `horizon_age` (household). A new key is code, not a
  migration.
- **`account` scope is reserved, not stored in M3.** The registry rejects it until an input version
  that carries Account-level lines exists (ADR-0015 section 8, simplification 4). `member` scope is
  added now because `retirement_age` needs it; Income stream and Life event keys are A3's.

### 2. Layering and resolution

- **Precedence, most specific first:** (1) the Scenario's own override at the line's most specific
  scope, (2) the Scenario's override at a wider scope, (3) the Household default at the most
  specific scope, (4) the Household default at a wider scope. In short: *what the Scenario states
  beats anything it does not*, then the more specific scope wins. Values are absolute, never deltas.
- A key unresolved after all layers is a validation error naming the key (no silent zero).
- **Resolution is a pure function** `resolve(defaults, overrides) -> ResolvedAssumptions` over plain
  tuples (`capabilities/projection/assumptions.py`), so it is unit-testable without a database. The
  service loads rows, calls it, then builds the `ProjectionInput` as B1 does. The pure resolver
  imports no `core.db` or `core.models` (extend ADR-0015's forbidden contract to it). Resolution
  happens **before** the engine; the engine never sees a Scenario.
- The resolver also returns a **provenance list**: for each value used, its key, scope, value and
  winning layer (Scenario or Household default). It explains a figure; it is not an engine input.
- **Cloning** a Scenario copies its override rows and drawdown order. There is no parent link and no
  Scenario inheriting from another: one overlay on the Household default keeps resolution to one
  rule.

### 3. Drawdown policy

- It is `scenario.drawdown_order`: a JSON text list of Category keys, or null. Null means the
  default order, still the code constant from ADR-0015 section 8 (there is no stored Household
  default order yet).
- It overrides **as a whole list**, never element by element. The service validates it: unique
  keys, each a projected Category. It resolves into `ProjectionInput.drawdown_order` unchanged.
- A JSON column, not a child table: it is always read and written whole, and is not money. Editing
  UI is B9.

### 4. Saved projection run (`projection_run`)

One row per explicit "Save run". Nothing is saved implicitly.

| Column | Content |
|---|---|
| `id`, `created_at` (UTC), `label` (nullable text) | identity |
| `scenario_id` (FK, RESTRICT), `scenario_name` | the Scenario run, and its name at the time |
| `engine_version`, `input_version` | integers (ADR-0015 section 3) |
| `rule_pack_version` | nullable text. Null until A4, which owns its form |
| `input_json` | canonical JSON of the resolved `ProjectionInput` (ADR-0015 section 2) |
| `result_json` | canonical JSON of the `ProjectionResult` |
| `sustainable_json` | nullable canonical JSON of the sustainable-spending output |
| `provenance_json` | the resolver's provenance list, plus Position-flagged Accounts |
| `funds_last_to_age`, `lasts_beyond_horizon` | denormalised headline, for list views only |

- **Exactness:** all JSON uses ADR-0015's canonical serialisation: money `{minor, ccy}`, rates as
  decimal strings, no floats (a float is rejected on write). `result_json` needs a canonical
  serialiser in `projection` with the same round-trip guarantee as the input. JSON columns carry
  `CHECK (json_valid(...))`. No Money or Rate lives in a run's relational columns.
- **The snapshot is the source of truth.** Reproducing a run never reads live Assumptions, the
  Position or prices.
- **Reproducibility contract:** `project(from_json(input_json))` must equal the stored result
  whenever `engine_version == ENGINE_VERSION`. When it differs, the stored result is shown as
  computed by engine vN and the UI offers "re-run with the current engine", which saves a **new**
  run. Only the current engine is kept (ADR-0015).
- **Immutable:** migration triggers reject `UPDATE` and `DELETE` on `projection_run`, as for
  `audit_event`. The Scenario FK is RESTRICT, not SET NULL (that would be an UPDATE), and the
  service blocks deleting a Scenario that has runs. A Scenario with no runs may be deleted; a
  seeded one may not.
- **Size (rough estimate, B4 to verify against real output).** `ProjectionResult` has one row per
  year and, per line, five Money figures (opening, growth, credited, withdrawn, closing) at about
  40 bytes each as `{minor, ccy}` JSON. A typical household is about 2 Members x 4 Categories = 8
  lines over about 50 to 60 years, so roughly 450 line-years x 5 x 40 bytes, or about 90 KB, plus
  year rows and ages: **about 100 to 150 KB per run**. The input is a few KB. Hundreds of runs are
  therefore tens of MB (300 runs is about 30 to 45 MB), well within SQLite's comfort. Listing never
  reads the payloads (denormalised headline columns), and restic deduplication keeps backups
  cheap. Compression is not needed now.
- **Cleanup is expected later.** An explicit, audited cleanup or retention facility will probably
  be wanted; its design (and how it coexists with the immutability triggers) is left to a future
  ADR. Immutability stays as above until then.
- A run snapshots no Assumption rows, only what they resolved to. The input is complete and
  smaller, and provenance says where each value came from.

### 5. Audit

- **Scenario and Assumption changes** (create, update, delete of a Scenario, an override or a
  drawdown order) go through `audit.record` in the same transaction (ADR-0009 section 5; entity
  types `scenario`, `assumption`). `before` / `after` carry the value as `{minor, ccy}`, a percent
  string for a Rate, or an int. Assumptions update in place; the audit trail is their history, as
  for Accounts. Deleting an override means "inherit again".
- **A saved run is audited as `create` only** (header fields, not the payload). It has no updates
  by construction.
- **Seeding is not audited** (ADR-0009 section 6).

### 6. Seeding

- Tables come from an Alembic migration with **no data**. Seeded rows come from an **idempotent
  startup seeder**, the same pattern as the catalogue: insert rows whose key is missing, never
  overwrite, and record `assumption_seed_version` in `meta`. Fresh instances and upgrades behave
  alike; user edits survive.
- **Seeds:**
  - Household default rows for `inflation`, `return_rate` per projected Category, and
    `horizon_age`, as illustrative placeholders, labelled as such in the UI.
  - `Likely`: no overrides (it is the Household default).
  - `Best` and `Worst`: absolute `return_rate` overrides per Category (and `inflation` for Worst).
    **Principle, following normal financial-modelling practice:** Likely is the median, and Best
    and Worst are roughly the 90th and 10th percentile of long-horizon annualised returns for the
    Category. B3 sets the actual numbers, with a cited source recorded beside the constants. They
    are ordinary editable Assumptions, which is the configurability story; nothing about them is
    special-cased.
  - Spending, saving and retirement ages are **not** seeded (they are the household's own);
    resolution fails with a clear message until set.
- Seeded Scenarios have `key` set; they can be edited but not deleted. Because overrides are
  absolute (no deltas), changing a Household default does not move a Scenario that overrides that
  key. This is accepted, with a **requirement: the change must be flagged.** When a Household
  default Assumption is changed, the user is shown which Scenarios override that key (and so will
  not move) and is prompted to consider updating them. This is a service and UI concern, not
  schema: the data to answer it is already the override rows.
- **Assumptions that vary over time or with Life events** (for example more conservative returns
  after retirement) are **out of scope here and handed to A3.** Every Assumption in this ADR is
  one value for the whole horizon.
- **Fixture:** seeds arrive through the seeder, so `fixtures/sample_household/` needs no run data.
  It gains Household default values for spending, saving and retirement ages (invented), and one
  custom Scenario with a few overrides and a drawdown order. No pre-saved runs (they would carry a
  stale engine version); "How to test" saves one.

## Consequences

- A Scenario is a thin overlay and the resolver is one small pure function. Adding a key or scope
  later is code, not a schema change.
- Saved runs are self-contained and replayable. The cost is full JSON per run, which is explicit
  and bounded by the user choosing to save.
- Immutability makes retention a later problem: no run deletion until a future ADR designs an
  audited cleanup facility.
- Forecloses: floats or `Numeric` in these tables; Scenario inheriting from Scenario; the engine
  or input module reading Assumptions; runs that reference live Assumptions; editing or deleting a
  run; per-element drawdown overrides.

## Out of scope

Account override UI and Account-scope storage, Monte Carlo schema, Knowledge-derived inputs, Income
streams and Life events, and time-varying Assumptions (A3, which also adds their keys), run
cleanup or retention (a future ADR), Rule pack shape and the content of
`rule_pack_version` (A4), a stored Household default drawdown order.

## Not decided here / routine follow-on (B3 to B5)

- Exact column and index names, the Alembic migration with its triggers and CHECKs, ORM mapping.
- The assumption-key registry module, Scenario and Assumption CRUD services with audit and
  validation, the import-linter contract extension, `ProjectionRequest` becoming the resolver's
  output.
- The seeder, its constants (the percentile-based rates from section 6, with a cited source), and
  the fixture additions above.
- Flagging Scenario overrides when a Household default changes (section 6): service query and UI
  prompt.
- The `ProjectionResult` canonical serialiser and its round-trip property test; the reproducibility
  test (save, reload, re-project, compare), with a mutation proof since this is invariant work.
- Save-run service and API, runs list and run detail, "re-run with current engine"; Scenario
  switcher, Assumption editing and Best/Likely/Worst comparison screens (ADR-0014 patterns; **no new
  iconography or design-language additions**); API client regeneration.
- Property tests: resolution precedence, an unset key always errors, a Scenario clone resolves
  equal to its source.

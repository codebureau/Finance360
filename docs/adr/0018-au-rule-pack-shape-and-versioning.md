# 0018. AU Rule pack shape and versioning

**Status:** Accepted
**Date:** 2026-10-26
**Amended by ADR-0022:** section 6 ("a code release") now covers only the shipped baseline; audited in-app captures layer on top.
**Issue:** — (epic: M3 First projection)

## Context

ADR-0004 named `rulepacks/` ("versioned by date") and said the engine takes a Rule pack version as
an input. ADR-0015 forbade the engine from reading a Rule pack and left the pack to this ADR.
ADR-0016 left `projection_run.rule_pack_version` null. Requirements make a saved run record its Rule
pack version, and make that part of M3's Definition of Done. ADR-0017 needs preservation age,
Superannuation Guarantee and contributions tax from the pack (B6, B7). The Age Pension (B10 to B12)
needs rates, thresholds and the pension age.

The pack must also be able to hold tax tables later (marginal rates, Medicare levy, the
Medicare levy surcharge, HELP, super caps), but tax is not built now.

## Decision

### 1. What a Rule pack is

A Rule pack is **versioned data plus a few pure functions**. It holds no algorithm for a benefit or
a tax.

- **Contract and registry live in `core/rulepack.py`** (pure types, no I/O). `rulepacks/au/` imports
  the contract; it imports no capability, `core.db` or `core.models`. `main.py` registers the pack
  at startup, as for Importers and providers (ADR-0013 section 5). Services look a pack up by
  jurisdiction key and never import `rulepacks/`. Core still imports nothing from `rulepacks/`.
- A pack declares:
  - `pack_id` (`au`), `jurisdiction`, and `revision` (an integer);
  - a **parameter schema**: for each key, its value kind and unit, its carry-forward policy
    (section 3) and its source;
  - **years**: for each financial year, a mapping of key to value;
  - **age functions**: `preservation_age(born_on)` and `age_pension_age(born_on)`, each a pure
    function from a date of birth to an age (years and months).
- **A financial year (FY)** runs 1 July to 30 June and is keyed by the year it ends in (FY 2026-27 is
  `2027`).
- **Value kinds:** `money` (with a unit: `year` or `fortnight`), `rate` (the `Rate` of ADR-0015, in
  parts per million), `int`, and `brackets`. Published fortnightly figures are stored as published
  and converted by the service. A taper such as "50 cents in the dollar" is a `rate`; "$3 per
  fortnight per $1,000" is also a `rate` (0.3%). **`brackets`** (an ordered list of threshold and
  `rate`) is declared in the contract so marginal tax tables fit, but **no code builds it in M3**. No
  float, no `Numeric`, no new numeric type.
- **Adding tax later** is new keys, one new value kind (`brackets`), and a tax capability that
  consumes them. Versioning, carry-forward, recording and overrides do not change. That is the
  test of this shape.
- **The calculations are not in the pack.** The Age Pension means test is pure engine-side code in
  `capabilities/projection/`, parametrised by the values the pack supplies (ADR-0015: the engine
  never reads a pack). A different jurisdiction's benefit brings its own calculation.
- The household uses `au` as a constant in the service. Choosing a pack per Member or per Category
  (for example a foreign jurisdiction's) is not decided here.

### 2. Selecting rules per projection year

- Projection year `t` uses the FY **containing its start date**. A year that straddles 1 July uses
  the FY it starts in (a stated simplification).
- Each FY entry holds the values **in force at 1 July** of that FY. Age Pension payment rates change
  on 20 March and 20 September, so the **current** FY entry is refreshed in place, with a revision
  bump, when a new indexation lands. Later years carry forward.
- **Age functions are evaluated against the pack revision, not an FY.** They are law keyed by date
  of birth. A Scenario may override them (section 5).

### 3. Carry-forward beyond the last known year

Each parameter declares a policy, because Australian parameters are indexed differently:

- **`cpi`:** indexed to prices. In the real-terms engine (ADR-0015) it is **constant in real
  terms**, so the last known value is simply repeated.
- **`frozen`:** fixed in nominal dollars (for example historically frozen tax thresholds). Each year
  after the last known FY its real value falls by the run's `inflation` Assumption, in one `scale`
  per year from the last known value. Rounding is not compounded.
- A `wages` policy (indexed to wage growth) is expected, needs the `real_wage_growth` Assumption
  (ADR-0017), and is added when a parameter needs it. It is not built now.

Materialising is a pure function in `core/rulepack.py`:
`materialise(pack, first_fy, years, inflation, overrides) -> rules by year`, returning a **step
function**: a new span only where a value changes. A `cpi`-only pack therefore yields one span.

### 4. Reproducibility and `rule_pack_version`

**The saved input is self-sufficient, so a run never depends on the pack.**

- **Values the service consumes** (ages, SG rate, contributions tax) are already baked into the
  `ProjectionInput` as year indices and amounts (ADR-0017).
- **Values the engine consumes** (the Age Pension parameters, from B11) enter `ProjectionInput` as a
  **materialised, per-span rules block**. This is a new field, so it is the first `INPUT_VERSION`
  bump, with a pure upcaster. The same B11 change that adds the step logic bumps `ENGINE_VERSION`.
  The block holds integers, `Money` and `Rate` only.
- Reproducing a run is therefore `project(from_json(input_json))`, exactly as ADR-0016 requires, with
  no pack involved. **Only the current pack is kept**, as only the current engine is: an old run is
  explained by its stored input, result and version label.
- **Every rule value used is recorded in `provenance_json`** (key, FY, value, and whether it came
  from the pack or a Scenario override), including the baked ones, so a figure can be traced to a
  published number.

**`projection_run.rule_pack_version` is a text label:**

```
<pack_id>/<last known FY label>/r<revision>        e.g.  au/2026-27/r1
```

- `last known FY` is the latest FY the pack has published figures for. It tells a reader how far
  figures are actual and where carry-forward starts.
- `revision` is an integer the pack author bumps whenever **any parameter value, schema or age
  function result changes**, including a correction. It is not a date.
- **Enforcement, as for `ENGINE_VERSION`:** a test pins `(revision, content hash)`. The hash is a
  SHA-256 of the pack's canonical JSON (parameter schema and years; the age functions are covered by
  golden tests). Changing data without bumping `revision` fails the test.
- `NULL` means the run was saved before packs existed (B1 to B5 runs) and recorded no pack.
  Every run saved from B10 onward has a value.
- Scenario overrides are not part of the label. They are Assumptions, they resolve into the input,
  and they are listed in provenance.

### 5. Scenario overrides: a hook only

- A pack parameter or age function can be overridden by an Assumption whose key is
  `rule.<pack key>` (for example `rule.age_pension_age`), at `household` scope, with the value kind
  the pack declares. It reuses ADR-0016's tables and layering (a Scenario state beats the Household
  default) with no new table. The assumption-key registry (ADR-0016 section 1) admits `rule.`-prefixed
  keys from the registered pack's schema.
- An override replaces the value **for every projection year**, in today's dollars, and is applied to
  the pack data before `materialise` and before `life_events.resolve` (ADR-0017).
- **M3 builds the hook and its resolver test, with no UI and no seed.** Nothing in B10 to B12 depends
  on an override existing.

### 6. Where the data comes from, and how a yearly update lands

- **A code release.** Pack data is typed Python in `rulepacks/au/`, one module per FY, each parameter
  with its source (document, page and retrieval date) beside the constant. Parsing happens at import
  from decimal strings, as for `Rate`.
- A yearly update is: a new FY module (or a refreshed current one), a `revision` bump, the pinned
  hash updated, golden tests updated, and a PR. It is routine work and not another ADR.
- **Rejected: a reference-data provider** that fetches published rates. Services Australia and the
  ATO publish no stable machine-readable feed, scraping is brittle, and data that moves outside a
  release would break the revision discipline above and make golden tests unreliable. This can be
  revisited if a stable feed appears; the contract does not prevent it.
- **Golden examples (cited for B11):** Services Australia's published Age Pension rates and worked
  examples, and the DSS Social Security Guide pension-rate calculators for the assets and income
  tests. B11 records the exact pages and retrieval dates beside the golden cases. They are public
  figures, not household data.

## Consequences

- A pack update never alters a saved run, and never needs old pack versions to stay runnable.
- The engine stays free of pack reads, and the pack stays free of algorithms. The cost is that rules
  are copied into each saved input, a handful of integers per span.
- A new jurisdiction is a new registered pack plus its own calculations.
- Tax tables, a `wages` policy and a Household-level jurisdiction choice are additions, not rewrites.
- Forecloses: the engine or capabilities importing `rulepacks/`; rule data in the database or a
  provider; benefit or tax algorithms in the pack; keeping old pack revisions runnable; mid-year rule
  changes in a projection year; a float in pack data.

## Not decided here / routine follow-on

**B10, AU Rule pack v1: preservation age and Age Pension age, versioned.** Sequencing (decided): B10 lands **before B6**:
- `core/rulepack.py` (contract, registry, `materialise`, the content hash) with unit tests, including
  both carry-forward policies and the step-function property (a `cpi`-only pack gives one span).
- `rulepacks/au/` with the two age functions (preservation age by date of birth; Age Pension age 67
  from 1 January 1957 with the phased earlier ages) and the unrestricted super age 65, each cited.
  Golden tests at the transition dates.
- Registration in `main.py`; the import-linter contracts: `rulepacks` imports only `core`;
  `capabilities` and `api` do not import `rulepacks`; `projection.engine` and `projection.input` do
  not import `core.rulepack`.
- `rule_pack_version` written on save, the pinned revision-and-hash test, and the `rule.` override
  hook with its resolver test.
- Rule values shown in provenance on the run detail screen. **No new iconography.**

**B7 adds to the pack, as data:** `sg_rate` and `contributions_tax_rate` for the years it needs, with
sources, and a revision bump.

**B11, simplified Age Pension calculation:**
- The parameter set in `rulepacks/au/` (maximum basic and supplement rates for single and couple, the
  income-free areas and taper, the assets-test thresholds for homeowner and non-homeowner and the
  taper, the deeming rates and thresholds) with the carry-forward policy per parameter and a source
  for each.
- The pure calculation in `capabilities/projection/`, golden-tested against the cited examples, with
  property tests (the payment never exceeds the maximum rate; it never rises as assets or income
  rise).
- It lands with the `INPUT_VERSION` and `ENGINE_VERSION` bumps and the upcaster when the rules block
  is added (B12 if B11 stays calculation-only).

**B12, Age Pension as a calculated Income stream:** the rules block in `ProjectionInput`, the engine
step, the service's materialisation, the result lines and the screen, and the sample household's
couple status and homeownership facts if not already present.

## Out of scope

Tax calculation and tax tables (the Tax planning epic), foreign Rule pack content, Monte Carlo, the Age Pension algorithm
itself (B11), the Scenario-override UI, choosing a pack per Member, a `wages` carry-forward policy.

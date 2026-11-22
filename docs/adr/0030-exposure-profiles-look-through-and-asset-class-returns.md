# 0030. Exposure profiles, look-through lines and asset-class returns

**Status:** Accepted
**Date:** 2026-11-22
**Issue:** #82 (epic #5, M4 Look-through analytics; unblocks #83 to #88, #6 and #89)

## Context

ADR-0003 names `ExposureProfile` as a canonical record and nothing more. ADR-0013 gave Instruments,
Prices and the derived value of a holdings-based Account (Holdings x Price x FX, plus cash, as lines
that sum exactly). ADR-0025 records each Super statement's Investment options as free-text names with
stated values, and explicitly designs no look-through. ADR-0015 and ADR-0016 fixed a pure engine over
one resolved `return_rate` per Member and Category line, and Assumptions keyed by a code registry with
`household | category | member` scopes. `INPUT_VERSION` is 3, `ENGINE_VERSION` is 3 and
`ASSUMPTION_SEED_VERSION` is 2 today.

M4 needs: how a profile is stored and sourced, what a super option attaches to, fund-of-funds, how
remainders reconcile to the Position, a glide path by Member age, where a Target allocation lives, and
how per-asset-class returns reach the engine.

**Principle (maintainer, 2026-11-22):** look-through is for understanding risk, not for reconciling
every cent of real-world data. **Representation and arithmetic stay exact**: weights are integer
parts per million, Money is minor units, and every Dimension adds up to the Account value through an
explicit residual line (AGENTS.md rule 3 is unchanged). **Validation and judgments are tolerant**,
with each tolerance a named constant, so rounded published data does not turn into busywork for the
owner.

## Decision

### 1. Placement and boundaries

- **Tables** in `core/models/exposure.py`. **Writes** go through one reference service,
  `capabilities/reference/exposure_profiles.py`, as Prices do: manual entry, a later Provider, an AI
  proposal and the factsheet Fill target (#90) all call the same `record_profile(candidate)`.
- **A new Capability `capabilities/lookthrough/`**: the option mapping (section 4), the pure resolver
  `resolve.py` (section 5), and `service.py` (lines, roll-ups, gap).
- **import-linter:** the Capability layers become `projection > tax | lookthrough > position > ...`
  (`tax` and `lookthrough` independent). `lookthrough.resolve` joins a forbidden contract like the
  engine's: no `core.db`, `core.models`, `core.audit`, service or other Capability.

### 2. Dimensions and Exposure labels are data

- **`exposure_dimension`** (`key`, `name`, `position`) and **`exposure_label`** (`dimension_key`,
  `key`, `name`, `position`; unique on the pair). Seeded by an idempotent startup seeder (insert
  missing keys, never overwrite, `exposure_vocab_seed_version` in `meta`), as for the catalogue. No
  owner-editing screen in M4; a new label is a seed change, not code or a migration.
- **Seed shapes** (labels are snake_case keys with display names):
  - `asset_class`: the broad classes as Australian funds publish them, with Australian and
    international shares separate (shares, property, infrastructure, fixed income, cash,
    alternatives), about eight;
  - `region`: about eight non-overlapping geographic groups;
  - `sector`: the eleven standard equity sectors;
  - `exposure_currency`: ISO 4217 codes for commonly held currencies.
  - Every Dimension has an `other` label: the manager's own published residual.
- #83 seeds `asset_class` only. #84 adds the other three by a seed-version bump. An unknown Dimension
  or label is rejected on write.

### 3. The Exposure profile record

- **`exposure_profile`** (`SupersessionMixin`; never updated or deleted, triggers enforce it):
  - `instrument_id`;
  - `as_at`: the date the published composition is as at;
  - `age_from`: nullable whole years, a glide-path band (section 7);
  - provenance: `source` (`manual`, a registered Provider key, or `ai`, meaning AI-proposed and
    owner-confirmed), `retrieved_on` (date), and `provenance_note` (required, as for Prices).
- **Children, immutable** (a correction writes new ones, as Super statement options do):
  - `exposure_weight` (`profile_id`, `dimension_key`, `label_key`, `weight_ppm`);
  - `exposure_reference` (`profile_id`, `instrument_id`, `weight_ppm`) (section 5).
- **Weights are exact:** a `Weight` value in `core`: integer parts per million, as `Rate` and the
  Work-use share are. Entered as a percent string with at most 4 decimal places; finer precision is
  rejected, a float raises. Each weight is greater than 0 and at most 100%. There are no negative
  weights: a geared or short position is entered net.
- **Sums are judged with a tolerance; arithmetic stays exact.** Published figures round, so a
  factsheet's weights often total 99.8% or 100.2%. `WEIGHT_TOLERANCE_PPM = 10_000` (1 percentage
  point), a named constant in `lookthrough`.
  - Weights are **stored exactly as entered**. The rules below apply when the profile is resolved,
    and the panel and profile page state them. Nothing is normalised.
  - A Dimension's total is its direct weights plus all reference weights. A profile may omit a
    Dimension entirely (a bond fund with no sectors).
  - **Within tolerance, short:** the shortfall goes to **Not classified** in that Dimension.
  - **Within tolerance, over:** the excess is trimmed from the Dimension's **largest direct weight**
    (ties go to the earliest label in seed order). If the Dimension has no direct weights, it is
    trimmed from the largest reference.
  - **Why trim the largest:** every line stays non-negative, so `allocate` and the property tests
    hold. Only one figure changes, by at most 1 point, where that is the smallest relative change.
    The rule is deterministic, and the panel names the label and the amount. A negative residual
    line would be just as exact, but a negative exposure misleads more than a trimmed one.
  - **Outside tolerance:** rejected, with the difference shown.
  - Make the tolerance a Household setting only if real use shows the need.
- **Current and selection:** one current profile per `(instrument_id, as_at, age_from)`. A write at
  the same key supersedes, and the earlier one stays retrievable. Look-through on a date uses the
  latest `as_at` on or before it. If there is none, it uses the earliest later one, marked "profile
  dated after", rather than showing No profile: a fund's mix changes slowly. A Provider never
  supersedes a `manual` profile; it is skipped and reported.
- **Audit:** every write is audited (entity `exposure_profile`). Seeding is not.

### 4. What a profile attaches to: always an Instrument

- **A super Investment option is mapped, per Account, to an Instrument** (kind `fund`, currency the
  Account's, typically created from the option's name in the mapping step). The profile attaches to
  that Instrument like any other.
- **`investment_option_link`:** `account_id`, `option_name` (as entered) and `option_name_norm`
  (case- and space-folded, unique with `account_id`), and `instrument_id`. Edited in place and
  audited. Super statement lines resolve through it by normalised name, so a later statement with the
  same name needs no new mapping.
- **Why an Instrument, not a profile owned by the option:**
  - one attachment point means one record shape, one resolver and one write path;
  - a fund-of-funds can reference an option, and the #90 factsheet Importer targets Instruments either
    way;
  - two Members' Accounts in the same lifecycle option map to the same Instrument and share one glide
    path.
- Per-Account mapping, not a shared option catalogue: the same option name at two funds never
  collides. ADR-0025 is unchanged.

### 5. Fund-of-funds

- **A reference** is a weighted pointer to an **Instrument**, not to a profile version. It resolves to
  that Instrument's profile on the look-through date, so an updated underlying flows through.
- **Mixing is allowed.** A diversified fund may hold underlying funds plus a direct cash sleeve.
  - References apply to every Dimension.
  - Direct weights for a Dimension then sum to 100% minus the references' total, within the
    section 3 tolerance.
  - A profile whose references total 100% has no direct weights.
- **The resolver is pure:**

  ```python
  resolve(amount: Money, instrument_id, dimension, on, age, profiles) -> tuple[ExposureLine, ...]
  ```

  - `profiles` is a plain mapping, preloaded.
  - At each level it splits `amount` with `core.money.allocate` (largest remainder) across the
    references and the direct share, then the direct share across its labels. Every level sums
    exactly, and no part is negative.
  - Each line carries the path of Instruments it came through, for "show underlying" and traceable
    figures.
- **Guards:**
  - *Cycle:* rejected on write, by a check over the graph of current references, with a message
    naming the loop. Defensively, a cycle met at read becomes a **No profile** line marked "cycle".
  - *Depth cap:* `MAX_DEPTH = 5` levels of references. A deeper write is rejected. At read, a branch
    beyond the cap becomes **No profile** marked "nested too deep".
  - *Missing:* an underlying Instrument with no profile on the date becomes **No profile** naming
    that Instrument. The total is unaffected.

### 6. Look-through lines and reconciliation

An Account's look-through starts from its **parts** and must sum, in every Dimension, to the
Account's value in the Position.

- **Parts sources**, a small ordered registry in `lookthrough`. An Account no source applies to is
  **Not analysed**.
  - `holdings`: an Account whose Behaviour derives a value (ADR-0013). The parts are each Holding
    (at its Price x quantity, through FX) plus the Account's cash.
  - `super_statement`: a `super_fund_account` Account. The parts are the option values on its latest
    current Super statement on or before the date. With no statement the Account is Not analysed,
    with a "provide a Super statement" prompt.
- **Explicit lines, never absorbed.** Each Dimension view is its labels plus these:
  - **No profile:** a part whose Instrument (or unmapped option) has no profile on the date, or a
    branch from section 5. It links to entry or mapping.
  - **Not classified:** the part of a profiled value that has no weights in this Dimension, plus a
    shortfall within tolerance (section 3). The Account's cash is asset class `cash` and exposure
    currency its own currency, and is Not classified for region and sector.
  - **Unallocated:** the Account's Position value minus its parts. For a holdings-based Account it is
    non-zero only when a newer recorded Valuation outranks the derived one. For super it is typically
    the gap between the 30 June option values and a later Valuation. Option values are **never
    scaled** to the current value.
- **Over-allocation:** parts above the Account value give a **negative Unallocated** line. The parts
  keep their stated values, and totals still reconcile exactly.
- **The line is always shown. Only a material gap is flagged.**
  - `RECONCILE_TOLERANCE`: the larger of 1% of the Account value and A$50. A gap of either sign
    within it is shown quietly (muted, no warning). Beyond it, the gap is flagged.
  - **A stale option mix** (the Account's Valuation is dated after its Super statement) is judged
    against `STALE_TOLERANCE`, 15% of the Account value, since a year's returns and contributions
    explain such a gap. The line is labelled "change since <statement date>". Beyond 15% it is
    flagged as "the option mix may have changed".
  - Both tolerances are named constants in `lookthrough`.
- **Currency:** lines are worked in the Account's currency and converted to the Base currency by
  allocating the Account's Base value across them. They sum to the Position figure exactly.
  Percentages are display only, rounded once from Money.

### 7. Glide path (#6)

- A **glide path** is an Instrument's set of current profiles at one `as_at`, each with an `age_from`.
  - A band runs from its `age_from` to the next band's; the last band is open-ended.
  - The set is written and superseded as a whole.
  - Banded and unbanded profiles never coexist at one `as_at`.
- The resolver's `age` is the owning Member's age in whole years on the look-through date (the
  ADR-0015 rule).
- **Out of band:** an age below the first band uses the first band, and the line says so. With
  open-ended last bands, nothing falls above.
- **A glide path on a joint Account is not resolved.** It becomes **No profile**, "a glide path needs
  one owning Member". Rare, and stated rather than guessed.

### 8. Roll-ups (#87)

- **Levels:** Account, Category, Member and Household, per Dimension. Not analysed Accounts are
  listed with their values, so the Household total equals the Position.
- **Joint Accounts are apportioned by ownership share, so both margins hold exactly:**
  - per label, the Members' amounts sum to the Account's;
  - per Member, the amounts sum to the Position's Member share of that Account.
  - Method: allocate a negative Unallocated line by share first. Then allocate each Member's
    remaining share across the remaining label amounts in turn, by largest remainder.
  - Property test: both margins hold, and nothing is negative except Unallocated.

### 9. Target allocation (#88): a Household-level record

- **`target_allocation`**: `dimension_key`, `label_key`, `weight_ppm`. One current set for the
  Household, replaced as a whole and audited (the audit trail is its history). The set sums to
  exactly 100%. This stays strict on purpose: it is the owner's own intent, not rounded published
  data, and the form shows the remainder as the owner types.
- **Asset class only in M4.** The service rejects other Dimensions; the column keeps the door open.
- **Not a Scenario Assumption and not per Scenario:** it describes the household's intent, not a
  projection input. Nothing in the projection reads it in M4.
- **Gap, exactly in Money:** target money is `allocate(analysed total, target weights)`, and gap =
  actual minus target per label. No profile, Not classified and Unallocated are shown as rows with no
  target, so the table reconciles.
- **The concentration threshold** is a nullable Household column with a visible default.

### 10. Asset-class returns in the projection (#89)

- **Assumption keys** (ADR-0016 registry, no migration; `scope_ref` holds the label key):
  - `asset_class_return`: kind `rate`, a new scope type `asset_class`.
  - `return_basis`: kind `choice` (`category` | `asset_class`), household scope.
- Layering is unchanged: a Scenario's value beats the Household default.
- `return_basis` defaults to `category` when unstated, so no existing Scenario moves (the
  `tax_in_projection` precedent). The fixture sets `asset_class`.
- **The mix is resolved before the engine. The engine never sees asset classes.**
  - For each line (Member x Category), `lookthrough` supplies the Member's apportioned asset-class
    Money from section 8.
  - A pure function in `projection` (under the forbidden contract) computes the line's rate:
    `round_div(sum(m_ac x r_ac) + m_fallback x r_category, total)`, one rounding per line-year.
  - `m_fallback` is the line's No profile, Not classified, Unallocated and Not analysed money, plus
    any asset class with no resolved `asset_class_return`. It takes the Category `return_rate`.
  - An over-allocated Account contributes its parts only, normalised to their total.
  - A zero line takes the Category rate.
- **Stated simplifications:**
  - each Account's share of a line, and each part's share of its Account, stay as at the start
    (constant mix, rebalanced yearly);
  - only a glide path moves, by the owning Member's age in each projection year. That is the
    de-risking #89 asks for.
- **Input contract:**
  - `Line.return_rate` becomes `Line.returns`: half-open year spans `(from_year, until_year, Rate)`
    covering `[0, years)` with no gap. Consecutive equal years are merged.
  - **`INPUT_VERSION` 3 to 4**, with an upcaster that turns a v3 rate into one full-horizon span.
  - **`ENGINE_VERSION` stays 3** if the upcast golden tests are unchanged (the same input gives the
    same result). If any figure moves, bump it in the same PR (ADR-0015 section 3).
  - **`ASSUMPTION_SEED_VERSION` 2 to 3** for the seeded asset-class returns (Likely as the Household
    default, Best and Worst overrides, the ADR-0016 percentile principle with a cited source).
- **A saved run** snapshots the resolved spans in `input_json`, as before. `provenance_json` gains,
  per line:
  - the basis;
  - the starting asset-class mix (Money and weight);
  - the fallback amount and its reasons;
  - the profiles used (Instrument, `as_at`, band);
  - each glide-path band change by year.

  Reproducing a run never reads profiles.

### 11. Where tolerance applies, and where strictness stays

| Point | Rule |
|---|---|
| Profile weights per Dimension | within ±1 point: a shortfall goes to Not classified, an excess is trimmed from the largest weight. Beyond that, rejected (section 3) |
| Unallocated or over-allocation | always shown; flagged only beyond 1% of the Account value or A$50, whichever is larger (section 6) |
| Stale option mix | flagged only beyond 15% (section 6) |
| No profile on or before the date | use the earliest later profile, marked as such (section 3) |
| Glide-path age below the first band | use the nearest band, noted (section 7) |
| Super statement checks | already warn-and-tick (ADR-0025); unchanged |

Strictness stays where it costs the owner nothing, or where tolerance would hide a real error:

- **Target allocation sums to exactly 100%:** it is typed by the owner, not published.
- **Unknown labels are rejected:** the `other` label exists for anything unlisted.
- **Cycles and the depth cap** are structural errors.
- **4 decimal places of percent** is finer than any factsheet.

## Consequences

- One record shape, one resolver and one write path serve ETFs, super options, fund-of-funds and
  glide paths. A Provider or the #90 factsheet Importer is a new caller of `record_profile`, not a
  new shape.
- Every view adds up to the Position exactly, with remainders named rather than hidden. Only a gap
  beyond tolerance is flagged, so a factsheet is entered as printed. The costs are up to four
  explicit line kinds, which the panel must explain, and a trim note when a profile totals over
  100%.
- A super Account looks less analysed as its statement ages (a growing Unallocated line). That is
  honest, it is quiet up to 15%, and M5 imports narrow it.
- The engine stays pure and ignorant of look-through. It only learns that a rate can vary by year.
- Forecloses:
  - floats or `Numeric` for weights, and any silent normalisation;
  - negative weights;
  - profiles attached to anything but an Instrument;
  - references pinned to a profile version;
  - scaling option values to fit the Account;
  - asset classes inside the engine;
  - Target allocation as a Scenario Assumption.

## Not built in M4

- Security-level look-through (out of scope, not deferred).
- A Provider for profiles, AI extraction mechanics, and the factsheet Importer and Fill target
  (#90).
- History views of exposure, a Due rule for stale profiles, and owner-editable vocabularies.
- Sector hierarchies, user-defined Dimensions, and targets on other Dimensions or per Member.
- Glide paths on joint Accounts, Monte Carlo, correlations, and fee drag by option.

## Not decided here / routine follow-on

Every slice regenerates the API client, extends `fixtures/sample_household/` with invented values and
updates its Guide pages, as each issue lists. UI follows ADR-0014. **No new iconography:** the
Analytics nav entry uses a lucide icon. The explicit lines reuse the existing reconciliation styling
(as Unreconciled does).

- **#83:**
  - the migration (tables from sections 2 to 4, supersession triggers, CHECKs, partial unique index);
  - `Weight` with its Hypothesis round-trip test;
  - the vocabulary seeder with `asset_class` only;
  - `record_profile` with validation within `WEIGHT_TOLERANCE_PPM`, and audit;
  - the Instrument-page form, which shows the running total and the difference, and the profile
    history;
  - the `holdings` parts source;
  - the Account panel (asset class, No profile, Not classified, Unallocated), with the quiet-or-flagged
    rule and `RECONCILE_TOLERANCE`;
  - tests:
    - sums: a shortfall goes to Not classified, an excess trims the largest weight (including a tie),
      and a total beyond tolerance is rejected;
    - supersession;
    - the "profile dated after" fallback;
    - reconciliation to the Position, with a mutation proof.
  - **#83's acceptance criterion "weights must sum to 100%" now reads "within the tolerance".**
    Product Manager should update the issue.
- **#84:** the three Dimension seeds (seed-version bump), the switcher, and Exposure currency beside
  Holding currency. Unknown-label rejection tests.
- **#85:** `exposure_reference` entry on the form, the resolver's recursion and guards, the "show
  underlying" expansion, and property tests (conserved, non-negative, two-level nest, cycle
  rejected, an excess trimmed from the largest reference when there are no direct weights).
- **#86:** the `investment_option_link` table and the mapping step (pick or create a `fund`
  Instrument), the `super_statement` parts source, `STALE_TOLERANCE` and the "change since" label,
  the flag beyond tolerance (of either sign), and the "provide a Super statement" prompt.
  **#86's "an option mix above the Account total is flagged" now means beyond tolerance.**
- **#6:** banded profile entry (a whole set per `as_at`), age resolution with the out-of-band note,
  and the joint-Account refusal.
- **#87:** the roll-up service with exact two-margin apportionment (property test), the Analytics
  screen and level and Dimension switchers, the Not analysed list, and traceable figures.
- **#88:** the `target_allocation` table and form, the concentration threshold column, top
  exposures, and the gap table.
- **#89:**
  - the two Assumption keys and the `asset_class` scope;
  - the line-rate function;
  - `Line.returns` with the v3 upcaster;
  - golden tests proving v3 results are unchanged;
  - the seeds;
  - the run's provenance and its "basis used" note;
  - the Category-fallback statement on the run.

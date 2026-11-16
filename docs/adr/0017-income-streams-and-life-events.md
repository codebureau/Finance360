# 0017. Income streams and Life events

**Status:** Accepted
**Date:** 2026-10-26
**Issue:** — (epic: M3 First projection)

## Context

ADR-0015 made the engine a pure function over a fully resolved `ProjectionInput`, and left Income
streams and Life events to this ADR. ADR-0016 fixed Assumption layering and the saved run, and
handed time-varying Assumptions (spending phases) here. B1 used stand-ins that this ADR
replaces: super accessible "at retirement", saving and spending that switch on and off at the two
retirements, and nothing in between.

M3 builds only what B6 to B8 need: retirement per Member, super access, a salary Income stream with
super contributions, and spending phases. The Age Pension (B11, B12) and the Rule pack shape
(ADR-0018) are separate. Tax (the Tax planning epic), reversionary amounts and per-stream indexation (a backlog design item) are not
decided here, but nothing below may make them need a migration or an engine rewrite.

## Decision

### 1. The engine does not change

Everything below resolves in the service, **before** the engine, into fields `ProjectionInput`
already has: `inflows`, `outflows` and `Line.accessible_from_year`.

- **No `INPUT_VERSION` bump and no `ENGINE_VERSION` bump** come from this ADR. B6 to B8 change what
  the service puts in those fields, not what a field means. Saved runs and golden tests are
  untouched.
- The first bump in M3 comes with the Age Pension (ADR-0018), which needs new step logic.
- Growth, indexation and phases are expanded by the service into **one `Inflow` or `Outflow` per
  year** (half-open `[t, t+1)`). A stream over 60 years is at most 60 small entries, and the engine
  gains no growth logic. The same technique carries a nominal-frozen stream later: the service
  deflates it (ADR-0015 section 5).

### 2. Income streams are stored, but only the stored kind is a row

Two kinds of Income stream (CONTEXT.md sharpened):

- **Stored:** an amount the household states: salary now; business income, a defined-benefit
  pension or an annuity later. A row in `income_stream`.
- **Calculated:** an entitlement derived from the Rule pack, Member facts and the Position: the Age
  Pension, later a foreign State Pension. **Never a row.** The engine computes it per year (ADR-0018,
  B11 and B12). Its tax kind is `government`.

`income_stream` (additive migration; Money in typed columns per ADR-0009; every change audited with
entity type `income_stream`; rows are edited in place, the Audit trail is the history):

| Column | Meaning |
|---|---|
| `id`, `name`, `notes` | identity |
| `type_key` | a code registry key, like `behaviour_key` (ADR-0009 section 6). M3 registers `salary` only |
| `ownership` and `income_stream_owner(stream_id, member_id, share_bp)` | the same sole or joint shape as Account. **M3 allows sole only**; a joint stream splits with `allocate` later, with no migration |
| `gross_annual` (Money) | the annual cash amount **in today's dollars of the projection's `as_at`**, excluding employer super |
| `take_home_annual` (nullable Money) | what reaches the household after tax. **Required for `salary` in M3** and credited to the projection. Replaced by a calculation when the tax engine lands |
| `start_on`, `start_event`, `end_on`, `end_event` | nullable pairs, at most one of each pair set (CHECK). A date, or a Life event key. Both null on start means "now". `salary` defaults `end_event = 'retirement'` |
| `credit_category` | the Category whose line the stream credits. Default `cash` |

- **Tax-relevant kind is an attribute of the stream type**, held in the registry, not a stored
  column. M3 kinds: `employment`, `business`, `investment`, `government`. `salary` is `employment`.
  A column would duplicate what the type already says and let the two disagree. A tax capability
  reads the kind from the registry, so it needs no migration. Adding a kind or a type is
  code.
- **Indexation is also an attribute of the type.** `salary` indexes with the Assumption
  `real_wage_growth` (section 5). Per-scheme indexation (a backlog design item) will be a different registered
  indexation, not a new column.
- M3 **creates only `salary`**; the service rejects other types until a slice builds them.

### 3. Life events are derived dates, not stored rows

A **Life event** in M3 is a `(member_id, kind, date, source)` value computed in the service by a pure
function `life_events.resolve(members, retirement_ages, rule_pack)`:

| Kind | Source | Date |
|---|---|---|
| `retirement` | Scenario (the `retirement_age` Assumption, ADR-0016) | birthday at that age |
| `preservation_age` | Rule pack | birthday at `preservation_age(born_on)` |
| `unrestricted_super_age` | Rule pack (age 65) | birthday at that age |
| `age_pension_age` | Rule pack | `born_on` plus `age_pension_age(born_on)` (can fall at 66 years 6 months) |

- **Every Life event is a date, and one rule turns a date into a year index:** the first year whose
  start is on or after the date, never below 0. It reproduces B1's `retirement_age - age_now`
  exactly (year starts are whole years apart), so B1's golden answers hold.
  *Note (B6):* it matches for every date of birth except a 29 February birthday projected
  from 28 or 29 February, where a 366-day year can make it differ by one year either way. The rule
  as written here is authoritative.
- Life events appear in the screen's timeline and in `provenance_json` (with source and Rule pack
  version). They are **not** engine input: the engine sees the year indices they produce.
- Rule-fixed dates come from the pack's age functions (ADR-0018). A Scenario override of the rule
  (ADR-0018 hook) is applied to the pack **before** `resolve`.
- **Stored Life events** (mortgage paid off, inheritance, pension transfer, death of a Member) are
  not decided here. They wait for their first slice; the `start_event` and `end_event` columns
  accept their keys then.

### 4. Super accessibility and contributions

- **Access:** a Member's `superannuation` line is accessible from
  `min(max(retirement_year, preservation_year), unrestricted_year)`. That is: retired and past
  preservation age (the condition of release), or age 65. **Deliberately not "at preservation age"
  alone:** retiring at 55 with preservation age 60 then leaves a real gap funded from cash and
  Investments, which is the point of the model. Transition to retirement is not modelled.
- `overseas_pensions` stay accessible at the Member's retirement year (B1) until a Rule pack for the pension's jurisdiction
  exists.
- **Contributions from salary:** for each year a `salary` stream is in force, the service adds an
  `Inflow` to the owner's `superannuation` line of `gross × sg_rate × (1 - contributions_tax_rate)`,
  both rates from the Rule pack for that year's financial year, rounded once with `core.money.scale`.
  This is employer Superannuation Guarantee on top of the cash salary, net of the flat contributions
  tax. **Not modelled:** Division 293, concessional caps, the maximum contribution base, salary
  sacrifice, fund fees. The screen states each.

### 5. Cash flows, saving and the Drawdown policy

- **Salary credits `credit_category` (cash)** with `take_home_annual`, grown by
  `real_wage_growth`. It is credited in the engine's step 2, before step 3 withdrawals, so it funds
  spending first without any Drawdown policy change. A surplus stays in cash and earns the cash
  return. A calculated Age Pension works the same way. This is the requirements' "income streams →
  cash → investments → super" order, achieved by timing rather than by an entry in
  `drawdown_order`.
- **Interaction for B9 (decided):** a stream's `credit_category` must be in `drawdown_order`
  (`ProjectionInput` already rejects an inflow with no line). B9 requires `cash` in every accepted
  order, or the service must reject the order with a clear message. This ADR does not otherwise
  touch the Drawdown policy.
- **New Assumption key `real_wage_growth`:** a `rate`, scopes `household` and `member` (the resolver
  already falls back from a Member to the Household). It is **real**: 0% means pay keeps pace with
  inflation, matching the real-terms engine. The year-`k` amount is one `scale` from the stored
  base (rounding is not compounded year on year).
- **`annual_saving` keeps its B1 meaning for a Member with no salary stream.** For a Member who has
  one, saving is whatever salary leaves after spending, so `annual_saving` is not applied to that
  Member. B7 removes B1's equal split only for such Members.
- **Salary requires working-years spending (amended 2026-10-26).** "Whatever salary leaves after
  spending" needs a spending outflow in the salaried years, and B1 has none before the last
  retirement. Without one, the full take-home pay would be banked and every headline inflated. So
  when any Member has a salary stream in force in year 0, the resolved spending phases (section 6)
  must include a phase starting in year 0 (a `start` phase at offset 0). Otherwise resolution fails
  with a missing-value error naming working-years spending, exactly as an unset Assumption does
  (ADR-0016 section 2). It is never a silent zero.

### 6. Spending phases

Spending is a household need, not an entitlement, so it stays an Assumption and not an
Income-stream-like outflow.

`spending_phase` (additive migration; the same layering as `assumption`):

| Column | Meaning |
|---|---|
| `id`, `scenario_id` (nullable FK; **null is the Household default**) | as `assumption` |
| `anchor` | `start` (the projection start) or `last_retirement` (the year the last Member retires) |
| `offset_years` | whole years after the anchor, 0 or more |
| `annual_amount` (Money) | today's dollars |

- **A phase runs from its resolved start year until the next phase starts**, and the last runs to
  the horizon. Phases are sorted by resolved start year; when two resolve to the same year, the later
  anchor (`last_retirement` after `start`) wins and the other is empty. Each phase becomes one
  `Outflow`, so there is no engine change.
- **Anchoring to retirement is the point:** "active years then slower years" moves when a Scenario
  retires earlier or later. An absolute age would not.
- **A Scenario's phases replace the Household default phases as a whole list**, never a phase at a
  time (the precedent is `drawdown_order`, ADR-0016 section 3). A Scenario with no phase rows inherits
  the default list. The service validates: amounts above 0, no duplicate `(anchor, offset_years)` in
  one list.
- **Migration of `annual_spending`:** B8 converts each existing `annual_spending` Assumption row (any
  layer) into a `(last_retirement, 0)` phase row and retires the key. It is additive, small and
  tested, but it is a migration of live data, so it is invariant work (ADR rule: write the test that
  catches the defect, break the code once to prove it).
- **Nothing before the first phase.** A household with no `start` phase and no salary behaves as in
  B1 (no flows before the last retirement). With a salary, a year-0 phase is required (section 5).
- **Sustainable income (B2) with phases:** the bisection scales **every phase from `last_retirement`
  by one common factor**, holding their ratios. The headline is the first such phase's resulting
  amount. This keeps the search monotone and the answer meaningful.

### 7. Resolution, provenance and audit

- Resolution order, all in the service before the engine: Assumptions (ADR-0016) → Rule pack
  (ADR-0018) → `life_events.resolve` → year indices → `Line`, `Inflow`, `Outflow`. Each step is a pure
  function over plain values, importing no `core.db` or `core.models` (extend ADR-0015's forbidden
  contract to `life_events` and the phase expansion).
- `projection_run.provenance_json` gains, in the same list the resolver already writes: each Life
  event used (source, date), each stream used (id, type, owner, amounts as stored) and the phase list
  used. A saved run snapshots the **resolved input**, not the stream rows, so editing or deleting a
  stream later never changes a saved run.
- **Fixture:** `fixtures/sample_household/` gains invented salary streams and a two-phase spending
  default.

## Consequences

- The engine stays untouched through B6 to B8, and no saved run or golden answer moves.
- A tax capability can find every `employment`, `business`, `investment` and `government`
  stream from the registry without a migration.
- Per-year `Inflow` entries trade a slightly larger input for an unchanged engine. If a stream ever
  needs a fractional-year start, that is an input-version question for its own slice.
- Take-home is entered in M3, so changing the Rule pack never changes salary. The cost is that
  cash flow ignores tax until the tax engine replaces `take_home_annual` with a calculation.
- Forecloses: a stored `kind` column; Age Pension as a row; Life events as engine input; spending
  phases keyed by absolute age; per-phase Scenario overrides.

## Not decided here / routine follow-on

Everything is routine for the named slice unless listed under "Out of scope".

**B6, Life events: retirement per Member and super access** (needs ADR-0018's pack for the ages,
see the sequencing note below):
- `life_events.resolve` and its unit tests; the date-to-year rule with leap-day birthdays.
- `Line.accessible_from_year` for `superannuation` per section 4, replacing "accessible at
  retirement" in the service and in the stated-simplifications panel.
- A Life events timeline on the Projection screen, and Life event provenance.
- Property test: the year rule matches B1's `retirement_age - age_now` for every date of birth.
- Golden: retire before preservation age leaves a gap that becomes a shortfall.

**B7, salary Income stream and super contributions:**
- The migration, ORM model, registry (`salary`, kind `employment`), CRUD service with audit, API,
  Income streams screen (ADR-0014 patterns), API client regeneration.
- The `real_wage_growth` key and its household default seed (an illustrative 0%).
- Salary and super inflows per section 4 and 5, stated simplifications on the screen, fixture
  extension.
- Property tests: contributions never exceed gross; a stream with a `retirement` end ends in the
  Member's retirement year.

**B8, spending phases:**
- The migration, the `annual_spending` conversion (section 6) with its test, phase CRUD with audit and
  the whole-list validation, resolver extension, B2 bisection over a common factor, the Spending
  screen, fixture.
- Flagging Scenarios that override the phase list when the Household default list changes (the
  ADR-0016 requirement, extended).

**UI and iconography:** no new iconography or design-language additions. Income streams and the
phase editor use ADR-0014's list and form patterns with existing lucide icons.

**Sequencing (decided):** B10 lands before B6. B6 needs `preservation_age` from the Rule pack, and
B7 needs the SG and contributions-tax parameters. ADR-0018 therefore puts B10 (the pack contract and
the AU ages) **before** B6, and B7 adds its two parameters to the pack as data.

**Sequencing (amended 2026-10-26): B8 lands before B7.** B7 credits take-home pay, which is only
meaningful once spending phases can cover the working years (section 5). The order is B8, then B7.
B8 must not give the sample household a `start` phase before B7 adds salaries, because a `start`
phase with no income would draw down assets in the working years. B7 adds the salaries and the
working-years phase to the fixture together, and enforces the year-0 phase rule.

## Out of scope

Reversionary amounts, per-stream scheme indexation and frozen foreign pensions (a backlog design item); inheritance,
death of a Member and other stored Life events; Division 293; tax (the Tax planning epic); the Rule pack shape
(ADR-0018); Account-level overrides; joint-owned stream behaviour; transition to retirement.

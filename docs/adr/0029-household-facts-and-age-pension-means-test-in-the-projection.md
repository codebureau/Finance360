# 0029. Household facts for the Age Pension, and how the means test reads the projection

**Status:** Accepted
**Date:** 2026-11-22
**Issue:** #68 (epic #4, M3 First projection; unblocks #69 and #70)

## Context

ADR-0017 fixed the Age Pension as a **calculated** Income stream, never a row, credited to cash
before Drawdown. ADR-0018 fixed that its figures reach the engine as a materialised rules block
(the first `INPUT_VERSION` bump after B1) and that a run never reads the pack again. The pure means
test exists (`age_pension_annual(params, couple, homeowner, assets, financial_assets, other_income)`
in PR #74). What is undecided is what it reads: two facts the app does not hold (couple status,
homeownership), which projected balances count and when, how the pension avoids depending
circularly on the Drawdown it affects, and how the rule figures and the pension's working reach and
leave the engine. `INPUT_VERSION` is 2 and `ENGINE_VERSION` is 2 today.

## Decision

### 1. Household facts

New term **Household fact** (CONTEXT.md): a stated fact about the Household that a rule needs and
the app cannot derive. It is stored on the Household, audited, and **is not an Assumption**: a
Scenario cannot vary it.

- Three nullable columns on `household` (additive migration, audited as entity `household`):
  - `couple_status`: `single` or `couple` (CHECK), meaning the Household's Members are a pension
    couple (assessed together on combined assets and income).
  - `homeowner`: boolean, meaning the Household owns the home it lives in. The home is exempt from
    the assets test. This fact only selects the assets-test threshold; the app does not identify
    the home among Property Accounts (the third fact does).
  - `principal_home_account_id`: a reference to one Property Account, chosen on the Household
    screen. Only meaningful for a homeowner; it is the one Property Account the test exempts.
- **Who enters them:** the owner, on the Household screen (a routine slice; the home is a picker).
- **Defaults when null:** the service resolves them, and the working and provenance say
  "default, not entered":
  - `couple_status`: `couple` when the Household has exactly two Members, `single` when it has one.
  - `homeowner`: `true`.
  - `principal_home_account_id`: when the Household is a homeowner (entered or default) and has
    exactly one open Property Account, that Account. With no Property Account there is no home to
    exempt. **A homeowner with several Property Accounts and none chosen is not modelled**: no
    pension, and the run asks the owner to choose the home. Guessing would silently exempt or
    assess the wrong Account. For a non-homeowner every Property Account is assessed and a stored
    choice is ignored. A chosen Account that is closed or not a Property is treated as unset.
- **Validation:** `couple` requires exactly two Members and `single` exactly one. A Household that
  does not fit (an explicit value that disagrees with the Member count, or more than two Members,
  such as two unpartnered Members assessed separately) is **not modelled**: the projection runs
  with no Age Pension and the run states why. It is never a silent zero and never a failed run.
- **Fixed over the projection.** No Life event changes them. Death of a Member, a couple becoming
  single, selling or downsizing the home, and reversionary amounts (#26) wait for their own
  design. The only per-Member Age Pension fact is the Member's Age Pension age (a Life event from
  the Rule pack, ADR-0017), already available.

### 2. What the means test reads

**Assessable assets** (new CONTEXT term) are what the means test counts. Projected lines vary by
year; Property and Vehicle Accounts are **fixed per-year inputs**, not lines.

| Item | Counted? |
|---|---|
| `cash`, `investments` lines | Yes, from year 0. Financial (deemed) |
| `superannuation`, `overseas_pensions` lines | Yes, from the **owner's own Age Pension age year**; no before. Financial (deemed) |
| Property Accounts other than the principal home | Yes, all, at their latest Valuation as at the projection start, constant in real terms (`cpi`). **Not financial** (not deemed) |
| Vehicle Accounts | Yes, at their latest Valuation, held flat in nominal terms, so falling in real terms by the run's inflation (one `scale` per year, as the `frozen` policy of ADR-0018). A stand-in for depreciation. **Not financial** |
| The principal home | No (exempt) |
| Liabilities | No deduction (see below) |

- **Super rule.** Super in accumulation is exempt until its owner reaches Age Pension age and counts
  from then. So each Member's super (and `overseas_pensions`, which follow the same rule; foreign
  schemes' real treatment is not modelled) starts counting in that Member's `eligible_from_year`.
  Known simplification, not modelled: super already being drawn as a retirement-phase pension
  before Age Pension age is assessable in practice. Not counting it overstates the pension in those
  years.
- **Assets are not all financial.** `financial_assets` (deemed) is cash, Investments, and super and
  overseas pensions once counted. `assets` adds Property and Vehicles. The pure function already
  takes both.
- **Property and Vehicles:** every Account of those Categories except the principal home, at its
  latest Valuation as at `as_at` (Position's own selection; an Account Position flags for no
  Valuation or FX rate stays out, and the working lists it). A jointly owned Account counts in full
  (the household is assessed). The service bakes one `Money` per year for each of the two kinds
  into the input, and the working shows them under their own labels, "fixed input, not projected".
- **Liabilities: no deduction, a known simplification.** The real rule deducts debt secured against
  an assessed asset. Nothing links a loan Account to the asset it secures today, so it cannot be
  done honestly. Not deducting overstates assets, so the pension is conservative. The working says
  so whenever a Property or Vehicle is assessed. Linking loans to assets belongs with the property
  work (#26).
- **Income** is the deemed income on financial assets (inside the function) plus `other_income`: the
  **gross** pay of the stored Income streams in force that year, for both Members of a couple. The
  service bakes it into the input as one `Money` per year, because the engine never sees gross
  (stream inflows carry take-home). The pension itself is not income to the test.
- **Known gap: rent.** Income streams cannot represent rent today (only `salary` is registered,
  ADR-0017), so rent from an assessed Property is **not** counted under the income test, and the
  pension is overstated for a household with a rented property. The working **flags this whenever a
  non-home Property is assessed.** A rental Income stream type is a follow-up with the property work
  (#26); it is not designed here. Work bonus, business and investment income streams, rent
  assistance, the energy supplement, gifting and hardship are also not modelled.
- **Eligibility:** a Member is eligible from the year index of their `age_pension_age` Life event
  (ADR-0017). The test uses **combined** assets and income of the couple, whatever each partner's
  eligibility. With one eligible partner in a couple, the pension is **half** the couple total
  (one partner's rate less half the combined reduction), credited to that Member; with both, the
  total is split in half with `allocate`. No eligible Member in a year means no test that year.
- **Residence, citizenship and qualifying-period tests are not modelled.**

### 3. Timing and the circularity: test on the opening position

The assessment for year `t` reads **opening balances** (the closing balances of year `t-1`, or the
starting Position for year 0), before growth, credits or withdrawals. The pension is then credited
to the eligible Member's `cash` line in step 2, before withdrawals (as ADR-0017 section 5), so it
funds spending first.

- **Why opening:** the opening position is fixed before year `t` begins, so the pension depends only
  on the past. Testing on closing balances would make the pension depend on withdrawals, which
  depend on the pension. Within-year iteration to a fixed point would add loops and a convergence
  question to a pure, deterministic engine for no real gain.
- **Cost, accepted and stated:** a one-year lag (this year's draw affects next year's pension) and
  no growth in the assessed year. Deeming is also computed on the opening amount, which is how a
  start-of-year assessment behaves.
- The new engine step is **step 0**, before growth: assess; step 2 credits the result.
  Deterministic and non-circular by construction.

### 4. How the pension appears

- A **calculated Income stream** of tax kind `government` (ADR-0017), per couple assessed, paid per
  Member into that Member's own `cash` line. Never a row, never an entered amount.
- **Not taxed in the plan** (the credit is the whole payment). Stated on the screen: it is
  assessable income in practice, but often reduced to little or nothing by tax offsets at the
  amounts in play. Revisit when tax of calculated streams is designed.
- **Result:** each `YearRow` gains `age_pension: AgePensionYear | None`, holding the Members paid
  and their amounts, couple and homeowner as used, each counted Category's assessed amount, the Property and Vehicle amounts (labelled "fixed input"), assets,
  financial assets, other income, deemed income, the income-test and assets-test results, which one
  bound (or none, or the maximum), and the maximum. This is **the working**: it is stored in
  `result_json`, so traceability never needs a re-run. The pure module exposes a function that
  returns this working, and `age_pension_annual` stays a thin wrapper over it, so the two cannot
  disagree. The credit is also in `credited` on the cash line, so the existing identities hold.
- **Provenance** (`provenance_json`) lists the Household facts used (value, entered or default),
  each eligibility date and each rule value (key, FY, value, origin per ADR-0022, provisional flag).

### 5. Rule values reach the engine

- **A new input field, `age_pension: AgePensionInput | None`** (default `None`), holds: the
  couple and homeowner values, `(member_id, eligible_from_year)` per Member, `other_income` per
  year, the fixed per-year Property and Vehicle amounts (each Account's id, value and Valuation
  date go in provenance), and the rules as **per-span `AgePensionParams`** (ints, `Money` and `Rate` only). `None` means
  no Age Pension is calculated (the facts do not fit, or the pack holds no `age_pension.` figures),
  and the run says which. A partly captured set is a resolution error naming the missing key.
- **Materialisation reuses `materialise`.** The `age_pension.` prefix stops being treated like
  `tax.` there: `is_exact_year_key` is split into a materialise predicate (`tax.` only) and an
  override predicate (both prefixes). Every `age_pension.` key is `cpi`, so the result is one span,
  constant in real terms; any FY the pack publishes (or captures) applies from that year as
  published, as ADR-0018 section 2 already says. Fortnightly figures stay as published and the pure
  function converts them.
- **Real-terms caveat, stated:** payment rates are wage-benchmarked in practice, so holding them
  constant in real terms understates the pension over long horizons (a conservative simplification;
  a `wages` policy is not built).
- **No Scenario override yet.** `age_pension.*` stays out of the overridable keys. Best and Worst
  differ through returns, inflation and retirement age flowing into the assessed balances, which is
  enough for #70. Policy-risk overrides stay a hook: allowing them later is dropping the prefix
  from the override predicate, because `materialise` already takes overrides; no input or engine
  version change.

### 6. Versions and reproducibility

- `INPUT_VERSION` 2 to **3** (the new field), with a pure upcaster that sets `age_pension = None`.
  An old run's numbers are unchanged through it.
- `ENGINE_VERSION` 2 to **3** (new step 0, new result field). The result gains an optional field, so
  `result_json` of old runs still loads with `age_pension = None`. A run saved at engine 2 keeps
  its stored version and is shown as such, per ADR-0016's existing re-run rule.
- The saved input carries the whole rules block and facts, so a pack revision or a later change to
  a Household fact never alters a saved run. No change to the tax engine version or to the run
  record's columns: `rule_pack_version` already names the effective pack.
- Golden engine test: update with its `engine_version`, and add a case checked against a published
  Services Australia example (ADR-0018 section 6).

## Consequences

- One engine, one more step. The pension is deterministic, traceable per year, and cannot depend on
  drawdown.
- The household's own two facts are explicit and audited, with visible defaults, rather than
  guessed in the engine.
- Cost: a one-year lag, constant real rates, an untaxed, couple-or-single-only pension, no debt
  deduction (conservative) and no rent income (overstates for a rented property), each flagged in
  the working.
- Forecloses: the pension as a stored row; assessing on closing balances or by iteration;
  Scenario-varied Household facts; a fact changing mid-projection before its own design; the engine
  reading the pack or the Household.

## Not decided here / routine follow-on

- Rent assistance, the energy supplement, work bonus, gifting, asset hardship, part-rate cases,
  residence tests, reversionary amounts (#26), non-partnered multi-Member Households, tax on the
  pension, wage-indexed payment rates, Scenario overrides of entitlement rules, Age Pension in full
  (a candidate milestone). Deducting debt secured on an assessed asset, rent as an Income stream,
  retirement-phase super before Age Pension age, and growth or sale of Property in a projection are
  also left out (#26).
- **UI and iconography:** a form control on the Household screen and a working view in the run
  detail, both in ADR-0014 patterns. No new iconography or design-language additions.

## Consequences for #69 and #70

**#69 (engine slice)** also owns, as routine: the `AgePensionInput` dataclass and its canonical JSON;
the upcaster and `INPUT_VERSION` and `ENGINE_VERSION` bumps with the golden test; step 0 in the
engine; the result field and its serialiser (old results load); the service resolving facts,
defaults, eligibility years, `other_income` per year, the per-year Property and Vehicle amounts
(Vehicles deflated by inflation), and materialised params (splitting
`is_exact_year_key`); provenance entries; the "not calculated, because..." states; the working
function in `age_pension.py`; property tests (no negative Holdings, ownership shares unchanged, the
pension never rises as assets rise at the engine level); a mutation proof of the opening-balance
timing. It needs the Household facts columns to exist, so the facts slice lands first.

**#70 (screens)** also owns: the Household facts controls if the facts task did not build them, the
working view per year from `AgePensionYear` (tests applied, which bound, assets, income, rule values
with source and origin), the stated-simplifications panel, the Guide `08-projection.md` and "What to
provide, when", the extended `fixtures/sample_household/` (invented two Members with a Life event
reaching Age Pension age inside the horizon, a principal home and one non-home Property or
Vehicle, so the assets test visibly binds in "How to test"), and screenshots.

**Household facts task (filed after acceptance):** the three columns, migration, audit, Household
screen controls (couple, homeowner, a picker for the home among Property Accounts), fixture, Guide and "What to provide, when", and the validation and defaults of
section 1.

# 0028. Regular deduction from super: the Line debit

**Status:** Accepted
**Date:** 2026-11-14
**Issue:** — (part of the Tax planning epic). Called for by ADR-0019 section 10.

## Context

ADR-0015's engine can only **credit** a line (`Inflow`) or spend from the household (`Outflow`, met
by drawdown). Three things are a regular amount leaving one Member's super balance, not spending:

- Division 293 tax when the fund pays it (the `div293_payer = super` Assumption, ADR-0019 section 10);
- insurance premiums paid from the balance (the cover itself is the insurance design's business);
- later, fund fees.

ADR-0019 settled that tax must not invent its own mechanism, and that tax only emits
`(Member, financial year, amount, payer)` (`Div293Emission` in `tax/div293.py`) for projection to map.
This ADR decides the mechanism and the mapping. The tax-in-projection slice (take-home pay, and the `own` payer as an
`Outflow`) changes neither `ProjectionInput` nor the versions, so the two do not conflict.

Facts in the code this rests on: the engine has no tax of its own (contributions tax is taken in
`projection.service` before an `Inflow` is emitted; returns are entered as the line's rate); a line is
one per `(member_id, category_key)`, never per Account (ADR-0015 section 2); a projection year `t`
maps to financial year `fy_of(as_at) + t`, keyed by the year it ends in (as `_rate_years` already
does).

## Decision

### 1. One general field: `ProjectionInput.debits` (`Line debit`)

```python
@dataclass(frozen=True)
class LineDebit:
    member_id: int
    category_key: str      # a line that exists, as for an Inflow
    year: int              # projection year t, 0 <= year < years
    amount: Money          # above 0, in the input currency, today's dollars
    reason: str            # a key from DEBIT_REASONS: "div293", "insurance_premium", "fund_fee"
    ref: str               # short human label of what it is for, 1 to 40 chars, e.g. "FY2025-26"
```

- **One year per debit, not a range.** Division 293 differs every year; a constant premium is emitted
  as one debit per year (at most 120). This keeps the engine free of a second range convention.
- `reason` is a small closed set in `input.py`. It is a tag for traceability and display, never
  engine logic: **no code branches on it**. Adding a reason adds no field and needs no version bump.
- `ref` carries the "which year's liability" label. It is text for the working, never parsed.
- Canonical order: `(year, member_id, category_key, reason, ref, amount)`. Serialised as a
  `debits` list in canonical JSON, money as `{minor, ccy}`. Multiple debits on one line and year are
  allowed.
- **Why general, not tax-specific:** the engine only knows "a regular, non-discretionary amount leaves
  this line". Division 293, premiums and fees differ only in who computes the amount and when, which
  is the service's concern (ADR-0015: the engine never knows where a value came from). One field
  means one set of step logic, one property test and one version bump, and tax stays below projection
  (ADR-0019 section 1).

### 2. Step logic (engine)

Within year `t`, after step 2 (cash flows) and **before step 3 (withdrawals)**, a new step 2b:

1. For each line, take that line's debits for year `t` in canonical order. Each is paid
   `min(amount, balance still available on the line)`, where the available balance is
   `grown + credited - debits already paid this year`.
2. The part that cannot be paid from the line is **unpaid** and is **paid personally**: it is added to
   that year's `need`, as if the Member paid it from household money, and is met by drawdown like
   spending. It is not borrowed and not carried to a later year. The line never goes below zero.
3. `need` is the active outflows plus all unpaid debit parts. Withdrawals then run on the reduced
   available balances, so the drawdown weights see the post-debit balance. The identity
   `withdrawn + shortfall = need` holds as before, because `need` includes the unpaid part.

Why this position: it follows the end-of-year, flows-earn-no-growth convention (ADR-0015 section 6),
and a debit is not a choice, so it comes ahead of drawdown. A debit ignores `accessible_from_year`:
the fund pays Division 293 and premiums whatever the Member's access age.

- **No tax on a debit.** It is paid out of a balance the engine already treats as after-tax
  (contributions tax is taken before the credit; returns are the line's net rate). The amount leaves
  the line and the household; it does not reappear as income or spending.
- **Real terms.** The amount is in today's dollars like every other input. The service supplies it on
  the same basis as take-home pay; the engine does not index it.
- **Unpaid is shown, never hidden.** See section 5.

### 3. Mapping from tax (service only; engine knows nothing of tax)

`projection.service` turns `Div293Emission(member_id, financial_year, amount, payer)` into debits:

- **Lag of one projection year** (ADR-0019 section 10): the liability for FY `N` is paid in the
  projection year whose FY is `N + 1`, so debit year `t = (N + 1) - fy_of(as_at)`.
- **Year 0** pays the liability for the year just ended (`fy_of(as_at) - 1`), **estimated from the
  year-0 figures**: it uses the emission for `fy_of(as_at)`. The debit's `ref` says so
  ("FY2025-26, estimated from year-0 figures").
- The emission for the last projected FY falls due after the horizon: no debit; the working states it.
- Zero amounts emit no debit.
- **Keyed by `div293_payer` (per Member, Assumption, from the tax-in-projection slice):**
  - `super`: a `LineDebit(member_id, "superannuation", t, amount, "div293", ref)`;
  - `own`: an `Outflow` for the same year and amount (the tax-in-projection slice). The lag mapping is **one shared function**
    that both payers use, so the two cannot disagree on timing.
- **Default payer is `super`.** Once this mechanism is built, the `div293_payer` Assumption defaults
  to `super` for every Member (the common case: the fund pays Division 293 from the super balance, and
  the projection should show its effect). Until then the tax-in-projection slice ships with `own` and shows `super` as "not yet modelled"; the Developer
  of this ADR switches the default. **Effect on existing Scenarios:** their results change only when
  tax-in-projection is on (Division 293 is not estimated otherwise); with it off nothing changes.
  Saved runs stay as computed (section 4).
- **Which super line:** a Member has exactly one `superannuation` line (lines are per Member and
  Category; their several fund Accounts are already summed into it). Division 293 is the Member's
  own liability, so it debits **that Member's** line, never another Member's, and never
  `overseas_pensions`.
- Premiums and fund fees (later slices) build `LineDebit`s directly with their own `reason`; they do
  not pass through tax.

### 4. Versions (ADR-0015 sections 2 and 3)

- **`INPUT_VERSION` 1 to 2.** A field is added. A pure upcaster `1: _upcast_v1_to_v2` adds
  `"debits": []`, so every stored snapshot loads and upcasts to an input with no debits.
- **`ENGINE_VERSION` 1 to 2.** The step order gains a step and the definition of an output changes
  (the line identity gains `debited`; each year gains per-debit rows). Figures for an input with no
  debits are identical, which is what the unchanged golden figures must prove.
- **Result shape.** Each `LineYear` gains `debited` (sum paid). Each `YearRow` gains `debits`:
  `(member_id, category_key, reason, ref, requested, paid, unpaid)`, where `paid` came from the line
  and `unpaid` was paid personally. Identities:
  `closing = opening + growth + credited - debited - withdrawn`, `requested = paid + unpaid` per
  debit, and `need = spending + sum of unpaid`.
- **Existing saved runs** (engine 1, input 1; rows are immutable, ADR-0016): they **still display as
  computed by engine v1** from their stored result and snapshot, and the UI offers "re-run with the
  current engine" as for any version change (that saves a new run; with no debits it reproduces the
  same figures). To make that true, `result_json` reads a missing `debited` as zero and a missing
  `debits` as empty, and `runs` reads a missing provenance `debits` section as none. No migration
  and no rewrite of stored rows.
- **Goldens** record `engine_version`; each golden file's recorded version changes to 2 with **no
  figure changing**. A figure moving in a no-debit golden means the change is wrong.

### 5. Traceability

- The engine result carries every debit with its `reason`, `ref`, requested, paid and unpaid amounts.
  The working (the yearly table's per-line detail) shows each as its own row under the line, labelled
  from `reason` and `ref`, for example "Division 293 (FY2025-26): $X".
- The service adds a `debits` section to the run's provenance snapshot (as it already does for
  `income`, `spending` and `rules`): per debit, the source that produced it. For Division 293 that
  is the Member, the FY, the emission amount, whether it was the year-0 stand-in, the `div293_payer`
  Assumption's layer, and the Rule pack version used (ADR-0018). This is the "traceable figures"
  link back to the Tax estimate's own working.
- **Unpaid debits are flagged**, in the existing warning treatment, naming the Member, the year and
  the amount ("Division 293 could not be fully paid from super in year 3: $X paid personally"). The
  debit's row shows requested, paid from super and paid personally, and the year's need shows the
  amount it added.
- A line debit is a row in an existing table, so there is **no new iconography and no design-language
  addition**.

## Consequences

- One field, one step and one version bump serve Division 293 now and premiums and fees later; the
  next consumer adds a `reason` and a service builder, not engine work.
- Tax stays pure and below projection; the only coupling is `Div293Emission`, which already exists.
- Sustainable income (bisection over spending) is unaffected: debits are fixed inputs, so its
  monotonicity holds.
- A debit the line cannot cover falls to household spending, so the cost is never understated, and
  the identities stay exact.
- Forecloses: a tax-specific deduction path; engine logic that branches on `reason`; debits that
  borrow, carry forward or accrue interest (the unpaid part is paid personally that year, nothing more); debits taxed in the engine.

## Not decided here / routine follow-on

For the Developer slice that implements this (the issue is the ADR only; it needs its own sub-issue
under the insurance design or the Tax planning epic, ordered **after the tax-in-projection slice merges** so it extends that slice's shared lag mapping and its
`div293_payer` Assumption rather than writing a parallel one):

- `LineDebit`, the `debits` field, validation, canonical JSON, the v1 to v2 upcaster, both version
  bumps, the engine step, the `LineYear.debited` and `YearRow.debits` result fields, `result_json`
  tolerance for old shapes, and the regenerated API client.
- Tests: golden answers **unchanged** when there are no debits (only the recorded `engine_version`
  moves); a Hypothesis property test that no balance is ever negative and `requested = paid + unpaid`
  with random debits and that the unpaid part is in `need`; the identities; a hand-worked golden with one debit, and one case with a too-small balance (the unpaid part reaches `need` and is drawn down); a
  version-bump test (old v1 snapshot upcasts, an old stored result still loads and shows as engine
  v1, re-run saves a new run); a mapping test for the lag, year 0 stand-in and last-year drop.
- Switching the `div293_payer` default to `super` and removing the "not yet modelled" note;
- The working display and the unpaid notice; the provenance `debits` section.
- The **Guide** page for projection (what leaves super and when, the one-year lag, the unpaid case),
  and "What to provide, when" if it changes.
- **Fixture:** a Scenario in `fixtures/sample_household/` with `div293_payer = super` for a Member
  high enough to owe it (invented values), so "How to test" has a debit to look at.
- `CONTEXT.md` gains **Line debit** in this PR.

## Out of scope

- Premium amounts and cover (the insurance cover ADR); the Division 293 estimate itself; the
  content and rates of fund fees; the `div293_payer` Assumption and the `own` `Outflow`.

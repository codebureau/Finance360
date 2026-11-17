# 0019. Tax capability: record register, pure estimate engine, saved estimates and Tax what-ifs

**Status:** Accepted
**Date:** 2026-11-01
**Issue:** — (epic: Tax planning)

## Context

The Tax planning epic brings tax planning in as near-term planning beside the retirement projection (requirements §4:
estimates, what-ifs, a return worksheet and a register of deductible items are in scope; lodging is
not). A local prototype proved the arithmetic (a pure engine over entered totals, rules passed in) but
held category totals only. The product needs more than the prototype:

- **Records first.** Line-level records that roll up to totals are useful with no estimate run, kept
  across years and easy to retrieve.
- **Estimates and Tax what-ifs** that reuse ADR-0016's pattern and are reachable by a later MCP or AI
  layer.
- **A reproducible proof of correctness:** the engine reproduces lodged returns to the cent.
- **Later reuse inside the projection** (ADR-0017 anticipates replacing `take_home_annual`) without a
  dependency cycle.

ADR-0015 (pure engine, saved input), ADR-0016 (base plus overrides, immutable saved result),
ADR-0017 (Income stream type registry), ADR-0018 (Rule pack, `brackets` reserved) and ADR-0009
(Money, audit, supersession) already fix the patterns. This ADR applies them and decides what is new.

## Decision

### 1. Placement and dependency direction

- **`capabilities/tax/`** is a new Capability. Its parts:
  - **Pure modules:** `input`, `rules`, `labels`, `record_types/`, `engine`, `reconcile`,
    `breakeven`, `compare`, `result_json`.
  - **Impure `service` and `evidence`:** database, audit and file I/O.
  - `api/tax.py` calls only the service.
- **Layer order: `projection > tax > position > household`** (the existing projection > position >
  household contract gains `tax` between). Tax never imports projection. When projection later needs
  tax, `projection.service` imports tax's *pure* modules (`input`, `rules`, `engine`), builds a
  `TaxInput` from Income streams for each projection year, and bakes the resulting take-home pay into
  the `Inflow` it already emits. **The projection engine and `ProjectionInput` do not change** (no
  version bump) for that step.
- **The tax engine is pure:** frozen dataclasses of tuples, no I/O, no clock, no Rule pack read. The
  service reads the pack through the registry (ADR-0018), converts the financial year's values into a
  typed `TaxRules` block, and passes it in.
- **Prefill needs Income streams, and the stream type registry (`projection/income.py`) is above tax.**
  When the prefill slice lands, the registry (pure data: type key, tax kind, indexation) moves down
  to `core/`. Tax must not import projection to get it. This is routine, but it is the reason the
  layering holds.
- **Import-linter** (the Developer edits `pyproject.toml` in the first slice):
  1. replace the projection > position > household layers contract with
     `projection > tax > position > household`;
  2. a forbidden contract: tax's pure modules import none of `core.db`, `core.models`, `core.audit`,
     `tax.service`, `tax.evidence`, or any other Capability;
  3. `tax.engine` and `tax.input` never import `core.rulepack` or `rulepacks` (copy of the projection
     contract);
  4. add `capabilities.tax` to the forbidden list of projection's pure modules (so only
     `projection.service` may use it), and add tax to the auth and data-Capability independence lists;
  5. tax imports `core`, `household` and (for prefill) `position` only; it does not import `imports`,
     `reference` or `auth`.
  Existing rules ("capabilities and the API never import `rulepacks`", "entry points reach domain logic
  only through the service layer") already cover the rest. No contract is weakened.

### 2. Tax records, Record types and Return labels

- A **Tax record** belongs to one Member (the taxpayer). A joint cost or income is entered as each
  Member's share (prefill splits with `core.money.allocate`). One exception: a household-level fact
  (for example dependent children) has a null Member.
- A **Return label** is a code-registry entry (`labels.py`): key, return item (for example `item1`,
  `D5`), the item's plain-language name and description (so a person or an AI can find it), the
  direction (`income`, `deduction`, `offset`, `credit`, `fact` or `memo`), the value kind (`money`,
  `days` or `flag`), whether the engine `modelled` it, and which Record types may feed it. `memo`
  labels are on no return but feed views (section 9). **Every return item the product knows about is
  registered, modelled or not.** Not called a Category, which is reserved (CONTEXT.md).
- A **Record type** is a registry entry in `record_types/` (a code registry like the Income stream
  type and the Assumption key, not a top-level plug-in tree, because only tax uses it). It declares:
  - `key`; a payload parser to a frozen value (rejects floats; money is `{minor, ccy}`, quantities are
    scaled integers or decimal strings);
  - `span(payload) -> (from_on, to_on | None)`;
  - a pure `contributions(records, financial_year, rules) -> tuple[Contribution]`, where a Contribution
    is `(record_id, label, value)`. A type may feed several labels. It rounds once per record with
    `core.money.scale`; the label total is the exact sum of contributions. It uses Rule pack values
    only through the `rules` argument (per-unit rates, caps, effective-life tables).
- **Label totals are never stored.** A **Label total** is `sum(contributions)` for a Member, label and
  financial year, computed on demand and snapshotted into an estimate. Because every contribution
  carries its `record_id`, a total always drills down to its records.
- **Build order for the mechanism:** `line` first (one amount, one label, optional work-use share as a
  `Rate`; also serves the entered totals for capital gains, rental and sole trader, and the label
  registry lists `line` as the only type for those labels until a richer one replaces it). The second
  is `income_statement` (section 9), which proves multi-label output. Depreciating assets, the home
  office hours schedule and the car kilometres schedule are later slices, one type each, with
  per-unit rates in the pack. **No framework beyond what two types need.**
- **Facts** (private cover days, non-commercial loss rules met, HELP debt) are Records of a `fact`
  type that yield `flag` or `days` labels, so the engine still sees only label totals.

### 3. Estimate input, engine and reproducibility

- `TaxInput` (`TAX_INPUT_VERSION`, canonical JSON, no float, with the same round-trip guarantee as
  ADR-0015): the financial year; for each Household Member, their date of birth and label totals; the
  family facts; the `TaxRules` block; and the list of provenance entries. `estimate(TaxInput) ->
  TaxResult` carries `TAX_ENGINE_VERSION`. Both versions follow ADR-0015 section 3 (golden tests pin
  the engine version).
- **An estimate is always for the whole Household** for one financial year, because the family tests
  (Medicare levy reduction, MLS) need both Members. A "per Member" view is a slice of the result.
- **Amounts are nominal dollars of that financial year** (law is nominal). Projection deflates where
  it must; the tax engine never works in today's dollars.
- **Money:** `Money` in AUD only, minor units, ratios through `core.money.scale`, rates as `Rate`.
  ATO rounding conventions (for example truncating taxable income to whole dollars) are explicit named
  steps in the engine, implemented in `core.money` (nowhere else rounds, ADR-0009), and found by the
  proof against assessments (section 8). A foreign-currency amount is entered already converted, and
  foreign-income conversion rules are unmodelled (reported).
- **Unmodelled items are reported, never ignored.** The result lists every label with a non-zero
  total whose `modelled` flag is false, and the estimate is marked `incomplete`.

### 4. Rule pack keys and the `provisional` marker

- Tax values join the AU pack under a `tax.` key prefix, one group per modelled rule (brackets,
  offsets, Medicare levy, the low-income reduction, MLS tiers, HELP, the small business offset, super
  caps, Division 293 later). The exact list is derived from the prototype's rule shape in the port
  slice.
- **`brackets` is built here for the first time** (ordered `(over: Money, rate: Rate)`, canonical JSON,
  included in the revision hash). Thresholds not indexed by law are `frozen`; rates and the
  Medicare levy rate repeat. New `money` units (`per_km`, `per_hour`) arrive with the record types that
  need them.
- **`provisional`:** an FY entry may mark individual keys `provisional` with a note ("announced, not
  yet law"). It is part of the canonical JSON and the hash, so changing it needs a `revision` bump. The
  `TaxRules` block carries it. An estimate copies the markers into its input and shows a warning while
  any exist for the year. A marker is cleared by a revision bump when the figure becomes law. It is
  not a second value kind.
- **Exact years only for estimates.** An estimate for a financial year with no pack entry fails naming
  the year; it never carries forward. Carry-forward (ADR-0018 section 3, including `frozen` erosion
  of thresholds) applies only when projection later asks for future years.
- Figures reach the pack through the guided process (B11 script, and in-app capture, ADR-0022), not by fetching. No figure
  appears in this ADR.

### 5. Storage (additive migration; Money in typed columns; JSON with `CHECK (json_valid)` and no float)

| Table | Content |
|---|---|
| `tax_record` | `id`, `member_id` (nullable FK), `record_type`, `from_on`, `to_on` (nullable), `description`, `notes`, `payload_json`, `created_at`. Edited in place, deleted by a service call, **audited** (entity `tax_record`; the Audit trail is the history). The financial year is **derived** from the dates, never stored, so it cannot disagree. A record is relevant to a year if its span overlaps it, so a depreciating asset (open `to_on`) spans years. Indexed on `(member_id, from_on)` and `record_type` |
| `tax_evidence`, `tax_record_evidence` | evidence file reference (section 7) and the many-to-many link to records |
| `tax_whatif` | `id`, `name` (unique), `financial_year`, `notes`, `origin` (`user` or `ai`), `overrides_json`, `created_at`. Editable, **audited**, like a Scenario |
| `tax_estimate` | an immutable saved estimate (section 6) |
| `tax_lodged_result` | a lodged result (section 8) |

### 6. Estimates, Tax what-ifs, comparison, break-even

- A **Tax what-if** is a base plus **overrides**, never inheriting from another. The base is the
  register's label totals for the year (and, from the prefill slice, selected prefilled forecasts).
  Overrides are structured, validated by the service against the label and Record type registries, and
  use three operations only (values are absolute, never deltas, as ADR-0016):
  - `set` a Member's label total to an amount (the "slider");
  - `exclude` a label or a Record (the "tick off");
  - `add_record` a hypothetical Record of any type (a laptop purchase is a depreciating asset record,
    so the type's own function computes the effect).
- A saved **Tax estimate** is the immutable counterpart of ADR-0016's saved run, written on an explicit
  "Save estimate" (`whatif_id` null for the base). Columns: `id`, `created_at`, `label`, `financial_year`,
  `whatif_id` (FK RESTRICT) and `whatif_name`, `tax_engine_version`, `tax_input_version`,
  `rule_pack_version` (the ADR-0018 label), `input_json`, `result_json`, `provenance_json`,
  `incomplete`, `has_provisional`.
  - **Pinning:** `input_json` holds the label totals and the full `TaxRules` block, so an estimate
    never reads the pack, the register or the engine to be explained. Reproduction is
    `estimate(from_json(input_json))`, equal to the stored result whenever the engine version is
    current; otherwise the stored result is shown as computed by vN, with "re-run with the current
    engine" saving a **new** estimate.
  - `provenance_json` lists, per label total, each contribution (record id, description, amount) and
    each override applied, copied at save time, so deleting or editing a record later changes nothing.
  - Triggers reject `UPDATE` and `DELETE`; the what-if FK is RESTRICT. Deleting an estimate waits for
    the cleanup ADR flagged in ADR-0016. An estimate is a few KB, so size is not a concern.
  - Audited as `create` only.
- **Comparison** is a pure function over two saved estimates of the same financial year: per-label and
  per-result deltas. It lives in `compare.py`.
- **Break-even** (for example: is sole trader work worth it?) is a pure search in `breakeven.py`, the
  same technique as the sustainable-income bisection: an integer bisection in minor units over one
  override amount, until a stated condition holds (for example household net outcome at least the
  base case). **Monotonicity is not guaranteed** (MLS tiers and a HELP cliff can make net fall as
  income rises), so the function returns *a* crossing, verifies it at the found value and one minor
  unit either side, and flags a non-monotone range. The result is stored only if the user saves the
  found value as an ordinary Tax what-if.
- **AI constraint.** Every operation above is a service function taking and returning structured
  values, available to the API and a later MCP tool. An AI may turn a question into overrides, which
  the service validates deterministically; it may then calculate a Tax what-if (hypothetical, `origin =
  'ai'`, shown with the figures it used) and explain the stored result from `provenance_json`. It may
  not write Records or entered totals (those need confirmation, AGENTS.md rule 4), and it never
  produces a figure that the engine did not.

### 7. Evidence

- Evidence files live in the instance data directory under **`documents/tax/`**, content-addressed:
  `documents/tax/<sha256[:2]>/<sha256>`. The database holds the reference (`sha256`, original
  filename, media type, size, added date), never file bytes. A duplicate upload is the same file.
  Files are never overwritten.
- **Backup and restore need no code change:** ADR-0011 already backs up and restores `documents/` in
  place with the database in one snapshot. Receipts therefore stay out of the repo and out of the
  database, and restore returns files and references consistent with each other.
- Write order: file first (idempotent, content-addressed), then the row in the transaction. An
  orphaned file is harmless. A health check reports a reference whose file is missing or whose hash
  differs, rather than hiding it. Orphan clean-up is an explicit later action.
- Evidence content reaches an AI only through the AI gateway (the image and scanned-document import path), out of scope here.

### 8. Lodged results and the proof of correctness

- A **Lodged result** is a Member's assessed outcome for a financial year, entered from a notice of
  assessment: `tax_lodged_result(member_id, financial_year, assessed_on, items_json, evidence link)`.
  `items_json` maps result keys (taxable income, tax on taxable income, offsets, Medicare levy, MLS,
  HELP, credits, refund or amount payable) to Money, plus named `other` lines for anything the engine
  has no key for. A later notice (an amendment) **supersedes** the earlier row as Valuations do
  (ADR-0009); the earlier stays retrievable. Audited.
- **`compare_to_lodged(result, lodged)`** is pure: it reports each difference and every lodged item the
  engine cannot match.
- **Proof of concept:** a local test builds the engine inputs and expected values from `private/`
  (gitignored) and asserts agreement to the cent for the most recently lodged return, skipping where
  `private/` is absent, like the Importer real-export check. It reports unmatched return items; it
  never passes over them. Synthetic golden tests cover everything in the repo. Each year checked needs
  that year's rules in the pack.
- **Amended 2026-11-17 (#61).** The proof found two conventions:
  - **A half cent goes to the even cent.** It applies at every step that keeps cents: bracket tax,
    the Medicare levy and surcharge, HELP, Division 293, offsets, and ratio shares. One named step in
    `core.money` does it. A fixed direction does not fit: on one lodged return, two surcharge amounts
    on an exact half cent at the same rate settled in opposite directions. The low income tax offset
    is worked out as one fraction and rounded once.
  - **The net outcome is compared within 5 cents.** The notice rounds the final result by a few cents,
    and the labels cannot reproduce that step. Every other item is still compared to the cent, and the
    engine itself stays exact.

  The evidence is one return. A later notice that disagrees reopens the rounding convention.

### 9. Income reconciliation (first release, a view with no table)

- A pure function `reconcile(label totals) -> IncomeReconciliation` per Member and for the Household,
  for one financial year, from the same Records (so there is no second data entry).
- Its source is an **`income_statement` Record type**: one Record per employer per Member per year
  carrying the income statement figures (gross payments, tax withheld, reportable fringe benefits,
  reportable employer super contributions, employer super, novated lease post-tax contributions, other
  deductions, net pay received). It feeds both return labels and `memo` labels.
- **Total package** = gross pay plus employer super, 100%. Slices: **tax withheld (includes Medicare
  levy)**, super, novated lease costs, other deductions, take-home. PAYG withholding on an income
  statement is one figure, so the first release shows one tax slice, not a factual Medicare split. A
  split can come later from the engine, labelled estimated (the maintainer's decision). **Unreconciled = package minus the slices.** It is always
  shown, signed, never netted away or hidden. Percentages come from `core.money.allocate` over 10^6 so
  they sum to exactly 100% (a negative Unreconciled is shown as a note rather than a slice).
- **Reportable fringe benefits sit beside the pie, not in it** (the figure is grossed up and notional);
  the real lease costs are the slice. Fringe benefits and lease costs are entered in this release;
  prediction arrives later (needs the payslip and novated-lease Importer, and the obligations design).
- **Insurance premiums paid from the super balance are not paid from pay,** so they are not a slice and
  not a Division 293 contribution; they appear as a note under the pie (section 10).
- Percent shares and the Unreconciled amount are facts at gross level. They are not an estimate.

### 10. Constraints on later slices (not built here)

- **Division 293.** The engine yields the liability for a financial year from the Rule pack's
  threshold and rate (`tax.div293.*`). Its first version uses gross pay and super guarantee and the
  screen states that it **understates** (the full measure also counts fringe benefits, reportable
  super, investment income and net investment losses).
  - **Lag, confirmed with a challenge.** The ATO assesses Division 293 separately, after the return is
    processed and the fund has reported contributions, so a year's liability is normally paid about a
    year later. The projection shifts it by **one projection year** (a stated simplification: real
    timing varies, and a release from super is paid by the fund after the ATO's authority). The first
    projected year carries the liability for the year just ended, estimated from year-0 figures.
  - **Payer.** An Assumption (`div293_payer`: `super` or `own`, per Member, ADR-0016 registry).
    `own` becomes an `Outflow`; `super` needs the next constraint.
- **One "regular deduction from super" mechanism** serves Division 293, insurance premiums paid from
  the balance (the obligations and insurance design) and later fund fees. Today `ProjectionInput` can only credit a line, so it needs a
  new line-debit field and step logic: an `INPUT_VERSION` and `ENGINE_VERSION` bump under ADR-0015.
  That is a separate ADR or slice owned with that design; **tax must not invent its own mechanism**. This ADR
  only fixes that tax emits `(Member, financial year, amount, payer)` and projection maps it.
- **Prefill and records.** Records are facts. Income streams and other forecasts enter an estimate as
  prefilled, overridable, source-marked base values and are never written as Records. The rule that
  stops a year-to-date Record total and a full-year forecast from both counting is decided in the
  prefill slice.

## Consequences

- Records are valuable on their own, and the estimate sees only label totals. A new Record type is
  code, with no engine, schema or migration change.
- One tax engine serves the estimate screen, the what-if search and, later, the projection, behind the
  same pure function, with no cycle and no change to the projection engine for the take-home step.
- Saved estimates are explainable and reproducible without the pack, the register or any old engine.
- Cost: a few KB per estimate, and no estimate deletion until the cleanup ADR.
- Forecloses: stored label totals; a typed-in total next to its records; tax importing projection;
  the engine reading the pack or the database; a float or `Numeric` anywhere in tax; carry-forward
  silently filling an estimate year; evidence bytes in the database or the repo; a what-if that
  inherits from another; AI-written Records or figures.

## Not decided here / routine follow-on

Everything below is routine for its slice unless it appears under "Out of scope".

- **Port (engine, entered values):** the `tax.` pack keys and their sources via the guided process;
  `brackets`; `TaxRules`; the engine and golden tests; label registry; `line` and `fact` types; the
  `tax_record` and estimate-side migrations; the proof test against `private/`; import-linter edits;
  API and screen (ADR-0014 patterns, existing icons); regenerate the API client; fixture extension
  (invented Records for one year).
- Record register screens: by Member, year and label with drill-down, search, evidence attach.
- Reconciliation screen: the slice may add a small categorical chart colour set, as semantic tokens, to
  the design language (decided: deferred to that slice). No new iconography is decided here.
- Depreciating assets (cost, date, effective life, method, work-use share, low-value pool D6,
  disposal, sole trader treatment), home office hours, car kilometres: each a Record type in its own
  slice, in or out of scope as the maintainer confirms then.
- The exact `TaxResult` fields, error messages, API shapes, media-type and size limits for evidence,
  the evidence health check, the saved-estimate list, and break-even's search bounds.
- Prefill from Income streams and investments; novated lease and fringe benefit prediction;
  the return worksheet; tax into the projection; Division 293 and the line-debit mechanism.

## Out of scope

Cover records, declining employer death or TPD benefit, cover versus target, and contingency runs
(the obligations and insurance design's separate ADR; nothing here blocks them, and the line-debit mechanism is shared). Lodging with the
ATO; whether tax guidance goes beyond modelling (left open); foreign income conversion rules;
estimate retention and deletion; reading Rule data from the database (see ADR-0022).

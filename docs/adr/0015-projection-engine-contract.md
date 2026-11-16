# 0015. Projection engine contract

**Status:** Proposed
**Date:** 2026-10-25
**Issue:** — (epic: M3 First projection)

## Context

Every M3 slice runs on one engine: the first projection (B1), sustainable income (B2), Scenarios
and saved runs (A2), Income streams and Life events (A3), and the Age Pension (A4, B10 to B12).
ADR-0004 fixed the principle: the engine is pure functions over immutable inputs. ADR-0002 and
ADR-0009 fixed exact money: integer minor units, rounding once, half to even, only in `core/money`.
Requirements §5a and §5d add annual steps, today's dollars by default with inflation as an
Assumption, deterministic results in M3, and saved runs that record their exact inputs and the
engine version.

This ADR fixes the engine's contract: what goes in, what comes out, one year's steps, and the
arithmetic. It deliberately does not cover storage or how Assumptions are layered.

## Decision

### 1. Placement and signature

- `capabilities/projection/` is a new Capability:
  - `input.py`: the Projection input, its validation and its serialisation;
  - `engine.py`: the engine;
  - `service.py`: builds a Projection input from the Position and the request, then calls the
    engine.
- The engine is one pure function:

  ```python
  ENGINE_VERSION = 1

  def project(projection_input: ProjectionInput) -> ProjectionResult: ...
  ```

  It does no I/O and reads no clock, database, settings or Rule pack. Every input is a frozen
  dataclass made of tuples. The same input always gives an equal result.
- **Boundaries (import-linter):**
  - a layers contract `projection > position > household`;
  - a forbidden contract, as for Importers: `projection.engine` and `projection.input` must not
    import `core.db`, `core.models`, `core.audit`, `projection.service` or any other Capability.
  - Only `service.py` touches the Position service. `api/projection.py` calls only the service.

### 2. The Projection input (versioned, serialisable)

The Projection input is **fully resolved**: Assumptions have already been resolved into concrete
values per line before the engine sees them. Layering (A2), Life events and Income streams (A3)
and Rule packs (A4) all happen in the service *before* the engine runs. The engine never knows
where a value came from. It is this resolved value that a saved run snapshots (A2 decides where).

`ProjectionInput` (`INPUT_VERSION = 1`):

| Field | Meaning |
|---|---|
| `input_version` | `int`, equal to `INPUT_VERSION` |
| `as_at` | the Position date. Year 0 starts on it. "Today's dollars" means dollars of this date |
| `currency` | the Base currency (`AUD`). Every Money in the input must be in it |
| `members` | `(member_id, born_on)` per Member, used for ages and the reference Member |
| `years` | the number of annual steps (1 to 120) |
| `inflation` | a `Rate` |
| `lines` | the balances (see below), unique on `(member_id, category_key)` |
| `inflows` | `(member_id, category_key, amount, from_year, until_year)`. Credited to that line |
| `outflows` | `(amount, from_year, until_year)`. The household's spending need |
| `drawdown_order` | the category keys, in the order they fund outflows |

- A **line** is `(member_id, category_key, start: Money, return_rate: Rate, accessible_from_year)`.
  - Lines carry no Account ids. They hold the Position by Member and Category.
  - If finer granularity is needed later, such as Account overrides, that is a new input version,
    not a new engine.
- **Year ranges are half-open:** `[from_year, until_year)`. A null `until_year` means the end of
  the horizon. Every amount is a positive annual Money in today's dollars.
- **Serialisation is canonical JSON**, using existing conventions:
  - money as `{minor, ccy}` (ADR-0009 section 5);
  - rates as decimal strings;
  - dates in ISO format;
  - keys sorted, and tuples kept in a canonical order (lines by drawdown position, then
    `member_id`).
  - A float anywhere in a payload is rejected. `from_json(to_json(x)) == x` exactly.
- **Versioning:** bump `INPUT_VERSION` when a field is added, removed or changes meaning. Old
  versions stay readable: `input.py` keeps one pure upcaster per past version, so a saved run's
  snapshot always loads.

### 3. Engine version

- Bump `ENGINE_VERSION` (an integer) whenever **the same Projection input could give a different
  result**. That covers changes to step order, timing, rounding, drawdown allocation, the
  definition of an output, or a bug fix that moves a figure.
- Do not bump it for refactors that leave every result identical.
- **Enforcement:** golden test files record the `engine_version` they were computed with, and a
  test asserts it equals `ENGINE_VERSION`. Changing a golden answer therefore forces a deliberate
  bump in the same PR.
- **Only the current engine is kept.** An old run is explained by its stored result plus its
  version, not by re-running an old engine.

### 4. Rates and rounding: no floats anywhere

- **`Rate`** (`core/rate.py`, so A2's Assumption columns and Rule packs can share it) is a frozen
  value: an integer count of **parts per million** of 1, so 6.5% is `65_000`.
  - It is built from a percent decimal string or a `Decimal` with at most 4 decimal places of
    percent ("6.5", "-2.25"). Finer precision is rejected, never rounded, as in `parse_amount`.
  - A float raises.
  - It serialises back to the percent string.
  - The value must be greater than -100%. Inputs are capped at ±100%.
  - A2 stores it as `ScaledDecimal(4)` of percent, or as the integer, whichever A2 prefers. The
    value is the same either way.
- **Arithmetic is integer-only.** `Decimal` is used only to parse. There is no `Decimal` context
  arithmetic, because it silently rounds at 28 digits, and no `Fraction` held across steps.
- **One rounding helper**, added to `core/money` (ADR-0009: nowhere else rounds):
  `scale(money, numerator, denominator) -> Money`. It computes `round_div(amount × numerator,
  denominator)`, half to even.
- **Growth for one line for one year** is a single `scale` call, so it rounds once:

  ```
  numerator = 1_000_000 + return_ppm
  denominator = 1_000_000 + inflation_ppm
  ```

  This is the exact real factor `(1 + r) / (1 + i)`, not the approximation `r - i`. Each line's
  balance is integer Money between years. The rounding error is at most half a minor unit per line
  per year. Since `start >= 0` and both factors are positive, growth can never make a balance
  negative.
- A Money overflow (beyond int64) raises `MoneyError`, which the API reports as a validation error.
  It never wraps.

### 5. Real versus nominal

- **The engine computes in real terms (today's dollars).**
  - Return Assumptions are entered **nominal** and turned into a real factor using inflation, as in
    section 4.
  - Inflows and outflows are today's-dollar amounts, held constant in real terms, which means they
    are indexed to inflation.
- **The nominal view is presentation only.** When it is built, it will be a pure function over the
  result and the inflation `Rate`: `nominal_t = scale(real_t, (10^6 + i)^(t+1), 10^(6(t+1)))`,
  rounded once per figure. It is never a second engine path.
- A later stream that is frozen in nominal terms, such as a foreign State Pension, enters as a
  real amount the service has deflated. That is A3's concern.
- **B1:** real only, no toggle. Inflation is entered on the screen and is used only to form the
  real factor.

### 6. One annual step (year t, flows at the end of the year)

1. **Growth.** Each line's opening balance becomes `scale(balance, 10^6 + r, 10^6 + i)`. Growth is
   on the opening balance only.
2. **Cash flows.** Every inflow active in year t is credited to its line. The year's **need** is
   the sum of the active outflows.
3. **Withdrawals.**
   - The need is met from **accessible** lines (`t >= accessible_from_year`), category by category
     in `drawdown_order`.
   - Within a category, the amount is split across Members' lines **in proportion to their
     balances**, using `core.money.allocate` with largest remainder, so no line goes below zero.
   - If the accessible balances cannot cover the need, all of them are withdrawn. The unmet
     remainder is that year's **shortfall**, and is never borrowed. Projection continues to the
     horizon.

Timing is end of year: flows earn no growth in the year they occur. A mid-year convention would
bump the engine version.

### 7. Outputs

`ProjectionResult` contains:

- `engine_version` and `input_version`;
- one row per year, holding:
  - `t`;
  - the date the year starts;
  - each Member's age at that start;
  - per line: opening balance, growth, credited, withdrawn and closing balance;
  - the need and the shortfall;
- the headline fields described below.

**Exact identities:**

- `closing = opening + growth + credited - withdrawn` for every line;
- `withdrawn total + shortfall = need` for every year.

Category and Household totals are plain sums of lines.

- **Reference Member:** the youngest Member (latest `born_on`; a tie goes to the lowest id). Ages
  are whole years on the anniversary of `as_at`.
- **`funds_last_to_age`:** the reference Member's age at the start of the **first year with a
  shortfall**. Spending is met in full up to that age. If no year in the horizon has a shortfall,
  the value is null and **`lasts_beyond_horizon`** is true.
- **Sustainable income (B2)** needs only what this contract already gives: the engine is cheap
  (lines × years, integer arithmetic) and deterministic, so the service can bisect over the
  outflow amount in minor units. No design is needed here. Monotonicity is B2's property test.

### 8. B1: how the first slice uses the contract

- **Starting Position:** `position_on(session, as_at)` (default: today in the Household's time zone).
  - For each projected Category, each Member's total (held solely plus their joint share) becomes
    that Member's line for the Category. These come from Position's own allocation, so they already
    sum exactly.
  - Accounts that Position flags (no Valuation, no FX rate) stay out, as they do in Position, and
    the screen lists them.
- **Projected Categories:** `cash`, `investments`, `superannuation`, `overseas_pensions`. Property,
  vehicles and liabilities are not projected (a stated simplification). The service creates a
  zero line wherever a Member has no balance in a Category, so inflows always have a target line.
- **Fixed default drawdown order:** `cash`, `investments`, `superannuation`, `overseas_pensions`.
  This is a constant in the service. The Drawdown policy (B9) will later resolve into the same
  `drawdown_order` field.
- **Access:** cash and investments are accessible from year 0. Super and overseas pension lines are
  accessible from the owning Member's retirement year (a stated simplification until B6 brings in
  preservation age).
- **Years:**
  - a Member's retirement year is `max(0, retirement_age - age at as_at)`;
  - `years` is the horizon age minus the reference Member's age at `as_at`.
- **Cash flows:**
  - The optional saving is an inflow from year 0 until the **first** Member's retirement year. It
    is split equally across Members with `allocate`, into their Investments lines.
  - Spending is an outflow from the year **all** Members are retired.
  - In between, there is neither saving nor drawdown: the remaining salary is assumed to cover
    spending.

**B1 simplifications (accepted by the maintainer, 2026-10-25).** The screen states each one.

1. **Phasing between the two retirements.** Saving stops when the first Member retires, and
   spending starts only when all Members are retired. In between, nothing flows. B7 replaces this
   with the salary Income stream, under A3's design.
2. **Saving is split equally across Members into their Investments lines.** A later slice, which
   adds contributions with a target, replaces this.
3. **The reference Member is the youngest.** This Member fixes the horizon and the
   "funds last to age N" headline.
4. **Lines are per Member and Category, not per Account.** Account-level Assumption overrides would
   need a new input version.

### 9. Invariants that get property tests (Hypothesis)

- No balance is ever negative, in any line or year, for any valid input.
- The two exact identities in section 7 always hold.
- Determinism: `project(x) == project(x)`, and `project(from_json(to_json(x))) == project(x)`.
- The Projection input round-trips exactly, and a float anywhere in a payload is rejected.
- `Rate` round-trips through its percent string. Parsing rejects anything finer than 4 decimal
  places of percent.
- With a zero real rate (return equal to inflation) and no flows, every balance is unchanged
  exactly.

**Golden tests (B1):**

- a flat-return case worked by hand year by year;
- a hand-checked depletion age;
- an inaccessible super line that becomes drawable in its owner's retirement year.

## Consequences

- One engine serves every later slice. A3 and A4 add calculated flows (the Age Pension
  means-tested on projected assets) as **new step logic plus new input fields**, which means
  bumping both versions. They do not create a second engine.
- Saved runs (A2) become a matter of storing the canonical JSON input, the result, and the two
  versions. Reproducing a run never depends on the live Assumptions.
- Working in real terms makes the depletion age independent of display choices. The cost is that
  every non-indexed amount must be deflated before it enters the engine.
- Per-year rounding in each line keeps golden answers easy to check by hand, at a bounded cost of
  half a cent per line per year.
- Forecloses:
  - floats or `Decimal` arithmetic in projection;
  - the engine reading the database or Rule packs directly;
  - Assumption layering inside the engine;
  - storing a nominal series as a separate computation;
  - keeping old engine versions runnable.

## Out of scope

- **Scenario and Assumption storage and layering**, seeding Best/Likely/Worst, and the saved-run
  record: A2.
- **Income streams and Life events**, beyond B1's retirement-age arithmetic: A3.
- **Rule packs** (preservation age, Age Pension) and how their version is recorded: A4.
- Monte Carlo, monthly steps, the nominal toggle, configurable Drawdown policy (B9), tax, fees and
  contributions tax.

## Not decided here / routine follow-on (for B1)

- Exact dataclass field names, error messages, and the API request and response shapes. The
  request takes percent strings and amounts as decimal strings, never JSON numbers. Then
  regenerate the API client.
- Mapping `MoneyError` and input validation failures to 422 responses.
- Leap-day birthdays in the age arithmetic, which needs a documented rule and a test.
- The Projection screen: a "funds last to age N" headline, the yearly table or chart by Category,
  the stated-simplifications panel, and the flagged-Accounts list. It is a new page and nav entry
  under ADR-0014's patterns. **No new iconography**: it uses a lucide icon.
- The two import-linter contracts in section 1.
- Checking that `fixtures/sample_household/` has Members with dates of birth and investable
  Accounts in each projected Category, and extending it if not.
- The `core.money.scale` helper and `core/rate.py`, each with its own unit tests.

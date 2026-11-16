# 0025. Super statement: an Account's year, recorded under Accounts

**Status:** Accepted
**Date:** 2026-11-07
**Issue:** — (part of the M5 Super import epic; read by the Division 293 prediction and the super-returns slice)

## Context

The Super insights plan (the M5 Super import epic) makes the fund's **annual statement** the main source: balance roll, outflows,
contributions received and option values at 30 June. One slice records it by form; a later slice fills that form from the
fund's PDF statement (ADR-0024); Division 293 prediction and the returns-against-the-plan slice read it.

Two homes were weighed. A new **Tax record type** (ADR-0019) is a Member's dated entry in the tax register,
and the tax calculation sees only Label totals; the statement is mostly not tax (a balance roll, units and
prices), so it would be a payload the tax capability only partly understands, with no Account reference,
and the M4 option mix would then have to reach into tax. A statement describes one **Account's year**,
which is what Accounts and Valuations already model (ADR-0009, ADR-0013).

## Decision

### 1. Home and dependency direction

A **Super statement** is a per-Account, per-financial-year record in the **position** capability
(`capabilities/position/super_statements.py`, tables in `core/models/super_statement.py`), beside
Valuations. Capability layering is `projection > tax > position > household` (enforced), so **tax and
projection read it through position's service functions; position never imports tax.** The Division 293 prediction needs
concessional contributions received and Division 293 released by year; the returns slice needs the balance roll by year.
Position exposes these as frozen dataclasses (`SuperYear`) from one read function. No Label, Tax record or
Record type is added, and the income statement is untouched.

It attaches to an Account of the super fund Account type (`super_fund_account`). Any other Account is
refused.

### 2. Shape

One **current** statement per Account and financial year (`period_start` 1 July, `period_end` 30 June; the
year is derived, as for Tax records). Money is the shared Money type in the Account's currency; units and
unit prices use `ScaledDecimal` (as Transactions do); no floats.

- **Required (the balance roll):** opening, contributions in, investment returns, withdrawals, closing. A
  basic fund statement has only these.
- **Optional outflow breakdown** (`NULL` is "not stated", distinct from zero): contributions tax; fees before
  the tax rebate; the rebate; fees taken inside the investment; Division 293 released; other withdrawals;
  insurance premiums.
- **Optional contributions received:** concessional, non-concessional.
- **Optional option lines** (child rows: option name, units, unit price, value). The value is stored as
  stated, never recomputed from units x price.

Provenance is the owner entering it; a PDF read through ADR-0024 attaches as Evidence, reusing the existing
Evidence store. Writes are audited.

### 3. Checks: warn, and tick to save

Consistent with ADR-0024, a failed check does not block. It is shown beside the figures involved
and the owner ticks that they have checked it; the ticked check names are stored on the statement.

- **Roll:** opening + in + returns - withdrawals = closing, to the cent.
- **Outflows,** when any is given: the parts (the rebate subtracts) equal withdrawals, to the cent. Fees
  inside the investment are stated but excluded from this sum, as they are already netted from returns.
- **Options,** when any are given: values sum to closing within one cent per option line.

Only the malformed blocks: an unparseable amount, negative units, a period that is not one financial year,
a second current statement for the same Account and year, or a non-super Account.

### 4. Valuation and corrections

Saving, in one transaction, adds a **Valuation** at `period_end` from the closing balance, source
`statement`, with a provenance note naming the statement. The statement holds its Valuation id.

A **correction supersedes** the old statement (`SupersessionMixin`; the old row and its option lines stay
retrievable) and calls `correct_valuation`, so the Valuation is superseded with it. Removing a statement
supersedes its Valuation likewise. A manual Valuation on the same date is untouched; the selector already
ranks `statement` first.

### 5. Option mix

Option lines at 30 June are recorded now as the household's option mix for that date. **M4 reads them later;
this ADR designs no look-through and no option catalogue.** Option names are free text; mapping a name to an
Instrument or an underlying allocation is M4's.

## Consequences

- Division 293, the concessional cap check and returns get one source with no tax-side coupling, and the
  pure tax engine is unchanged.
- A second per-year Account record sits beside Valuations; supersession and the Valuation write path are
  reused, not copied. A statement and its Valuation are written together by one service function.
- A richer statement from another fund later needs only a Document profile filling the same form.
- Foreclosed: the statement as a Tax record, and recomputing option values from units x price.

## Not decided here / routine follow-on

For the Developer: migration and models; API, the form (check warnings and tick) and the regenerated client;
the fixture (an invented rich statement with options for one Member's super Account, an
invented roll-only one for the other); the Guide page for the area and the "What to provide, when" schedule;
tests (the three checks, supersession together with its Valuation).

Non-goals: PDF reading, returns, Division 293, any transactions import, personal
details, beneficiaries, insurance cover amounts, other funds' profiles, and M4 look-through.

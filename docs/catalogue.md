# Category & Account type catalogue

The **seed catalogue** for M1: the default Categories, Account types (each referencing a Behaviour)
and Income stream types. It comes from the M0 Financial inventory, generalised so that **nothing
here identifies the household**. The inventory itself stays private.

Categories and Account types are **data** (seeded, user-editable). Behaviours are **code**
(ADR-0004). In M1 every Account type runs on the *simple balance* Behaviour; the "Target
Behaviour" column shows where each one is heading.

## Categories

| Category | Class | Holds |
|---|---|---|
| Cash | Asset | Transaction, savings and offset accounts |
| Investments | Asset | Brokerage accounts holding listed Instruments |
| Superannuation | Asset | Australian super fund accounts |
| Overseas pensions | Asset | Foreign defined-contribution pension pots (e.g. a foreign personal pension) |
| Property | Asset | Real property, including the principal residence |
| Vehicles | Asset | Owned vehicles (depreciating) |
| Loans & credit | Liability | Home loans, credit cards, other borrowing |

Income stream entitlements (Age Pension, foreign State Pensions, defined-benefit pensions) are
**not Categories**. Income streams are siblings of Accounts and don't sit on the balance sheet.

## Account types

| Account type | Category | M1 Behaviour | Target Behaviour | Valuation source | Notes |
|---|---|---|---|---|---|
| Transaction account | Cash | simple balance | simple balance | manual, then bank import | Small, fluctuating balances. Hubs for transfers between Household Accounts |
| Savings account | Cash | simple balance | simple balance | manual, then bank import | May be earmarked (e.g. for a tax bill); see open questions |
| Offset account | Cash | simple balance | offset-linked | manual, then bank import | Linked to one home loan; reduces its interest. 100% offset is typical |
| Brokerage account | Investments | simple balance | holdings-based | derived (M2) | Multiple Streams per Account (trade + cash). Cash portion is part of the Account's value |
| Brokerage account (holdings) | Investments | holdings based | holdings-based | derived (M2) | Added by catalogue version 2. An Account is switched to it by an explicit, audited edit (ADR-0013 section 4); the plain Brokerage account stays simple balance |
| Super fund account | Superannuation | simple balance | super | statement/manual, then import (M5) | Per Member. Holds one or more **investment options** with balances. Options may be **age-based (lifecycle glide path)** |
| Overseas DC pension | Overseas pensions | simple balance | holdings-based | manual/screenshot | Unit-linked: units × provider unit price (an unlisted Instrument). Foreign currency. May carry guarantees or transfer conditions |
| Principal residence | Property | simple balance | simple balance | estimate | Exempt from the Age Pension assets test. Refreshed occasionally from a recorded source |
| Vehicle | Vehicles | simple balance | simple balance | estimate | Depreciation is an Assumption |
| Home loan | Loans & credit | simple balance | amortising loan | manual, then lender import | A "mortgage" may be several loan Accounts (e.g. construction-era splits). Has **dated terms** and **redraw** (see below) |
| Credit card | Loans & credit | simple balance | simple balance | manual, then bank import | Limit and rate as attributes |
| Investment property | Property | simple balance | simple balance | estimate | Added by catalogue version 3. A Property that is not the principal home: counted by the Age Pension assets test (ADR-0029). Rent is not yet an Income stream (#76) |

## Income stream types

| Income stream type | Amount comes from | Indexation | Notes |
|---|---|---|---|
| Salary | Entered (gross; packaging aware later) | Assumption (wage growth) | Drives employer super contributions. Stops at a retirement Life event |
| Self-employment income | Entered | Assumption | May generate a tax provision |
| Defined-benefit pension | Stated by the scheme | **Per scheme rules** (e.g. annual revaluation) | May carry a **reversionary (survivor) amount**. Payable from the scheme's normal pension age |
| Foreign State Pension | Calculated: Rule pack + Member facts + Scenario | **Per Rule pack**, e.g. frozen when resident in some countries | Future, Scenario-based; eligibility may change (policy risk) |
| Age Pension | Calculated: AU Rule pack + Household position + Scenario | Per Rule pack | Couple means test (assets and income). Future, Scenario-based (policy risk) |

## Not yet settled

These need design before they're modelled. They're tracked as a `needs-design`
issue and are **not** glossary terms yet (see `CONTEXT.md`, "Open questions").

- **Obligations that aren't loans:** e.g. a **novated lease**. The vehicle belongs to the lessor
  while leased. Repayments reduce take-home pay, and a **residual (balloon)** falls due at lease
  end.
- **Insurance policies:** contingent payouts (life, TPD) relevant only to "death or disability of a
  Member" scenarios. Premiums are cash flow. Cover may decline with age.
- **Provisions:** money earmarked for a known future liability, e.g. tax on self-employment income.
  A Tag covers this in M1.
- **Expected inheritance:** a Scenario-varied **Life event** (uncertain timing and amount), owned by
  the receiving Member. Not an Account.
- **Dated loan terms:** rate and repayment changes are announced on one date and effective on
  another. These are a time series per loan, like Valuations.
- **Redraw:** repayments made ahead of schedule, already reflected in the loan balance. It is
  **available liquidity, not an asset**; counting it as one double-counts.
- **Contributions tax drag:** e.g. Division 293 tax on concessional contributions for higher
  incomes, sometimes paid from the super balance.
- **Policy risk:** Scenarios override entitlement rules (e.g. a tighter means test, a higher pension
  age, an unfrozen foreign State Pension).

## Input formats seen

| Format | Example sources | Path |
|---|---|---|
| Structured CSV (multiple Streams) | Brokerage exports | Deterministic Importer |
| Structured CSV (single Stream) | Bank and lender exports | Deterministic Importer (generic bank CSV) |
| On-screen data where exports fail | Super fund portal, overseas pension portal | Screenshot → AI-assisted extraction → staged diff |
| Scanned letters and PDF statements | Defined-benefit scheme letters, fund statements | AI-assisted extraction, or manual entry for one-off figures |

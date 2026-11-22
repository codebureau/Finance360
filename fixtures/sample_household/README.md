# Synthetic sample household

Invented data only, never real. `scripts/review.py` copies everything in this directory (except this
README and `.gitkeep`) into the review slot's data directory on first start and on `review --reset`.

Fixture files are plain, non-database files (`.gitignore` blocks `*.sqlite`/`*.db`). The app loads
them through the service layer on first run, so audit and validation apply:

- `household.json`: the Household name, time zone and Effort level (`steady`, so every What's due rule
  has something to show; the app's own default is Light), and three invented Members (name, date of
  birth). Its `facts.principal_home` names the Account ("Family Home") that is the principal home, set
  through the service by `load_fixture_facts` on the run that creates the Accounts (so a later start never
  undoes an owner's edit); couple status and homeowner are left blank, to show their labelled defaults. The
  non-home Account for the means test is the invented "Family Car" (a Vehicle with Valuations). Read by
  `finance360.capabilities.household.service.bootstrap` only while no Household exists yet. Its
  Members are audited with source `fixture`. Its `accounts` (nine invented Accounts across the
  Categories, one joint, one in GBP, with Tags; owners are named by Member name) are read by
  `finance360.capabilities.position.accounts.load_fixture_accounts` only while no Account exists.
  Each Account's `valuations` (about 24 months, Oct 2024 to Sep 2026, in the Account's currency:
  monthly or irregular, with gaps, mixed sources; the home has a manual and an estimate on one date;
  the home loan holds the amount owed as a positive figure) are read by
  `finance360.capabilities.position.valuations.load_fixture_valuations` only while no Valuation
  exists, so an existing review database picks them up on its next start. They are audited with
  source `fixture`.
  One Account is deliberately short: `Sample Term Deposit (Sample Bank)` (Jordan, cash) is opened
  2025-01-01 but first valued 2025-03-15 (three Valuations: 10000.00, then 10150.00 on 2025-09-30 and
  10620.50 on 2026-09-30). The Jan and Feb 2025 month-ends therefore show one flagged Account, which
  the Position history chart draws as a hollow, "incomplete" point. It has no Tag.
  Three Accounts exist for What's due (ADR-0020): `Old savings (Sample Bank)` (cash, last valued
  2025-11-30), `Old super fund (Sample Super)` (last valued 2026-03-31) and `Holiday unit (Sample)`
  (property, last valued 2025-06-30). Each is old enough to be asked for at Steady, and the first two
  at Light. `Sam Sample` has an income statement for FY 2024-25 and none for FY 2025-26, so What's due
  asks for it from 15 July 2026 (it asks about the latest ended financial year, so as time passes
  every Member without a newer statement is asked).
  The top-level `fx_rates` (six invented GBP to AUD rates, Oct 2024 to Aug 2026, some with more than
  two decimal places) are read by `finance360.capabilities.reference.fx_rates.load_fixture_fx_rates`
  only while no FX rate exists. They are audited with source `fixture`.
  The top-level `instruments` (three invented Instruments: `Sample Broad Market ETF`, `Sample Mining
  Ltd` and the unlisted `Sample Balanced Growth Fund`, each with invented ticker, yahoo, ISIN,
  vendor-id and product-name-alias identifiers, all AUD) are read by
  `finance360.capabilities.reference.instruments.load_fixture_instruments` only while no Instrument
  exists. They are audited with source `fixture`. The fund has no ticker and no provider symbol, as
  an unlisted fund would not.
  The top-level `income_streams` (an invented salary for each Member: Alex 120,000.00 gross and
  91,000.00 take-home, Jordan 85,000.00 gross and 66,000.00 take-home; both end at the owner's
  retirement) are read by
  `finance360.capabilities.projection.income_streams.load_fixture_income_streams` only while no
  income stream exists. They are audited with source `fixture`. The take-home figures are invented,
  not computed from any tax table.
  The top-level `tax_records` (invented Tax records for both Members in FY 2025-26, which the AU
  Rule pack holds tax figures for. An `income_statement` entry carries a `statement` object of amounts in place of `label` and `amount`: an income statement for each (Alex: 120,000.00 gross pay, 14,400.00 employer super, 28,500.00 tax withheld, 6,000.00 of novated lease costs, 1,200.00 of other deductions, 84,100.00 take-home, 9,000.00 of reportable fringe benefits and 600.00 of insurance premiums paid from super, so 200.00 is unreconciled. Alex's FY 2025-26 statement also carries the ATO breakdown: employer "Sample Employer", 108,000.00 gross amount and 12,000.00 of bonuses and commissions, and 5,000.00 of salary sacrifice (type "Novated lease"). Alex has an FY 2024-25 statement for the same employer, so opening the FY 2025-26 one shows "Compared with last year": a bonus of 8,000.00 (up 4,000.00) and 3,000.00 of salary sacrifice (up 2,000.00), gross pay up 8,000.00. The FY 2024-25 statement reconciles to the cent. Jordan: employer "Sample Employer", 85,000.00 gross pay, 10,200.00 employer super and 1,800.00 reportable employer super (salary sacrifice; Jordan has no Super statement for FY 2025-26, so Division 293 counts these 12,000.00 as low tax contributions from the Income statement, while Alex's come from his Super statement, which wins over his Income statement), 19,400.00 tax withheld, 66,000.00 take-home, so Unreconciled is negative 400.00), Alex's 111,621.00 of consulting income (label `other_income`), which lifts Alex above the Division 293 threshold for FY 2025-26 (Division 293 income 258,000.00 = taxable income 234,600.00 + fringe benefits 9,000.00 + the 14,400.00 of concessional contributions on the Super statement; 8,000.00 over the threshold, less than the contributions, so Division 293 tax is 1,200.00; Jordan's is nil), Alex's interest, a donation and
  a home internet cost with a 40% work-use share, Jordan's franked dividends and franking credits, Jordan's 50.00 of foreign source income (20E/20M) with a 6.00 foreign income tax offset (20O), well under the simple cap so it is claimed in full,
  a 640.00 tax agent fee entered as each Member's 320.00 share, and Alex's `help_debt` fact; plus
  one work laptop of Jordan's dated in FY 2024-25 to show a record outside the year; and two
  `depreciating_asset` entries, with `amount` as the cost plus `effective_life_years`, an optional
  `work_use_percent` and `claim_in_full`: Alex's 2,000.00 camera on a 4-year life, started on
  1 January 2026 (181 days of FY 2025-26) and still used, so it feeds Business expenses in FY
  2025-26 and in every later year, and Jordan's 800.00 standing desk at 50% work use, claimed in
  full in FY 2025-26 as Other work-related expenses; 6,000.00 of Alex's sole trader income,
  so the camera's 495.89 for FY 2025-26 reduces a business rather than making a deferred loss; and
  two `home_office_hours` entries (each with an `arrangement` and a `months` list, both dates in one
  financial year): Alex's `regular` one, with `hours_per_day` and `home_weekdays` (Mon, Tue, Thu,
  Fri at home) and twelve months from July 2025 to June 2026 at 7.60 hours a day, each month the
  calendar prefill (working days = weekdays, office days = Wednesdays) except August and December,
  which have leave typed over it, and April, which has an extra office day (195 home days, so
  1,482.00 hours, feeding Business expenses at the Rule pack's rate per hour); and Jordan's
  `casual` one, whose months are `{month, hours}` (no hours per day, no weekdays): September to
  December 2025 at 18.5, 22, 15.25 and 9.75 hours, 65.50 hours in all, feeding Other work-related
  expenses; and three `car_kilometres` entries, each with a `trips` list of `{description, km,
  times}` (text; `km` has at most one decimal place, `times` is a whole number, and an optional `on`
  date falls within the record's dates), both dates in FY 2025-26: Alex's five trips (42.0 km x 12,
  18.5 km x 8, 6.2 km x 20, 31.0 km x 3 on 18 October 2025 and 96.4 km x 1 on 7 March 2026, so
  504.0 + 148.0 + 124.0 + 93.0 + 96.4 = 965.4 km) feeding Business expenses, and Jordan's three (27.5
  km x 10, 64.0 km x 6 and 112.8 km x 1 on 12 November 2025, so 275.0 + 384.0 + 112.8 = 771.8 km)
  feeding Work-related car expenses (item D1); both are below the Rule pack's limit per car) are
  read by `finance360.capabilities.tax.service.load_fixture_tax_records` only while no Tax record
  exists. The camera uses the Rule pack's `tax.depreciation.dv_percent` for FY 2025-26 (200).
  The home office hours use `tax.home_office.rate_per_hour` for FY 2025-26 (0.70 an hour), and the
  car kilometres `tax.car.rate_per_km` (0.88 a km) and `tax.car.max_km` (5,000 km): Alex's car is
  849.55 and Jordan's 679.18. A record for a year whose figures are not captured shows Figure needed
  and an estimate including its Member refuses, naming it. Each Member has one car kilometres
  record a year, so adding another for FY 2025-26 is refused. A third car record, Jordan's for
  FY 2026-27 (40.0 km x 5 = 200.0 km), reads a year the release holds no car figures for: with only
  the captured rate (below) it waits for the kilometre limit, so What's due shows "Not captured" for it.
  They are audited with source `fixture`.
  The top-level `library_documents` (one tiny invented text savings statement,
  `documents/sample_savings_statement.txt`, linked to nothing) is read by
  `finance360.capabilities.library.service.load_fixture_documents` only while the Library is empty, before the
  receipt below. It gives the Library screen an **unlinked** document, so the "Unlinked" filter and Delete have
  something to show, and its words ("Interest paid") are searchable. Audited with source `fixture`.
  The top-level `tax_evidence` (one tiny invented text receipt, `evidence/sample_donation_receipt.txt`,
  attached to Alex's donation record; `member` and `record` name the Tax record by Member and
  description) is read by `finance360.capabilities.tax.service.load_fixture_tax_evidence` only
  while no Document link exists yet. The loader adds the file to the Library (stored under the instance's
  `documents/library/` by its SHA-256, exactly as an upload does) and links it to that Tax record; the
  copy in `evidence/` is only the fixture's source. Audited with source `fixture`. (Before the
  Library the file lived under `documents/tax/`; the start-up step moves any such file, so an
  instance populated by the previous release shows the same receipt after an upgrade.)
  The top-level `document_profiles` (one **Proposed profile**, `documents/ato_income_statement_variant_profile.json`:
  a copy of the shipped ATO income statement profile under the invented key
  `ato-income-statement-sample-variant`, source `pasted`) is read by
  `finance360.capabilities.library.profiles.load_fixture_document_profiles` only while no Document profile is saved. It
  adds the sample ATO PDF below to the Library (unlinked, as an upload would) as the profile's test document and
  saves the profile through the real test, so Settings -> Document profiles has a profile to review beside the
  document and activate (which warns that the shipped profile also claims the document). Audited with source `fixture`.
  `documents/ato_income_statement_sample.pdf` is also the file a reviewer
  chooses under "Read from the ATO PDF" on the income statement form. It is an invented ATO income
  statement (a "Sample Employer Pty Ltd" in the ATO layout, two pages, all values made up: Total gross
  amount 84,500.00 = Gross amount 80,000.00 + Bonuses and commissions 5,000.00 + Paid leave amount
  2,500.00 + Salary sacrifice amount -3,000.00; PAYGW amount 18,250.00; Employer superannuation
  contribution liability 10,140.00; for 2025-26). Generated by
  `scripts/make_ato_income_statement_fixtures.py`, which also writes the variants the tests read from
  `fixtures/documents/` (a missing PAYGW amount, a total that is 1.00 out, and a bank statement),
  plus the layouts a real print can take, all invented: `..._no_super_section.pdf` (total
  60,000.00, no employer super row), `..._wrapped_super_label.pdf` (super label wraps to a second
  line; total 41,000.00), `..._two_leave_rows.pdf` (Paid leave amount 1,500.00 and 2,250.00 add to
  3,750.00; total 73,750.00 = Gross amount 70,000.00 + 3,750.00), `..._short.pdf` (only a Total gross
  amount of 52,000.00) and `..._payer_a.pdf` / `..._payer_b.pdf` (two statements for one year, one
  per payer: totals 30,000.00 and 20,000.00). Choose any of them on the income statement form to
  see what is read.
  The top-level `rule_figures` (one **invented** captured Rule figure: the car rate per kilometre
  for FY 2026-27 at 0.95 a km, with an invented quote and an `example.com` page address; it is
  not the official rate, and the shipped Rule pack holds no FY 2026-27 car rate) is read by
  `finance360.core.rulepack_store.load_fixture_rule_figures` only while no captured figure exists.
  It shows the Captured state on the Rule figures screen and in a FY 2026-27 record's trace. The
  loader captures it through the real service, audited with source `fixture`. It changes no FY
  2025-26 figure.
  `rule_figures_carry.json` (a separate file, read in the same pass and only with `rule_figures`, so
  a test that loads only `household.json` sees no carried year) starts
  **FY 2027-28** from the earlier years through the real "Start from" service: every figure the
  release or the fixture holds for an earlier year is carried forward as a provisional figure (the
  car rate from the fixture's FY 2026-27 capture, the rest from FY 2025-26), and the keys listed in
  `confirm` (the five Low income tax offset figures and the Medicare levy rate) are then confirmed
  as unchanged. So FY 2027-28 shows some figures confirmed and about thirty still **Carried**, the
  Tax estimate for that year runs and lists those as provisional, and "Confirm unchanged" per page
  can be tried on the rest. The values are the shipped figures reused, not official 2027-28 ones.
  `tax_whatifs.json` (a separate file, so the household file's counts do not move, read by
  `finance360.capabilities.tax.saved.load_fixture_tax_whatifs` only while there is no Tax what-if and
  no saved Tax estimate) adds two invented **Tax what-ifs** for FY 2025-26 and **one saved base
  estimate**, all created through the real service and audited with source `fixture`. "Alex buys a
  work laptop" adds a hypothetical depreciating asset (cost 2,400.00, three year life, 80% for work,
  bought 10 November 2025) to Alex's work-related expenses. "Jordan earns more, no donation" states
  Jordan's salary total as 95,000.00 and leaves Alex's donation record out. The saved estimate,
  "Base, as the records stand", is the base calculated with the engine of the day (so it is never
  stale) and is the one to compare a what-if against. The load order is: Tax records first, then
  this file.
  `lodged_results.json` (a separate file, so the household file's counts do not move, read by
  `finance360.capabilities.tax.lodged.load_fixture_lodged_results` only while there is no Lodged
  result, through the real service and audited with source `fixture`) holds two invented notices of
  assessment for **Jordan Sample, FY 2025-26**, so the Return worksheet (Tax, "Return worksheet")
  has something to show. The earlier one (assessed 14 August 2026) is **superseded** by the amended
  one (assessed 9 October 2026), so the history shows both; the earlier notice has a taxable income
  of 85,606.00 and a net outcome of a 391.49 refund. The current notice is close to, but not equal
  to, the estimate of 85,106.00 taxable income, 1,702.12 Medicare levy, 19,292.51 total tax and a
  557.49 refund: taxable income, tax on taxable income, offsets, surcharge, HELP and credits match
  to the cent; the Medicare levy (1,702.07) and total tax (19,292.46) each differ by 0.05; net
  outcome (557.54 refund) is 0.05 off, so it is matched within the notice's rounding with the
  difference still shown; and the notice carries one line the engine has no figure for ("Sample
  processing adjustment", 25.00), listed as unmatched. Alex has no Lodged result, so the worksheet
  for Alex shows the estimate alone. The estimate figures are the engine's for the Tax records
  above, so a change to those records or to the Rule figures moves the differences. Load order: Tax
  records first, then this file.
  The top-level `assumptions` are read by
  `finance360.capabilities.projection.scenarios.load_fixture_assumptions` only while no Household
  default spending phase and no custom Scenario exists. `defaults` are Household default
  Assumptions layered over the seeded ones (annual saving 20,000.00, retirement age 60 for Alex and
  62 for Jordan; Members are named by name). `spending_phases` is the Household default spending
  plan: 100,000.00 a year from the projection start (the working years, which a salary requires),
  then 70,000.00 a year from the last retirement, then 55,000.00 a year from ten years after it.
  The annual saving of 20,000.00 is not applied to a Member with a salary stream, so with both
  salaries it has no effect. `scenarios` holds two custom
  Scenarios. The first, `Retire early, spend more`, has two overrides (Alex retires at 55, Investments return
  6.5%), its own whole spending list (110,000.00 a year from the projection start, then 85,000.00 a
  year from the last retirement; a Scenario's list replaces the default as a whole, so it needs its
  own working-years phase) and a
  non-default Drawdown order (Investments, Cash, Superannuation, Overseas pensions). The second,
  `Tax on, Alex's pay climbs`, switches on Tax in the projection (take-home pay is estimated, not
  the typed figure), grows Alex's pay 6% a year above inflation and states `div293_payer` `super`
  (Division 293 taken from Alex's own super balance a year later, a line debit; `super` is also what
  an unstated payer means, and `own` pays it from the household's money instead); it overrides no spending, so
  it uses the Household default plan. Best, Likely and Worst, the inflation, the returns and the
  horizon age come from the startup seeder (illustrative placeholders), not from this file. They are
  audited with source `fixture`. There are no saved runs (that is a later slice).

  Each Account entry may carry `identifiers` (kind, value, institution). `Sample Brokerage account`
  (the sample investment platform Account) holds one invented placeholder, `SAMPLE-PI-0001` (kind
  `vendor_account_id`, institution the sample platform's name), read by
  `finance360.capabilities.imports.account_identifiers.load_fixture_account_identifiers` only while
  no Account identifier exists. It is shown masked (`••• 0001`) everywhere and matches the account
  number in the sample exports below.

  The top-level `super_statements` (two invented Super statements, each naming its Account and the
  financial year it ends in) are read by
  `finance360.capabilities.position.super_statements.load_fixture_super_statements` only while no
  Super statement exists; each is created through the service, so its 30 June Valuation is recorded
  with it and both are audited with source `fixture`. Every check agrees in both, so neither needs a
  tick.
  - `Sample Super Fund` (Alex), FY 2025-26, is the rich one: opening 189,979.16 (the 30 June 2025
    Valuation), contributions in 17,400.00, returns 7,528.34, withdrawals 3,407.50, closing
    211,500.00. The outflow breakdown is contributions tax 2,160.00 + fees before rebate 1,030.00 -
    rebate 154.50 + Division 293 released 0.00 + insurance premiums 372.00 = 3,407.50 (other
    withdrawals are not stated, which is not zero; fees inside the investment, 640.00, are stated but
    not in that sum). Contributions received are 14,400.00 concessional and 3,000.00
    non-concessional. It has three Investment options (Growth, Balanced and High Growth, with units
    and unit prices) whose values add up to 211,500.01: one cent over the closing balance, inside the
    one-cent-per-option rounding tolerance, so the Options check shows "within rounding". Its 30 June
    2026 Valuation (211,500.00) sits between the 31 May and 31 July ones.
    It has no document attached: "How to test" attaches one (the savings statement already in the Library,
    or any PDF) to the statement or the Account.
  - `Dormant super fund (Sample Super)` (Jordan), FY 2024-25, is the basic one, balance roll only:
    70,190.00 + 2,350.00 + 410.00 - 72,950.00 = 0.00, the fund rolled out in full. Its closing
    balance and 30 June 2025 Valuation are zero, like the fund's existing Valuation, so no Position
    total or Position history point moves. It stays asked for by What's due.

- `rule_page_sample.txt`: an **invented** official-looking page (Medicare levy, FY 2026-27) to paste
  into "Read the page for me" on the Rule figures screen (choose the Medicare page; with no AI
  provider set, as in the review slot, Finance360 reads it by matching words on the page, labelled
  "Matched by words on the page"). It holds a figure that checks out, one that is announced but not
  yet law, one that is a large change from last year, one with a slipped digit (a threshold of
  $2,801,100, which fails the plausible range and so shows "Not found; enter it") and a signed-in
  banner with a Member's name (which only matters if an AI service reads the page: it is removed
  before the text is sent). Not loaded automatically; `review --reset` copies it into the review data directory.
  All figures are invented and are not the official ones.

- `imports/`: invented export files in the layouts of the supported investment platform Importer, named as the
  real exports are (`<cash|investment>_transactions_acc<number>_<from>_<to>.csv`; the file name
  carries the account number and the stated period) to add on the Imports page (Data >
  Imports > Add file). They are not loaded automatically; `review --reset` copies them into the
  review data directory, and they live in this directory in the repo. All values are invented.
  Below, the names are shortened to their kind and period.
  - `investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv` (trades): the trade
    Stream. Columns `Account number, Investment, Product ID, Product Type, Trade Date, Type,
    Unit Price, Quantity, Value, Brokerage`. Descending `dd-MMM-yyyy` dates, quantity unsigned (the
    Type column says Buy Trade or Sell Trade), `ETF` and `Share` product types. Five rows: two
    products in the Instrument roster (`SBME`, `SMPM`) and one outside it (`SNHX`, "Sample New
    Horizons ETF"), so staging flags one unmatched reference. The `SNHX` buy deliberately has no
    matching cash row.
  - `cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv` (cash): the cash Stream.
    Columns `Date, Type, Product Type, Product Name, Product ID, Units, Total`; signed totals;
    stated period 01-Jul-2026 to 30-Sep-2026. Deposits, interest and fees carry the Product ID
    `CASH`; buy and sell settlements exclude the brokerage fee, with a separate "Fees and Charges"
    row per trade; distributions carry the product *name* and no Product ID (they resolve through
    a name alias). 14 rows.
  - `cash_transactions_accSAMPLE-PI-0001_01-Sep-2026_05-Oct-2026.csv` (later cash): a later,
    overlapping, narrower cash export (01-Sep-2026 to 05-Oct-2026): the September rows again plus
    one new interest row.
    Commit order for the walkthrough: trades (matching `SNHX` first), then cash, then the later file.
    The cash file matches four of the five trades (settlement plus a separate fee row each); the
    `SNHX` buy has no cash row, so staging the cash file after committing the trades warns exactly
    once. No new fixture file is needed for commit: the committed state comes from running the steps.
  - `cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026 (1).csv` (corrected cash): the
    cash file above with the 01-Sep-2026 1,000.00 deposit dropped and the same stated period
    (01-Jul-2026 to 30-Sep-2026), 13 rows; the ` (1)` is what a browser adds to a repeat
    download. Staged after the original is committed, it shows 1 removed (that deposit), 13
    unchanged, 0 added.
  - `investment_transactions_accSAMPLE-PI-0001_01-Sep-2026_30-Sep-2026.csv` (sell only): a trade
    export holding a sell with no earlier buy (for Opening-position detection).
  - `investment_transactions_accSAMPLE-PI-9999_21-Sep-2026_21-Sep-2026.csv` (other account):
    carries an account number (`SAMPLE-PI-9999`) no Account holds, so the owner picks the Account
    (and may remember the reference).
  - `investment_transactions_accSAMPLE-KIDS-0001_03-Aug-2026_30-Sep-2026.csv` and
    `cash_transactions_accSAMPLE-KIDS-0001_01-Jul-2026_30-Sep-2026.csv` (managed fund pair): an
    invented managed investment fund, `Sample Kids Growth Fund` (Product ID `SKGF`, Product Type
    `Managed Fund`), for the *New transaction types* panel. The trade file has five rows: two `DRP`
    rows (a distribution reinvestment, built in as a buy with no cash settlement: 10.5 and 8 units)
    and three `Managed Fund Transaction` rows (420, 200 and 100 units), a Type the Importer does not
    know, so the file stages with that Type unmapped and commit blocked. The cash file holds the
    three matching `Buy` settlements, three deposits and one interest row, so once the Type is
    mapped to Buy, no trade warns about missing cash. The account number is `SAMPLE-KIDS-0001`,
    which no Account holds: **the Account is not in `household.json`** (adding an Account and an
    Instrument there would change the golden Position totals and counts of the existing slices).
    Create it in the app first: Accounts > Add Account, `Sample Kids investment (Sample Bank)`,
    category Investments, type *Brokerage account*, then add the file, choose that Account and
    tick "Remember this reference". `SKGF` is then created from the Unmatched references panel.
    Mapping `Managed Fund Transaction` to Buy and committing both files leaves 738.5 units.
  - `sample_bank_statement.csv`: not an investment platform export; staging refuses it with a clear message.

- **Switching the sample investment platform Account to a derived value.** `Sample Brokerage account` stays a simple
  balance (type *Brokerage account*); nothing switches by itself. After committing the sample trades
  (matching `SNHX` first) and cash exports, edit the Account and set its type to *Brokerage account
  (holdings)* (catalogue version 2 seeds it). Its page then shows a **Derived value** as at the latest
  date both Streams reach (2026-09-30). The fixture holds Prices for the two listed Instruments but
  none for `Sample New Horizons ETF` (it only exists once `SNHX` is created at commit), so the value
  first reads "No derived value ... has no Price"; add a manual Price of `12.50` dated 2026-09-30 on
  that Instrument and the total is **6,645.13**: 50 x 96.1075 (4,805.38) + 60 x 6.3304 (379.82) +
  50 x 12.50 (625.00) + cash 834.93. Add a manual Valuation on 2026-09-30 and the page shows the
  difference as reconciliation. Edit the type back to *Brokerage account* to restore the original
  series. No new fixture file is needed.

- **Fetching Prices and FX rates in the review slot.** No fixture file is needed: the review slot
  (`scripts/review.compose.yml`) sets `FINANCE360_PRICE_PROVIDER=yfinance`,
  `FINANCE360_FX_PROVIDER=rba` and `FINANCE360_PROVIDER_TRANSPORT=sample`. The real provider code
  (symbol lookup, float to decimal conversion, inversion of the published figure, the fetch service)
  runs, but its network seam serves **invented** data with no network: weekday closes for the two
  Instruments that have a `yahoo` identifier (`SBME.AX`, `SMPM.AX`), and a table in the RBA's layout
  for the GBP Account's currency. The app catches up once at start-up (nothing has ever succeeded),
  so a fresh review instance already shows a start-up catch-up run; Data > Reference data > Fetch
  now repeats it (idempotent). A symbol starting `FAIL` simulates a provider that is down: add an
  Instrument with the identifier `FAILING.AX` to see the error row. The fetched figures are not in
  `household.json`, so they never mix with the fixture's own `yfinance` Prices (Aug to Sep 2026),
  except that the sample provider's value for the last fixture day revises it (shown as "1 revised").

- `owner-password`: the synthetic, localhost-only password (`sample-household-login`) the review
  slot's app seeds its one Login from (`FINANCE360_OWNER_PASSWORD_FILE`, set in
  `scripts/review.compose.yml`), only if no Login exists yet. Not a real credential, never reuse it.
  `review` prints it when it serves (ADR-0010).

Each slice that introduces data extends the fixture (standing rule in `AGENTS.md`).

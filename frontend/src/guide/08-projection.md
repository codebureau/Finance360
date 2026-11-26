# Projection

The [Projection](/projection) answers "are we on track?" It takes today's Position, applies a Scenario's Assumptions year by year, and shows whether the funds last. It is a rough answer by design, and the screen lists what it leaves out.

## What it needs
- **Members** with dates of birth, on [Household](/household).
- **Accounts and Valuations** in Cash, Investments, Superannuation and Overseas pensions. These are the only Categories it projects. Property, vehicles and loans are not projected.
- **Income streams** for anyone still earning. Without one, the annual saving in the Assumptions is used.
- **Assumptions**, which the Projection screen lets you edit.

## Scenarios and Assumptions
A Scenario is a named set of Assumptions. Best, Likely and Worst are built in, and they start with illustrative placeholder figures, so review them. You can add your own, such as "Retire early, spend more", that changes only what you state and inherits the rest.

An Assumption is one input: a return, inflation, a retirement age or a level of saving. Each has a Household default, and a Scenario can override it. Returns are set for each Category and retirement ages for each Member. Spending is a list of phases, such as working years, then retirement, then later life.

## Reading the result
The screen leads with the answer, then the picture, and keeps the working one click away.
- The headline says whether the funds last beyond the horizon, or to what age spending is met in full (measured by the youngest Member), and the sustainable retirement income.
- **How the balances move** is a chart of the balance of each Category at the end of every year, stacked so the top edge is the total. Each Category keeps its colour from [Position](/position). The two icons switch between stacked areas (the default) and lines; **Use as my default** keeps your choice for other charts of this kind. Hover or tap a year for the exact figures.
- **Markers** on the chart are Life events: a solid line for retirement, a dashed line for preservation age and a dotted line for Age Pension age, each named in the list under the chart with its date. A shaded band marked **Short** covers the years spending is not met in full. An event that falls after the horizon is not drawn, and the chart says so.
- **The working** is one card below the chart. Each of its sections opens with **Show working**, one at a time: the spending phases, the **Assumptions in use**, the **Age Pension** (the facts it used and who is eligible), **Rule pack ages**, **Life events**, **What this projection assumes** (the simplifications; read it once) and the **Year by year** table, which has the same values as the chart. The buttons to edit the Assumptions and the spending stay beside their sections whether or not the working is open.
- If an Account is left out of the starting balances (no Valuation or FX rate), it is listed under the simplifications.
- If the Assumptions are incomplete, the screen says what is missing. A salary with no spending phase from the start is the usual cause.
- A [saved run](/projection/runs) shows exactly the same screen from what it stored.

## Tax in the projection
By default take-home pay is the figure you typed on the [Income stream](/income-streams). Two Assumptions change that:

- **Tax in the projection** (off unless stated). On, each year's take-home pay is estimated by the same tax engine as the [Tax estimate](/tax): the gross pay is the Income stream's figure for that year (grown by the real wage growth), and take-home pay is gross less income tax and the Medicare levy.
- **Division 293 paid by** (for each Member, `super` unless stated). With `super`, the default, the Member's own super fund pays [Division 293](/guide/division-293): the amount is taken from **that Member's** super balance **a year after** the year it is for, so FY 2030-31's is taken in the next year. With `own` the household pays it from its own money instead; the timing is the same. The first projected year also pays the year just ended, estimated from year 0 figures. A payment that would fall after the horizon is listed but not paid.

**What leaves super, and when.** A line debit is an amount taken from one Member's super balance at the end of a year, after growth and contributions and before any spending is drawn. It never takes the balance below zero. If the balance cannot cover it, the rest is **paid personally**: it is added to that year's spending and met by drawing down your Cash, Investments and the rest in the usual order, never borrowed or carried to a later year. The Year by year table lists each one under "Taken from a line" with what was asked for, what super paid and what was paid personally, and flags any part paid personally. Switching the default to `super` changes a Scenario only when Tax in the projection is on; with it off nothing moves. A run saved before this existed keeps its figures and says so; **re-run with the current engine** saves a new run beside it.

The Rule pack publishes tax figures only up to some year. Later years use the **latest published figures carried forward**: rates and brackets are held, and thresholds the pack marks frozen fall in real terms by inflation each year. The working says which years use carried figures and lists each year's gross pay, tax and take-home, and each Division 293 payment with its inputs.

Not modelled: deductions, HELP, other income, sacrifice and dependants. Division 293 counts taxable income and employer super only.

## Keeping and comparing
- **Save run** keeps a result exactly as computed, with its inputs and versions, so you can open it later: [Saved runs](/projection/runs).
- [Compare Scenarios](/projection/compare) projects them side by side, and shows how far apart they end up:
  - **Total balance by Scenario** draws one line per Scenario over the horizon, each labelled at its end with its name and final balance. A Scenario keeps the same colour here and on its button on the Projection page. Hover or tap a year to read every Scenario's balance.
  - **Headline results** shows the sustainable retirement income of each Scenario as a bar, and for each Scenario how long its funds last.
  - Up to six Scenarios are drawn; any more appear in the table only, with a note. A Scenario that cannot be projected is not drawn: the table says why.
  - **Show figures** under each card gives the exact numbers: the balance of each year, and the comparison table (the Assumptions that differ, the drawdown order and the spending phases).

## The Age Pension
From each Member's Age Pension age the projection pays the **Age Pension** into that Member's cash, like salary and before any Drawdown. It is calculated from the Rule pack's figures and your [Household facts](/household), never entered: the lower of the income test and the assets test, run each year on the balances the year **opens** with. Two consequences: this year's spending affects next year's pension (a one-year lag), and a year's growth is not yet counted. Cash and Investments count from the start; a Member's super and overseas pensions count from that Member's Age Pension age. Property (other than the home) and Vehicles count at their latest Valuation. A couple is assessed together.

If your Household facts do not fit (for example more than two Members), you own several properties and have not chosen the home, or the pack holds no Age Pension figures for the start year, the projection runs with **no Age Pension** and says why. Not modelled: debt secured on an asset, rent, super already being drawn before Age Pension age, Property growth, tax on the pension and Scenario changes to the Age Pension rules.

**Reading the Age Pension.** The Year by year table (under **Show working**) has an Age Pension column once someone is eligible, with each year's total a year in today's dollars. Press **Working** beside a year to open how it was reached:
- the assets read (each Category's opening balance, Property other than the home, Vehicles) and the income read (deeming on the financial assets, and the gross pay of Income streams);
- the maximum, what the income test alone and the assets test alone would pay, and which of them **bound** (a year where neither reduced the maximum says so);
- what each Member was paid;
- the Rule pack figures in force that year, each with its financial year, whether it is shipped or captured, and a **Provisional** mark where it has not been confirmed.

The **Age Pension** card above the table gives the run's inputs once: the Household facts, each labelled **entered** or **default, not entered**; when each Member reaches Age Pension age; the Property and Vehicles assessed, those exempt (the home) and any left out for want of a Valuation; and the simplifications that apply. If no pension was calculated, the card says why and what to change. A saved run keeps all of this exactly as it was; one saved before the Age Pension was calculated says so.

[Compare Scenarios](/projection/compare) has an Age Pension row: the first year each Scenario pays one and how much a year, or that none is paid. A large balance can mean no pension at all, which is the assets test working, not a fault.

## What it does not do yet
These are **coming later**: insurance premiums and fund fees (the mechanism that takes an amount from super is built; no premium or fee amounts are entered yet). Tax is only estimated where Tax in the projection is on.

## Ignore at first
Saved runs, Compare and custom Scenarios. Pick Likely, check the Assumptions and read the headline.

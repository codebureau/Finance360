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
- The headline says whether the funds last beyond the horizon, or to what age spending is met in full (measured by the youngest Member).
- The year-by-year table shows each year in today's dollars.
- "What this projection assumes" lists the simplifications. Read it once.
- If an Account is left out of the starting balances (no Valuation or FX rate), it is listed.
- If the Assumptions are incomplete, the screen says what is missing. A salary with no spending phase from the start is the usual cause.

## Tax in the projection
By default take-home pay is the figure you typed on the [Income stream](/income-streams). Two Assumptions change that:

- **Tax in the projection** (off unless stated). On, each year's take-home pay is estimated by the same tax engine as the [Tax estimate](/tax): the gross pay is the Income stream's figure for that year (grown by the real wage growth), and take-home pay is gross less income tax and the Medicare levy.
- **Division 293 paid by** (for each Member, `super` unless stated). With `super`, the default, the Member's own super fund pays [Division 293](/guide/division-293): the amount is taken from **that Member's** super balance **a year after** the year it is for, so FY 2030-31's is taken in the next year. With `own` the household pays it from its own money instead; the timing is the same. The first projected year also pays the year just ended, estimated from year 0 figures. A payment that would fall after the horizon is listed but not paid.

**What leaves super, and when.** A line debit is an amount taken from one Member's super balance at the end of a year, after growth and contributions and before any spending is drawn. It never takes the balance below zero. If the balance cannot cover it, the rest is **paid personally**: it is added to that year's spending and met by drawing down your Cash, Investments and the rest in the usual order, never borrowed or carried to a later year. The year-by-year table lists each one under "Taken from a line" with what was asked for, what super paid and what was paid personally, and flags any part paid personally. Switching the default to `super` changes a Scenario only when Tax in the projection is on; with it off nothing moves. A run saved before this existed keeps its figures and says so; **re-run with the current engine** saves a new run beside it.

The Rule pack publishes tax figures only up to some year. Later years use the **latest published figures carried forward**: rates and brackets are held, and thresholds the pack marks frozen fall in real terms by inflation each year. The working says which years use carried figures and lists each year's gross pay, tax and take-home, and each Division 293 payment with its inputs.

Not modelled: deductions, HELP, other income, sacrifice and dependants. Division 293 counts taxable income and employer super only.

## Keeping and comparing
- **Save run** keeps a result exactly as computed, with its inputs and versions, so you can open it later: [Saved runs](/projection/runs).
- [Compare Scenarios](/projection/compare) projects them side by side.

## The Age Pension
From each Member's Age Pension age the projection pays the **Age Pension** into that Member's cash, like salary and before any Drawdown. It is calculated from the Rule pack's figures and your [Household facts](/household), never entered: the lower of the income test and the assets test, run each year on the balances the year **opens** with. Two consequences: this year's spending affects next year's pension (a one-year lag), and a year's growth is not yet counted. Cash and Investments count from the start; a Member's super and overseas pensions count from that Member's Age Pension age. Property (other than the home) and Vehicles count at their latest Valuation. A couple is assessed together.

If your Household facts do not fit (for example more than two Members), you own several properties and have not chosen the home, or the pack holds no Age Pension figures for the start year, the projection runs with **no Age Pension** and says why. Not modelled: debt secured on an asset, rent, super already being drawn before Age Pension age, Property growth, tax on the pension and Scenario changes to the Age Pension rules. The per-year working screen is **coming later**.

## What it does not do yet
These are **coming later**: insurance premiums and fund fees (the mechanism that takes an amount from super is built; no premium or fee amounts are entered yet). Tax is only estimated where Tax in the projection is on.

## Ignore at first
Saved runs, Compare and custom Scenarios. Pick Likely, check the Assumptions and read the headline.

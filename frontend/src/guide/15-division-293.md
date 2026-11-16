# Division 293

Division 293 is an **extra tax on the super contributions of higher earners**. Normally the fund taxes the contributions that go into your super (concessional contributions) at 15%. If your income is high enough, Division 293 adds a further 15% on some or all of them. It is not part of your tax return. The ATO works it out separately and sends you a notice.

This page explains the mechanics, because the number is only useful if you can see why it is that number. The working is on the [Tax estimate](/tax): choose the financial year, press Calculate, and look under the result for **Division 293 for FY ...**.

> The one idea to hold on to: Division 293 looks at a **wider income than your taxable income**, and taxes the **lesser** of your super contributions and the part of that income above the threshold.

## The four parts of Division 293 income

Division 293 income is the sum of four things:

1. **Taxable income.** The same figure as on the Tax estimate.
2. **Reportable fringe benefits.** The grossed-up value of fringe benefits your employer reports on your income statement. A novated lease paid from pre-tax salary is the usual example: it lowers your taxable income, but it still counts here. Finance360 takes it from your income statement's Reportable fringe benefits.
3. **Total net investment losses.** Investment deductions that are more than the investment income, plus any net loss on a rental property. Usually nil.
4. **Low tax contributions.** The concessional contributions the fund **received** in the year, less any amount over the concessional contributions cap.

### Why reportable employer super is not added again
Your income statement also shows **reportable employer super contributions**. These are part of the concessional contributions the fund received, which are already in part 4. The ATO's own definition of the income test counts them once. Finance360 counts the contributions once and does not add the reportable employer super on top; adding it would count the same dollars twice.

## The threshold and the "lesser of" rule

- **The threshold** is $250,000 of Division 293 income. At or below it, Division 293 tax is nil.
- Above it, the tax is **15% of the lesser of**:
  - your low tax contributions, and
  - the amount by which your Division 293 income is **over the threshold**.

So if you are only a little over the threshold, you are taxed on only the part that is over. If you are well over, you are taxed on all your contributions, and no more. The working on the Tax estimate shows both amounts side by side so you can see which one applied.

An example, with invented figures: taxable income 234,600, reportable fringe benefits 9,000, no investment losses, contributions 14,400. Division 293 income is 234,600 + 9,000 + 14,400 = 258,000. That is 8,000 over the threshold, which is less than the 14,400 of contributions, so the tax is 15% of 8,000 = 1,200.

## Where the contributions come from

Concessional contributions count **when the fund receives them**, not when they are paid.

Finance360 uses the first of three sources that has a figure for the Member and the year, and says which one it used. It never adds one source to another, because that would count the same dollars twice.

1. **A Super statement** that states the concessional contributions received ([Accounts and imports](/guide/accounts-and-imports)), added up over that Member's super Accounts. This is the fund's own figure, counted by the date the fund received the money, so it is the best source and always wins.
2. **Otherwise, the Member's Income statements** for the year (a Tax record, see [Tax](/guide/tax)): the **employer super** plus the **reportable employer super** (salary sacrifice) on each one. Each Income statement is its own line in the working, named for its employer. Two notes come with it. These are the employer's figures by **period worked, not by date received**: a June quarter payment the fund receives in July counts in the next year on the fund's figure, so the estimate can differ until you enter the Super statement. And a personal contribution you claim as a deduction is **not on the Income statement**, so it is not counted.
3. **Otherwise** (no Super statement figure and no Income statement for the year), the Member's **Income stream**: the gross amount times the Superannuation Guarantee rate for the year from the Rule pack. An Income stream counts for the whole year; salary sacrifice into super is not in it, so it is not counted.

A Super statement that leaves the concessional line blank is passed over, and the working says so. With none of the three, no contributions are counted and the working says that too.

The Tax estimate lists every input with its value and where it came from, including each Rule pack figure with the page it was read from. Figures marked **Provisional** have not yet been confirmed against the official page: confirm them on [Rule figures](/rule-figures).

## The concessional contributions cap

Concessional contributions above the yearly **cap** are "excess" contributions. They are taken out of the low tax contributions above (they are taxed differently), and the working shows the excess. The Tax estimate also compares what the fund received with the cap and warns when you are **within 10% of it, or over it**. Finance360 does not work out the tax on excess contributions, and does not carry forward any unused cap from earlier years.

## When the notice arrives, and how it is paid

The ATO assesses Division 293 **after your return is processed and the fund has reported your contributions**, so the notice for a financial year normally arrives some months into the next one. That is why you can see a prediction before there is a notice.

When the notice comes you can pay it from your own money, or ask your super fund to pay it from your super balance by giving the fund a release authority. When a fund pays it, the amount appears on the fund's annual statement as **Division 293 released**. The Tax estimate shows that figure from your Super statement beside the prediction for the same year, when you have entered one. Because of the timing, a release in one year is usually for an earlier year, so the two will often not match.

The [Projection](/projection) models this when a Scenario switches on Tax in the projection. Each Member's **Division 293 paid by** Assumption is `super` unless stated: the amount is taken from **that Member's own super balance** **a year after** the year it is for (the first projected year also pays the year just ended, estimated from year 0 figures). If the balance cannot cover it, the rest is **paid personally** from the household's other money that year, and the screen says so. Stating `own` pays it from the household's own money instead. The Tax estimate itself does not model who pays.

## What this does not do
- It does not read the ATO's notice. It predicts, from your records.
- Unused cap from earlier years (carry-forward), excess contributions tax and the Superannuation Guarantee maximum contribution base are not modelled.
- An Income stream counts for the whole year; a Life event that ends it part-way through is not applied.
- It is a prediction for the year as your Tax records stand, so keep them up to date to keep it useful.

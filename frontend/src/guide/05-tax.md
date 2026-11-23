# Tax

Tax is three screens that work together, and it is easy to lose track of which does what. This page takes them in the order the information flows.

> The one idea to hold on to: you enter **Tax records**. Finance360 adds them up into **Label totals**. The **Tax estimate** is worked out from the Label totals. The **Income reconciliation** is a separate view that reads only your income statements.

## The three screens

### 1. Tax records: where everything goes in
[Tax records](/tax/records) is a register of dated entries. Each belongs to one Member, or to no one for a household fact. It is kept across years, and its financial year comes from its dates, so you never choose a year for it.

A financial year runs 1 July to 30 June, so FY 2025-26 is 1 July 2025 to 30 June 2026. You pick it from a list on each screen.

Each record is one of these:
- **An amount**: a dated figure, such as interest, a donation or a deduction. It counts toward a Return label (a named item of the tax return, such as Gross interest). If only part of a cost is for work, give a Work-use share, and only that share counts.
- **A fact**: a yes/no or a number of days, such as having a HELP debt.
- **An income statement**: the year's figures for one employer. See below.
- **A depreciating asset**: something you bought that loses value over several years, such as a camera or a laptop. See below.
- **Home office hours**: a monthly table of the hours you worked from home in one financial year. See below.
- **Car kilometres**: a list of your work trips for the year, at the cents per kilometre rate. See below.

A cost shared by two Members is entered once for each, as that Member's share.

Open a record to see its **Documents**: the receipts and statements that support it (PDF, PNG, JPEG, text or CSV, up to 10 MB). **Add** files a new document in the [Library](/library) and links it to the record. **Link existing** links a document that is already in the Library. **Open** shows it in a new tab, and **Unlink** removes it from the record while the document stays in the Library. The same document can support several records. Documents are stored with your data and covered by Backups. If a file ever goes missing or changes, the Tax records and Library screens warn you. See [Library](/guide/library).

### 2. Label totals: what gets counted
Underneath the [Tax estimate](/tax) and on [Tax records](/tax/records) you will see the total for each Return label, for each Member. Each is the exact sum of the records behind it, and you can drill from a total to those records. You never type a total in. To change one, change a record.

### 3. Tax estimate: the answer
On the [Tax estimate](/tax), choose the financial year, enter the number of dependent children, and press Calculate. You get, for each Member and for the Household: taxable income, tax on it, offsets, the Medicare levy, the Medicare levy surcharge, HELP repayment, total tax, credits and the **net outcome**. A negative net outcome is tax payable. A positive one is a refund.

Things to know:
- It is worked out fresh each time from your Tax records. Calculating saves nothing. Press **Save estimate** to keep this result exactly as it is now (see Tax what-ifs and saved estimates, below).
- It covers two Members, assessed as a couple.
- **Whole dollars, as on the return.** Each income and deduction label drops its cents before the labels are added up, the way the tax office does, so the taxable income is the same as a lodged return built from the same figures. Tax withheld, TFN amounts withheld, franking credits and PAYG instalments keep their cents. The net capital gain, after the discount, is also shown in whole dollars. The Tax records and label totals still hold the exact cents. Where a calculated figure (an offset, the Medicare levy, the surcharge, HELP) lands on exactly half a cent, it rounds to the even cent, as the notice of assessment does; the net result may still differ from your notice by a few cents.
- **Prefilled from Income streams.** Where you have no Tax records for a Member's salary in that year, the estimate uses the Member's [Income streams](/income-streams) instead and marks the total *Prefilled from Income streams*. A prefilled figure is a forecast: it is the stream's stated yearly amount (not indexed), shared between owners by their shares, and counted only for the part of the year the stream runs. A stream that starts or ends on a Life event such as retirement is counted for the whole year, and the screen says so. It is never saved as a Tax record. See Prefilled from Income streams, below.
- It needs the Rule pack's figures for that year. The screen lists the years it holds, and says so plainly if the year you chose is not one of them. It never borrows another year's figures.
- **Incomplete** means your records include a return item the estimate does not model. It lists them and leaves them out of the tax.
- **Provisional** means a figure used is announced but not yet law, or has not yet been confirmed against the official page.
- **Division 293** is worked out under the result for each Member, with every input and where it came from. See [Division 293](/guide/division-293).
- The screen lists its simplifications. Read them once.

## Tax what-ifs and saved estimates
A [Tax what-if](/tax/whatifs) tries a change on one financial year without touching your records. It starts from your Tax records and changes only what you tell it to, with four kinds of override:
- **State a total**: a Member's total for one Return label becomes the amount you type, whatever their records add up to.
- **Leave out a total**: a Member's total for one label is left out.
- **Leave out a record**: one Tax record is left out. It stays in your register.
- **Add a hypothetical record**: an amount, or a depreciating asset such as a laptop you are thinking of buying. It is in no register, and the asset works out its own deduction for the year.

Every override is **absolute**: it replaces what the records give, it is never "plus 5,000". A Tax what-if **never builds on another**. Add overrides, then press **Calculate** to see what each did and the Label totals used.

Press **Save estimate** on the Tax estimate or on a what-if and it is kept on [Saved estimates](/tax/estimates) exactly as calculated: the Label totals, the Rule figures, the result and the engine version. A saved estimate **never changes**, even if you edit a Tax record afterwards, and it cannot be deleted. A what-if with saved estimates cannot be deleted either. If an older engine version worked it out, that is said, and **Re-run with current engine** saves a *new* estimate from the same inputs.

To compare, tick two saved estimates **of the same financial year** and press **Compare**. It lists the Label totals that differ and every result line for the Household and each Member, as the second less the first. A negative change in net outcome means more tax to pay or a smaller refund.

### Break-even: how much is it worth?
A what-if's page has a **Break-even** panel. Pick a Member and a Return label that holds an amount, and press **Find break-even**: it finds the amount of that one total, from 0 to 10,000,000.00, at which the household's net outcome (credits less total tax) is at least what your Tax records give. The what-if's other overrides stay in force, so run it on a what-if that changes something; on one with no other overrides the answer is just your records' own total.

- **Up to** an amount means more of it makes the household worse off (income); **From** an amount means more of it helps (a deduction). It is exact to the cent.
- **It checks itself.** The cent either side is listed with its net outcome, and the result is calculated again in full.
- **A warning when the outcome is not steady.** A Medicare levy surcharge tier or a HELP repayment threshold can make it jump, and some totals pull it both ways. The amount is then *a* crossing (the lowest), not necessarily the only one.
- **No crossing** in range, or every amount meeting it, is said plainly.
- **Nothing is stored** by searching. **Save as what-if** creates an ordinary Tax what-if from this one's other overrides with the found amount stated.

## Return worksheet: your estimate against the notice of assessment
Once the tax office has assessed a year, the [Return worksheet](/tax/lodged) tells you how close the estimate came. It is how you find out whether the engine can be trusted for your household.

1. Pick the Member and the financial year.
2. Press **Enter Lodged result** and copy the amounts from the notice of assessment: the date on the notice, then taxable income, tax on taxable income, offsets, Medicare levy, surcharge, HELP, total tax, credits and the net outcome. Leave blank anything the notice leaves out. A refund is positive and an amount payable negative. Anything else the notice lists (a line the estimate has no figure for) goes under **Other lines**, named as the notice names it.
3. The worksheet sets the **estimate** (calculated now from your Tax records and the year's Rule figures) beside the **lodged** amount, with the **difference**, which is the estimate less the notice, for each item you entered. An item you did not enter reads **Not entered**.

How the comparison reads:
- **Every difference is listed**, to the cent. Nothing is rounded away.
- **Net outcome** is matched **within 5 cents**, because the notice rounds its final result by a few cents. Such a line says *Within rounding* and still lists its difference. Every other item must agree to the cent.
- **Lines the engine cannot match** (your Other lines) are listed by name. The estimate has no figure for them, so they are reported, never compared or dropped.
- If the estimate itself cannot be calculated (the year has no Rule figures, say) the notice is still listed, with the reason and a link to [Rule figures](/rule-figures).
- The estimate covers the whole Household, as on the Tax estimate page; set **Dependent children** to match your return.

**An amended notice** replaces the earlier one. Press **Enter an amended notice**, change what the new notice changes, and save. The earlier notice is **kept**: it stays under *Notices entered*, marked Superseded, exactly as you entered it. Add the notice itself under **Documents** on the worksheet. An amended notice keeps its earlier notice's documents. A Lodged result is never deleted; a wrong entry is amended.

This only records and compares. Nothing is lodged, and a notice is not read for you.

## Prefilled from Income streams: records first, never both
A year-to-date total from your records and a full-year forecast from an Income stream describe the same pay, so they must never both count. The rule is one line: **for each Member and each Return label, if the Tax register has any record for that label in the year, the records are used and the Income stream is not counted. Otherwise the Income stream fills the total.**
- There is no blending and no part-year top-up. Either a Member's salary is recorded or it is forecast.
- The estimate lists every Income stream line under *Prefilled from Income streams*, with what it adds or why it is **not counted** (for example: the register already has records for that income and year).
- A prefilled figure can be overridden. In a [Tax what-if](/tax/whatifs), **state a total** for that Member and label (the stated amount replaces the forecast, absolutely) or **leave out the total**. Leaving out the only record that covers a salary also lets the Income stream count, because the rule looks at the records the what-if keeps.
- Only salary is prefilled today. Investment income and capital gains are not prefilled.
- A saved estimate keeps the prefilled figure and where it came from, so deleting or changing the Income stream later changes nothing in it.

## Depreciating assets: one purchase, a deduction each year
A depreciating asset is a worksheet, not a separate line on your return. You enter the asset once, and each financial year it adds that year's deduction to one Return label total: **Business expenses** (item P8, for sole trader work) or **Other work-related expenses** (item D5). Open the asset to see the year-by-year schedule: opening value, days held, the deduction and the closing value. The Label total drills down to the asset.

You enter:
- the **cost**, and the **start date** (when you first used it or installed it ready for use);
- the **effective life** in years. You choose it: the ATO publishes tables, or you can work out your own;
- the **work-use share**, if only part of the use is for work. Only that share of each deduction counts;
- the **last day used**, only if you stop using it. Leave it blank while you still do, and the asset keeps counting in each later year.

Finance360 works out the **diminishing value** method only: each year's deduction is the opening value, times the days held in the year divided by 365, times the diminishing value percentage divided by the effective life, times your work-use share. The next year opens at what is left. It does not work out prime cost, a low-value pool, selling or scrapping an asset, or a simplified depreciation pool.

**The percentage comes from the Rule pack.** If the Rule pack does not hold it yet, the asset says so and adds nothing to any total, and a Tax estimate that includes that Member will not calculate until it is captured. Nothing is guessed. Capture it on [Rule figures](/rule-figures); the percentage is the one the asset's start year holds.

**Claim in full** deducts your work-use share of the whole cost in the year of the start date and nothing after. Finance360 does **not** check that the asset qualifies: whether you may do this depends on its cost and how it is used, and the threshold is different for business and non-business use. Check the ATO's rules before you tick it.

## Home office hours: a monthly table, one record a year
Home office hours are the fixed-rate way to claim working from home. You keep a table with one row per month, and Finance360 turns it into one deduction for **Business expenses** (item P8, sole trader work) or **Other work-related expenses** (item D5). The Label total drills down to the record.

You enter, for one financial year (1 July to 30 June), the **From** and **To** dates and then how you work from home. There are two ways:

- **Regular arrangement.** You work from home to a pattern. Enter the **hours per day**, and describe the pattern one of two ways: tick the **weekdays at home** (the form says, for example, "4 days a week at home"), or give the **office days a month** if you must attend the office a set number of days (say 2). The month table then fills itself in from the calendar: the **working days** are the Mondays to Fridays in the month, the **office days** are the weekdays you are not at home (or the figure you gave, never more than the working days), and **leave days** start blank. The calendar does not know public holidays, so enter those as leave. Change any month (a long weekend, an extra office day) and it keeps what you typed, even if you later change the weekdays; months you have not touched follow the pattern.
- **Casual.** You have no pattern. The table has one **hours** box per month for the hours you actually worked from home. A month you leave blank is not saved and counts as nothing.

**The months follow your dates.** The table has one row for each month from From to To, in order, once both dates are in the same financial year. Change a date and months are added or dropped, and what you typed for the months that remain is kept. **Remove** drops a month; **Add month** brings back the earliest month you removed (it never goes past To, and is greyed out when nothing is missing).

For a regular month, the home days are the working days less leave days and office days, and the hours are home days times the hours per day. For a casual month the hours are the ones you entered. The record's hours are the sum of the months, and the deduction is those total hours times the rate per hour, rounded once. The form shows each month's hours and the total as you type, and the record's page shows the arrangement, the table, the total hours, the rate and the deduction. Days and hours can have up to two decimal places, so half a day is fine. A regular month cannot have more leave and office days than working days, and each month appears once and must fall inside the record's dates. A record saved before arrangements existed shows as Regular with no weekdays; choose them the next time you save it.

**The form is roomy, and it never closes by accident.** A long form such as this one opens across the whole width of the window; the expand and collapse button beside the X switches between that and the narrow panel. Clicking outside a form never closes it. To leave, use Cancel, the X or Esc, and if you have typed anything Finance360 asks "Discard your changes?" first, so a year of entries is never lost to a stray click.

**One record covers one financial year.** If you work across 30 June, enter a record for each year. Nothing is split between years for you. If you only have a yearly total of hours rather than a monthly table, enter the deduction as an amount instead.

Finance360 works out the **fixed rate method** only, and does not check that you meet the ATO's conditions for it (such as keeping a record of your hours). There is no actual-cost method and no timesheet.

**The rate per hour comes from the Rule pack**, for the year the record is in. If the Rule pack does not hold it yet, the record says so and adds nothing to any total, and a Tax estimate that includes that Member will not calculate until it is captured. Nothing is guessed. Capture it on [Rule figures](/rule-figures).

## Car kilometres: a list of trips, one record a year
Car kilometres are the cents per kilometre way to claim a car you use for work. You list your work trips, and Finance360 turns them into one deduction for **Business expenses** (item P8, sole trader work) or **Work-related car expenses** (item D1, an employee). The Label total drills down to the record.

A Member has **one car kilometres record for each financial year** (1 July to 30 June), with a **From** and **To** date in that year. The limit is for one car, and one record is one car. A second record for the same Member and year is refused; add trips to the first one. If you work across 30 June, enter a record for each year.

The record is a table of trips. Each row has:
- a **description**: where to or from, and why (it is required);
- the **km** of one trip, more than 0, with at most one decimal place (a half kilometre is fine);
- the number of **Times** you made that trip, a whole number (it starts at 1), so a regular commute is one row;
- an optional **date**, which must fall between From and To.

The row's **total km** is km times Times, and the record's **total km** is the sum of the rows. The form shows each row's total and the running total as you type. **Add trip** adds an empty row (up to 500) and **Remove** drops one. A row you leave completely blank is not saved.

**The limit and the rate come from the Rule pack.** Only so many kilometres count for one car in a year, so the kilometres counted are the total or the limit, whichever is smaller; the deduction is those kilometres times the rate per kilometre, rounded once. The record's page shows the trips, the total km, the km counted (it says when the limit applied), the rate and the deduction, and the **How this is worked out** card replays it. If the Rule pack does not hold the rate and limit for the record's year yet, the record says so and adds nothing to any total, and a Tax estimate that includes that Member will not calculate until they are captured. Nothing is guessed. Capture them on [Rule figures](/rule-figures).

Enter work kilometres only: Finance360 does not apply a work-use share here. It works out the **cents per kilometre method** only and does not check that you meet the ATO's conditions. There is no logbook method, no actual-cost method and no trip tracking, and it is not linked to a vehicle Account.

## Checking a figure: how a record is worked out
A record that uses an official figure, such as a depreciating asset, home office hours or car kilometres, has a **How this is worked out** card on its page. It replays the calculation step by step with the exact numbers, then lists each figure it used: its name, its value, the financial year it is for, the official page it came from (opens in a new tab; it says if that address has not been confirmed), the day it was captured, the Rule pack version, and a **Provisional** note if it is announced but not yet law. Use it to check Finance360 is using the same rate as the ATO.

**Figures are held per financial year.** Rates change from year to year, and a record uses the figure of its own year, never another year's. A new year's rate has to be captured before records in that year count; until then the card says which figure it is waiting for.

**Shipped or captured.** A figure the release holds says "Shipped in r6". A figure someone read from the official page and captured in the app (without waiting for a release) says "Captured here", with the day, who captured it and the sentence it was read from, and the card shows the figure the release held if that differs. The Rule pack version then reads like `au/2026-27/r6+1:a3f9c1` (one captured figure, then a short code for exactly which), so two results with the same version used the same figures. Capture a figure on [Rule figures](/rule-figures); see [Rule figures: keeping official rates current](/guide/rule-figures).

## Income reconciliation: where did our pay go?
The [Income reconciliation](/tax/reconciliation) is not an estimate. It is a fact about the year, at gross level, taken from income statements only. Take the total package (gross pay plus employer super) as 100%. It shows how much went to tax withheld (which includes the Medicare levy), super, novated lease costs, other deductions and take-home. Whatever is left over is shown as **Unreconciled**. A gap is never hidden.

The screen shows one card for each Member and one for the Household. Each card has a headline (for example, how much of the package is take-home and which part is the next largest) and one horizontal bar for the package, which is 100%. The bar's parts always keep the same colour: tax withheld, super, novated lease costs, other deductions, take-home and **Unreconciled**. Unreconciled is a part of its own and is drawn whenever there is a gap, never folded into another part. A gap that is negative (more accounted for than the package) cannot be a part of the bar, so it is written beside it instead. The chart switch can show the same parts as a donut or as bars. The table of exact amounts and shares is behind **Show figures**. A Member with no income statement for the year gets the note about adding one, not an empty chart.

**Net pay received is optional.** It is not on the ATO statement; it comes from payslips or the bank. When a statement states it, Take-home is that figure and any gap shows as Unreconciled, as a cross-check. When it does not, Take-home is **worked out**: the package minus tax withheld, super, novated lease costs and other deductions. It is labelled "Worked out, not stated", and nothing is Unreconciled for that statement, because the remainder cannot leave a gap. With several statements, each is treated on its own before the totals are added: one that states net pay shows any gap, one that does not is worked out, and a Member's Take-home says how much of it was worked out. Reportable fringe benefits are shown beside the chart, not in it, because they are a grossed-up figure.

## Adding the year's pay: one way, not two
After 30 June (mid-July is usual), enter an **income statement** for each employer, for each Member, from the [Income reconciliation](/tax/reconciliation) screen or from Tax records. Three figures are required: Total gross amount, PAYGW amount and Employer superannuation contribution liability. The rest are optional, Net pay received included (see above).

**Pick a period, not two typed dates.** Every Tax record, and the income streams, ask for a **Financial year** (the year you are looking at, or the current one), which is the dates: 1 July to 30 June, shown beside the choice. For part of a year (an offset from a date, or a job that started mid-year) switch on **Custom dates**: both dates are filled in from the year, so you change only the one that differs. A record opens as a financial year when its dates are exactly one, otherwise as custom dates. A period that crosses 30 June, ends before it starts or runs over a year is flagged on the form before you save, and marked **Check dates** on the Tax records list so a mistake already saved is easy to find. It is only a warning: you can still save. An income stream can run for years, so custom dates are first-class there and a blank start or end still means now or retirement. A depreciating asset keeps its Start date and Last day used.

Do not also enter that pay as plain salary and tax-withheld lines. The estimate would count it twice. Finance360 warns you when you are about to, but still lets you save, so delete one of the two.

### Reading the ATO PDF instead of typing
The quickest way to fill the form is to let Finance360 read the statement's PDF.

1. In ATO online services (through myGov), open the income statement for the year and the employer, and use the browser's **Print**, then **Save as PDF**. (This is the page itself printed to PDF, not a scan or a photo.)
2. In the income statement form, press **Choose the PDF** under **Read from the ATO PDF** and pick the file.
3. The figures it finds are filled into the form and each is marked **Read from the PDF**, with the page it came from ("page 2"). A figure it cannot find is left empty for you to copy from the PDF beside the form. Check them against the statement, give the statement its Description and Employer, and enter the figures that are not on the ATO page (Net pay received, which you may leave blank, and the reconciliation figures), then save.

What it does and does not do:

- **The PDF sits beside the form, so you can check each figure by eye.** On a wide screen the form opens across the window with the PDF on the right, and each scrolls on its own. Press a figure's "page 2" to show that page of the PDF. On a phone (or in the narrow panel) the form and the PDF are two tabs, **Form** and **Document**: switch between them as often as you like and nothing you typed is lost, and pressing "page 2" brings the Document tab forward. The PDF is shown by your browser's own viewer. A browser that cannot show a PDF inline still shows the page numbers, as text. Finance360 does not yet highlight where on the page a figure was found.
- **Nothing is stored until you save.** The PDF is read on the Finance360 server, in memory, and nothing is sent anywhere else. When you save, the PDF is kept in the [Library](/library) and linked to the new Tax record as one of its Documents (Backup covers it). If keeping it fails, the record is still saved and the form offers to try again. If you close the form without saving, the file is forgotten.
- **It uses what it can read and never rejects the PDF.** The ATO prints only the sections your employer reported, so a statement can lack some rows (Bonuses and commissions, Salary sacrifice, taxable fringe benefits, leave) or the employer super row. Whatever is on the page is filled in; whatever is not is left empty, and the form lists it ("Not found on the statement, left blank"). If a figure the form needs (Total gross amount or PAYGW amount) cannot be found, the form says "Some figures could not be read: copy them from the statement beside the form", names what is still to enter, and fills in the rest. Even a PDF that is not recognised as the ATO income statement opens beside the form, so you can copy from it; nothing is filled in for it and it is not kept with the record.
- **Leave paid more than once adds up.** When the statement has several Paid leave amount rows, they are added together into the one field, and the field shows each row ("Added up from 2 rows on the statement") so you can see what it used. The leave types are joined in the Leave payment type field. This holds when each leave sits under its own sub-heading too.
- **It checks itself.** If Gross amount, Bonuses and commissions, Paid leave amount and Salary sacrifice amount do not add up to the Total gross amount, or the PDF is for a different financial year from the form's dates, you are warned beside the figures, and you tick that you have checked them before you can save. Salary sacrifice is shown as a negative amount, as the ATO prints it. A Bonuses and commissions, Paid leave amount or Salary sacrifice amount row the statement leaves out counts as 0 in this check, and the form says so. If a statement gives only the Total gross amount, with no Gross amount, the check cannot run, and the form says that.
- **It never guesses.** It reads exact wording only. The financial year is read from the statement's period. It reads the **Allowances** total (the Total under the Allowances heading), counted as nil when the statement lists none. It does not read the allowance's sub-category, Net pay received or the reconciliation figures: enter them yourself if you want them.
- **One statement per employer.** If you worked for two employers in a year, you have two statements for the same Member and year: read each into its own income statement.
- It reads PDFs with text in them. A scan or a photo is not read yet (coming later).

### The income statement form: copy the ATO statement top to bottom
The form follows the headings of the statement in ATO online services, in the same order, so you can copy it down the page. Give the **Employer** its name first (see "Compared with last year" below). Then:

- **Income: salary and wages.** Gross amount, Bonuses and commissions, Leave payment type (short text), Paid leave amount, Salary sacrifice type (short text), Salary sacrifice amount and Total gross amount. Only the **Total gross amount** is counted toward Salary and wages (Item 1). The others are **for information only**: they are what the total is made of, kept so you can see a bonus or salary sacrifice change from one year to the next. They feed no estimate figure.
- **Tax withheld: salary and wages.** The PAYGW amount.
- **Lump sum amounts.** A, B, E and W are income the estimate does **not calculate**, so a figure in any of them marks the estimate **Incomplete** rather than being ignored. A and B are Item 3 on the return (only 5% of B is taxable). E (payments in arrears) is declared at Item 24, with a schedule, and may attract the lump sum payment in arrears tax offset. For W (return to work payments) the return instructions do not say where it goes, so it is carried on its own. **D** (early retirement scheme and genuine redundancy payments) is tax-free, so it is for the record only and does not mark anything incomplete.
- **Allowances.** Counted toward Item 2 (allowances, tips and director's fees).
- **Deductions: Total.** For information only. A deduction you can claim, such as a union fee, is entered as its own Tax record, so it is not counted a second time from here. Do not confuse it with "Other deductions from pay" below.
- **Employer reported super.** The employer superannuation contribution liability.
- **Other amounts.** Reportable fringe benefits (the taxable employer status amount, for information, which cannot be more than the total), the Reportable Employer Super Contribution and the Reportable fringe benefits total. The estimate reads the **total** only, so if you enter just the total, nothing changes.
- **For the reconciliation.** Net pay received, novated lease costs paid from pay, other deductions from pay and insurance premiums paid from the super balance. These are our own, not on the ATO statement. They feed the [Income reconciliation](/tax/reconciliation) only. Leave Net pay received blank when you do not have it: Take-home is then worked out.

Community Development Employment Projects payments are not recorded.

### Compared with last year
Open an income statement and, if you have the previous financial year's statement for the **same Member and the same employer**, the page shows **Compared with last year**: each figure this year, last year, and the change (this year minus last year, so a fall is negative). A figure that is 0 in both years is left out. It is how you see a bonus or your salary sacrifice move from one year to the next. Nothing is shown when there is no earlier statement.

Finance360 knows an employer only by the **Employer** name you enter, ignoring capital letters and extra spaces. If you leave it blank it uses the record's Description instead, so give both years' statements the same Employer name. A statement with a different name is a different employer and is not compared.

## Entering a managed fund tax statement
A managed fund's tax statement lists its amounts against return items. Enter them as printed, on two labels:
- **Managed fund and trust distributions (not capital gains)**: item **13U** (unfranked distribution) plus item **13C** (franked distribution). The franked distribution already includes its franking credit, so do not add the credit to it.
- **Managed fund franking credits**: item **13Q**. This counts only as a credit against the tax, never as income a second time.

Foreign income on the statement goes on two more labels, under Other income:
- **Foreign source income (assessable)**: items **20E** and **20M**, added to your income like any other income label (whole dollars, cents dropped).
- **Foreign income tax offset**: item **20O**, the tax paid overseas. It is a non-refundable offset: it reduces the tax on your taxable income, but never below zero, and it shows in the **offsets** line of the result.

**The offset is capped, not worked out in full.** The estimate claims what you enter up to the simple cap, a figure on [Rule figures](/rule-figures) for the year (*Foreign income tax offset: simple cap*), the amount a person may claim without working out the full limit. If you enter more than the cap, the estimate claims only the cap, lists the offset as **Incomplete** and says so in the Member's notes, because the full limit calculation is not done. A year whose Rule figures do not hold the cap claims nothing and lists the offset the same way. Foreign entities (item 19), controlled foreign companies and foreign pensions are not modelled either.

Capital gains on the statement go under the capital gains labels (the discountable gain grossed up). Dividends you hold directly are different: there the franked amount leaves out the credit, so you enter it under **Dividends: franking credits** as well and the estimate adds it to income.

## A tax year, step by step
1. Once: add your Members on [Household](/household).
2. As it happens: add a Tax record for each deduction or income item, and add its Documents.
3. After 30 June: add each income statement.
4. Open the [Income reconciliation](/tax/reconciliation) and check that Unreconciled is small and makes sense.
5. Open the [Tax estimate](/tax) for the year, Calculate, and look at the net outcome and any Incomplete or Provisional notice.
6. If the estimate does not look right, open the Label totals under it and drill into the one that looks wrong. The cause is almost always a record.
7. When the notice of assessment arrives, enter it on the [Return worksheet](/tax/lodged) and see each difference.

## Not here yet
These are **coming later**: prefilling investment income, reading a notice of assessment from a file instead of typing it, and the Tax estimate itself supplying take-home pay: the [Projection](/projection) can estimate it with the same engine when a Scenario switches on Tax in the projection (see [Projection](/guide/projection)).

## Which parts do I need?
- **Just want the numbers for this year?** Members, Tax records, income statements and the Tax estimate.
- **Just want to keep the papers?** Tax records and their Documents. You can skip the estimate.
- **Do not care about the Projection?** Ignore it. Tax does not depend on it.

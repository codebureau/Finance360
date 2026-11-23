# Accounts and imports

This is the "keep it current" group in the sidebar: the places where figures go in. Everything else in Finance360 is worked out from what you put here.

[What's due](/), at the top of the sidebar (the Finance360 name also links to it), lists which Valuations and income statements could be brought up to date, and links to each. A screen you open from it has a "Back to What's due" link.

## Household
[Household](/household) holds the household's name, its time zone and its Members. Add the Members first, because every Account and Income stream belongs to one. A Member's date of birth drives the ages used in the Projection.

Household also holds three **Household facts**: whether the Members are a **couple** or **single** for pension purposes, whether the household is a **homeowner**, and which Property Account is the **principal home**. They are facts Finance360 cannot work out for itself. Each is listed with its value and whether it was **entered** or is a **default, not entered**: a blank couple status follows the number of Members (two is a couple, one is single), a blank homeowner is yes, and a blank principal home is your only open Property Account when you have exactly one. Press **Change** beside a fact to set it, or choose "Not entered" to go back to the default. Each change is recorded in the [audit trail](/audit).

Two cases are flagged on the card. **Needs a choice of home** means you are a homeowner with several open Property Accounts and have not said which is the home. **Not modelled** means the Household does not fit (more than two Members, or a couple status that disagrees with the number of Members). The [Projection](/projection) reads these facts for the Age Pension, and its Age Pension card lists each one as used, with the same entered or default label.

## Accounts
An Account is one real thing with a value: a bank account, a super fund, a loan, a property, a brokerage account. On [Accounts](/accounts), add one and choose:
- its **owner**: one Member, or jointly with shares that add up to 100%;
- its **Category and Account type**, which decide how its value works;
- its **currency**, AUD unless it is held in another.

An Account is closed, never deleted, and a closed Account still counts for the dates it was open. Tags are optional labels for your own groupings.

## Valuations: how an Account gets its value
Most Accounts are valued by hand. Open the Account, choose Add Valuation, and enter a date, the figure on the statement and where it came from (manual, statement or estimate). A loan's Valuation is the amount owed, as a positive number. If you enter a wrong figure, correct it: the original stays in the history, marked superseded.

## Recording a Super statement
A super fund sends an annual statement, usually in August or September. On a super fund Account's page, the **Super statements** section is where you type it in, one for each financial year. Choose Add Super statement, pick the year, and enter:
- the **balance roll**: opening balance, contributions in, investment returns, withdrawals (including tax, premiums and fees) and closing balance;
- optionally the **outflow breakdown**: contributions tax, fees before the tax rebate, the rebate, fees taken inside the investment, Division 293 released, other withdrawals and insurance premiums. Leave a figure blank when the statement does not state it: blank is not zero;
- optionally the **contributions received**, concessional and non-concessional, as the fund reports them;
- optionally each **Investment option** at 30 June: its name, units, unit price and value. Add a row for each option.

Finance360 checks the figures as you type, to the cent. The roll must add up (opening + contributions in + returns - withdrawals = closing). If you entered the breakdown, its parts (the rebate subtracts) must add up to withdrawals; fees taken inside the investment are left out of that sum, because they are already taken from returns. The Investment options must add up to the closing balance, allowing a cent for each option because funds round. A figure that does not agree is shown beside the figures involved with a tick: tick that you have checked it against the statement to save. The checks you ticked are kept and shown on the saved statement. Only a figure that is not an amount, a year that is not 1 July to 30 June, a second statement for the same year, or an Account that is not a super fund stops the save.

Saving also records a Valuation at 30 June from the closing balance, with the source "statement", so the Account's value and the Position use it. Click a statement's row in the list to open it **read-only**: the balance roll, the outflows, the contributions received, each Investment option and the checks (with the ones you ticked marked "You checked this"), without opening the form. Correct and Remove are offered there as well as in the row's menu. Correct a statement from either place: the original stays in the history, superseded, and its Valuation is corrected with it. A corrected statement keeps its documents. Removing a statement removes its Valuation too. The statement is for one Account, so a couple with two funds enters one for each. The **concessional contributions received** line feeds the [Division 293](/guide/division-293) working on the Tax estimate, and **Division 293 released** is shown beside its prediction. Reading the fund's PDF into the form and checking returns against the Projection are **coming later**.

## Documents on an Account and its Super statements
The page of every Account has a **Documents** section, and so does the page of each Super statement. Use **Add** to attach a file (a PDF, an image, text or CSV, up to 10 MB), for example the fund's statement PDF, so the real statements live beside the Account they describe. Use **Link existing** to attach a document that is already in the [Library](/guide/library). Open a document from the list, or go to its Library page to see everything it is attached to. **Unlink** takes a document off the Account or statement and leaves it in the Library. Removing a Super statement leaves its documents in the Library too. Finance360 does not read these files into the form yet.

## Imports: a brokerage Account valued from its exports
An investment platform Account works differently. You do not type its value. You add the exported files and Finance360 works it out from the Holdings and the day's Prices.
1. On [Imports](/imports), choose Add file. Pick the Account (or let the account reference in the file match it).
2. The file is **staged**, not saved. Read the review: any unmatched references need an Instrument chosen, and any new transaction type needs a meaning.
3. Check "Changes if committed". A later file that overlaps an earlier one replaces it for the dates it covers, and the replaced rows stay retrievable.
4. Commit.

There are two files per Account, for trades and for cash, and each is its own Stream. If an Account already held units before its first export, record an Opening position on the Account so its Holdings are right.

The one Importer Finance360 has today reads Vanguard Personal Investor files. Other providers are **coming later**.

## Exposure profiles: seeing what an investment Account is made of
An Instrument says what you hold; its **exposure profile** says what it holds. On the Instrument's page (open it from [Instruments](/instruments)), choose Enter exposure profile and type the fund's published split in each of four **Dimensions**: **asset class** (Australian shares, International shares, Property, Infrastructure, Fixed income, Cash, Alternatives), **region** (Australia, North America, Europe, United Kingdom, Japan, Asia Pacific, Emerging markets), **sector** (the eleven standard equity sectors) and **exposure currency** (AUD, USD, EUR and other commonly held currencies). Each Dimension also has Other, the fund's own leftover. Add the date it is as at and where it came from, and leave a weight blank for none. Each Dimension is a section of the form, and you can leave a whole Dimension blank when the fund does not publish it (a bond fund has no sectors). The form keeps a running total for each Dimension as you type and says how it will be treated:
- **Within one percentage point of 100%** is accepted exactly as you typed it. A total that is short (say 99.5%) puts the missing 0.5% in a line called **Not classified**. A total that is over (say 100.5%) takes the excess off the largest weight, and the profile says which one and by how much.
- **Further from 100%** is refused, with the difference given, so a typo cannot get through.

Correct a profile with Correct: the original stays in the history under Earlier versions, marked superseded. Every change is in the [audit trail](/audit).

An Instrument's page also shows its **Holding currency** (what it is priced in) beside its **Exposure currency** (what the assets are actually exposed to, from its profile). They differ: an Australian-listed global fund has AUD Holding currency but mostly foreign Exposure currency.

An investment Account built from its Holdings then has **Look-through** on its page, with a switcher for the four Dimensions (the choice is kept in the address, so a view can be linked). It splits the Account's value by the chosen Dimension, using each Holding's latest Price and the Instrument's profile on the date shown (the profile as at on or before it; if there is none, the earliest later one, and the panel says so). It has a few lines you should know:
- **No profile**: a Holding whose Instrument has no profile yet. It links to its page to enter one, and is never hidden.
- **Not classified**: value a profile does not place in the chosen Dimension: a profile's shortfall from 100%, a profile that leaves that Dimension out (the panel names it), and the Account's cash in region and sector (cash is Cash in asset class, and in its own currency in exposure currency).
- **Unallocated**: the Account's value less its Holdings and cash. It is zero when the value is built from Holdings; it appears when you record a newer Valuation, and it is flagged only when it is more than the larger of 1% of the value and A$50.

The lines always add up to the Account's value, in every Dimension. Funds that hold other funds, super options and a combined view across Accounts are **coming later**.

## Prices and FX rates
A brokerage Account's value needs a Price for each Instrument and, for a foreign currency, an FX rate. See [What to provide, when](/guide/what-to-provide): they are fetched daily if a provider is set up, and can always be entered by hand. A figure you enter yourself wins over a fetched one for the same date. The Account page flags a Price that is stale.

## Income streams
[Income streams](/income-streams) are pay that funds the household until a Member retires. Only salary is available so far. Enter gross pay and take-home pay a year; the Projection uses the take-home figure, and puts employer super into the Member's Superannuation. Take-home is typed by you, unless a Scenario switches on the projection estimate.

## Ignore at first
Tags, Opening positions, Instruments and FX rates (unless you hold something foreign), and the Projection's use of Income streams. Add Accounts and Valuations first.

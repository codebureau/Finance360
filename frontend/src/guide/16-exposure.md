# Exposure

The [Exposure](/exposure) screen answers "what is it all made of?": the whole Household's Position split by what it holds, by Member, by Category or by Account. It is built from the [look-through](/guide/accounts-and-imports) of each Account, so it is only as complete as the exposure profiles you have entered.

## What you see
A picture first, the exact figures one click behind it.
- **A sentence and a chart.** The card says how much of the Position is analysed, then what to notice ("Mostly Australian shares and International shares; 9% has no profile yet"). The chart is a donut by default; the Chart type icons switch it to a stacked bar or bars, and the app remembers your choice for each view. At most six parts are drawn; the smallest are folded into Other for the picture, and the figures keep every line.
- **Dimension**: switch between asset class, region, sector and exposure currency. The choice is kept in the address, so a view can be linked.
- **Level**: Household (everything together), Member, Category or Account. Each of the last three shows one small card per Member, Category or Account, each with its own small chart and its total, all in the one chart type you choose with the switch at the top of the card.
- **Show figures** opens the full table at the level you chose: each line with its share and exact value, what is Not analysed in it, and its total, ending in the Household total. Each Account name opens that Account's own look-through, which shows where its figures come from.

## What is analysed, and what is Not analysed
- An Account is **analysed** when it is built from Holdings (an investment platform Account) or is a super fund Account with a Super statement. Its value is split exactly as on its own page: by label, with No profile, Not classified and Unallocated as lines of their own, so the parts always add up to the Account's value.
- Everything else (cash, property, vehicles, an overseas pension, loans) has no look-through. It is listed under **Not analysed**, with its value and the reason, and still counts in the Position.
- So **analysed plus Not analysed is the Position**, as at the same date, to the cent. Loans are negative, as in the Position. An Account with no value in the Position is listed too, marked Not counted, and left out of every total.
- A holdings Account that cannot be split on the date (for example its imports end before it) is Not analysed, and its reason says so.

## Members and joint Accounts
A jointly held Account is shared between its owners by their ownership share, so that both sides add up exactly: each Member's lines add to that Member's share of the Account's value in the Position, and for each line the Members' parts add to the Account's line. Where a share does not divide evenly the odd cents go to the largest remainders, so nothing is rounded away. No part is negative except Unallocated, which is negative when the parts stated for an Account are more than its value.

## The date
Leave it as it is, and the screen uses today, or the latest date the holdings Accounts' imports reach when that is earlier, so an Account is not Not analysed only because its imports lag a few days. To see a particular date, add `?asat=2026-09-30` to the address. The Position link on the screen opens the Position for the same date.

## Where each figure comes from
Open an Account from the figures or the Not analysed list. Its page shows its look-through, with each Holding, the profile used and its date, and the underlying funds of a fund of funds. The Exposure screen adds Accounts together, and never changes them.

## Ignore at first
The Category and Account levels and the Dimensions other than asset class. The Household card and the Member level are enough to start.

## Coming later
How the mix compares with a target, the largest single exposures, and history over time.

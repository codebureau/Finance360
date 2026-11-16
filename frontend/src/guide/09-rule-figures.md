# Rule figures: keeping official rates current

Some of what Finance360 works out depends on official figures that the government sets: the cents-per-kilometre rate for a car, the hourly rate for working from home, the tax brackets, the Medicare levy and so on. Finance360 calls them **Rule figures**. Each one belongs to a financial year, and a record in that year uses that year's figure, never another year's. Nothing is guessed: if the figure for a year is not there, the record says it is waiting for it and adds nothing to any total.

The [Rule figures](/rule-figures) screen lists every figure a financial year needs. Pick the year at the top; the list runs from the first year Finance360 holds to next year (so you can enter figures that have been announced). Tick **Missing only** to see just what is left to do.

## Shipped or captured
Each figure says where it came from.
- **Shipped (r6)** means it came with the release you are running. The release number is the Rule pack's revision. These figures were checked by the maintainer with the official page open.
- **Captured** means someone in your household read it from the official page and typed it in here. It shows the day and who captured it, and the sentence they read it from. A captured figure takes effect at once; no release or restart is needed.
- **Carried** means you started the year from an earlier one (see **Start from last year** below): it is last year's figure, not yet checked against the page, and provisional until you confirm it.
- **Missing** means nobody has supplied it for that year yet.

If you capture a figure the release also holds, yours is the one used, and the release's figure is shown beside it so you can see the difference.

## When they change
Most change every **1 July**, when a new financial year starts (the car and home office rates are two). Others change when the government announces them, sometimes in the Budget before they become law. Check the [What to provide, when](/guide/what-to-provide) schedule: a new year's figures are due before you need to count records in that year.

## How to capture one
1. Open [Rule figures](/rule-figures) and choose the financial year.
2. On the figure's row, open its menu and choose **Capture figure**. The sheet has a link to the official page and says where on the page to look.
3. Open the page, find the figure, and type it in. The sheet shows how to write it and the range that is plausible. A figure outside that range is refused: if the real figure is outside it, the range is out of date, so ask the maintainer.
4. Optionally paste the sentence on the page that states it. It is kept with the figure and shown on any record that uses it, so you can see later where the number came from.
5. Check the page address and the day you read it, then press **Capture figure**.

If the figure is much larger or smaller than last year's, the sheet shows last year's beside yours and asks you to tick **Yes, this is right** before it will save. That catches a slipped decimal point.

To fix a mistake, choose **Correct figure** on a captured row. The new figure replaces the old one and the old one stays in the history (the [Audit trail](/audit) records both).

## Start from last year
Most figures repeat from one year to the next. When a year has missing figures that an earlier year can supply, [Rule figures](/rule-figures) offers **Start from 2025-26** (the latest earlier year with a figure).
1. Press it to preview each missing figure, its value and the year it comes from. A figure with no earlier year is listed under **No earlier figure; capture it** and stays missing. Nothing is saved yet.
2. Press **Carry N figures forward**. Each figure is saved as **Carried**: provisional, with the note "Same as 2025-26, not checked against the page". Figures you already hold are never touched.

A carried figure counts in the [Tax estimate](/tax), which lists it as provisional, by name, until you confirm it. Nothing is carried silently: a year with no figures still reads nothing.

**Confirming** replaces the carried figure with a normal one; the carried one stays in the history.
- **Confirm unchanged** on a row, once you have checked the page. The same value is saved from today.
- **Confirm for a whole page.** Carried figures are grouped by their official page; one button confirms the unchanged ones together, each with its own [Audit trail](/audit) entry.
- **Read the page for me** also checks carried figures. One that differs needs **Yes, this is right** first.
- **A figure that changed:** **Correct figure** on its menu.

A **Known change** note (a rate set in law to change on a date) appears on the proposal, the carried figure and beside Confirm. Such a figure is left out of the page confirm: confirm it on its own row. The note never changes a figure.

## Let Finance360 read the page for you
Instead of typing each figure, you can paste the official page and let Finance360 find them. **It needs nothing set up and is never greyed out.** Typing (above) works exactly as before whenever you prefer it.
1. Open [Rule figures](/rule-figures), choose the financial year and press **Read the page for me**.
2. Open the official page (the sheet links to it), select all, copy, and paste the text into the box. If the screen has missing figures on more than one page, first choose which page you copied. The box takes up to 40,000 characters; paste only the part with the figures if the page is longer.
3. Press **Read the page**. For each missing figure on that page, Finance360 proposes a value and the sentence it came from.
4. Check each sentence against the page, then press **Confirm** on a row, or **Confirm all** once you have checked them all. **Nothing is saved until you confirm.** Closing the sheet keeps your pasted text and the results until you leave the screen or reload.

**Who reads the page.** By default Finance360 does, **by matching words** on the page: it looks for the sentence that mentions most of the words in the figure's name, and takes the number from it. This runs on your server; the text you paste is not sent anywhere, is not stored, and costs nothing. Each result is labelled **Matched by words on the page**. It is plainer than an AI: it only reads whole-number, dollar and percentage figures, not tax brackets, and it would rather say **Not found; enter it** than guess. If the host has set up an AI service for [Ask](/guide/ask), the page is read by AI instead and each result is labelled **Read by AI**, with its confidence (high, medium or low) shown for your information only. If that AI service has reached its limit or is not answering, the page is read by matching words and a line says why.

**Checked before you see it.** Finance360 checks every proposal itself, whoever made it. The value must be a valid figure for that row (the right kind and unit, inside the plausible range, the same check as typing it), the sentence must really appear in the text you pasted, and the figure's number must appear in that sentence (`0.88`, `$0.88` and `88 cents` all match; a number written out in words does not). A proposal that fails a check is not offered: the row says **Not found; enter it**, gives the reason and has a box to type the figure. A figure the page never mentions is listed under **Not on the page you pasted**; type it from its row's menu.

**The same rules as typing.** A proposal for a figure that is much larger or smaller than last year's shows last year's beside it and needs **Yes, this is right** before it can be confirmed. A figure the page calls announced but not yet law is marked **Provisional** and keeps the page's wording as its note. A confirmed figure is stored with how it was found (**Matched by words on the page**, **Read by AI**, or typed), the sentence, and what was proposed kept beside the figure you confirmed, so a correction you made is visible. It shows in the figure's history, and on any record that uses it, like any other capture, and you can correct or withdraw it the same way.

**What is sent.** When Finance360 matches words, nothing. When an AI service reads the page, the text you pasted and the list of figures being looked for. Before it leaves Finance360, the names it knows (the Household, Members, Accounts and institutions), Account identifiers and e-mail addresses are replaced with placeholders such as `[MEMBER_1]`, in case a signed-in banner was copied with the page. Unlike [Ask](/guide/ask), **the figures on the page are kept**, because they are what is being read. Nothing else from your records is sent. The [AI log](/settings/ai-log) shows exactly what went and what came back. Matching words writes no AI log entry, because nothing was sent to an AI.

**Limits.** Matching words has none. When an AI service reads the page, it counts toward the monthly AI spend but not toward Ask's questions a day. If the AI service fails part-way, the page is read by matching words instead.

## Provisional figures
If a figure has been announced but is not yet law, tick **This is announced but not yet law** and add a note saying what was announced. The figure is marked **Provisional** wherever it is used, so an estimate built on it says so. When it becomes law, capture the settled figure: it replaces the provisional one.

## What withdraw does
**Withdraw figure** removes a capture. The figure then goes back to the release's figure for that year, or to **Missing** if the release has none. It always asks first, and tells you which figure it falls back to. A withdrawn capture is kept in the history; it is never erased.

## What [What's due](/) tells you
[What's due](/) watches Rule figures and links each item to the [Rule figures](/rule-figures) screen at the right year. The schedule line for each is in [What to provide, when](/guide/what-to-provide).
- **Not captured.** A Tax record reads a figure for a year nobody has captured. The item links to that year with **Missing only** ticked.
- **Differs from a release.** You captured a figure and a newer release ships a different value for that year. Your figure stays in use. **Keep mine** means you have seen the difference: it is not raised again unless a later release ships yet another value. **Use shipped** withdraws your captured figure so the release's applies.
- **No longer fits.** A release changed what a figure may be, so one you captured no longer passes the check. It is ignored until you capture it again.
- **Now firm.** You captured a figure as provisional and a release now ships the settled one. The release's figure is used; withdraw yours to tidy up.

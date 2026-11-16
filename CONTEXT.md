# Finance360

Finance360 is a personal household wealth-planning platform: total financial position, look-through
analytics and retirement projection, in an Australian context.

This file is Finance360's **controlled vocabulary**: the canonical name and meaning for each concept
in the domain. It records names; it does not make decisions. Where a term's meaning was settled by
an ADR, the entry reflects that ADR, and `docs/adr/` remains the place the reasoning lives.

**Partial by design.** Terms are added as they are actually resolved, not invented up front. An
absent term means "not yet settled," not "has no name."

## Language

### People & ownership

**Household**:
The single top-level unit Finance360 models: the people whose combined financial position is being
planned. One deployment, one Household.
_Avoid_: Family, Tenant, Organisation, Portfolio

**Household time zone**:
The Household's IANA time zone (default `Australia/Melbourne`). "Today" for date validation and
default as-at dates is the local date in this zone, never the server's UTC date.
_Avoid_: Locale, Server time

**Member**:
A person within the Household. Every Account and Income stream is owned by one Member or held
jointly. Member-level facts (age, preservation age, pension eligibility) drive projection.
_Avoid_: User, Person, Client, Owner (on its own)

> **Note:** a Member is not a login. One login exists for now; whether Members ever get their own
> logins is undecided.

### Access

**Login**:
The credential that lets someone into the app: a password and optional TOTP. One exists for now. It
is not a Member and not tied to one (ADR-0010).
_Avoid_: User, Account (an Account is a financial holding), Member (as a synonym)

**Role**:
What a Login may do: **owner** (read and write) or **viewer** (read-only, designed but not yet
built). The owner role is about access, not Ownership of Accounts (ADR-0010).
_Avoid_: Permission, Privilege

**Session**:
A signed-in browser's server-side record, started by a successful login and ended by logout or
expiry. A *trusted device* Session is the long-lived kind (ADR-0010).
_Avoid_: Token (a Session is looked up server-side, not self-contained; the other kind is an Access token)

**Access token**:
A bearer credential the owner creates in Settings for a tool on another machine (Claude Code over MCP). It
is shown once, stored hashed, scoped, expiring and revocable, and works on the MCP endpoint only; it can
never create tokens or activate a profile (ADR-0027). Not a Session and not a Login.
_Avoid_: API key, Password, Token (on its own)

### Classification

**Class**:
The top-level accounting nature of an item: **Asset** or **Liability**, and later possibly
**Income** or **Expense**. Follows chart-of-accounts usage.
_Avoid_: Type, Kind

**Category**:
A roll-up classification grouping Accounts of a similar financial nature, such as Investments,
Superannuation, Property, Cash or Loans. It is also the axis along which the product is explored in
depth. A classification, not a container.
_Avoid_: Area, Domain, Bucket, Asset class (see below)

**Account type**:
A kind of Account with shared behaviour and rules, such as an investment platform account, a super fund
account, an offset account or a home loan.
_Avoid_: Product, Template

**Behaviour**:
A code-implemented pattern of how an Account type's value and rules work: *simple balance*,
*holdings-based*, *amortising loan*, *offset-linked*, *super*. Account types reference one. Adding an
Account type is configuration; adding a Behaviour is code.
_Avoid_: Strategy, Handler, Plug-in (on its own)

**Tag**:
A free-form, user-defined label on an Account, usable as an extra roll-up dimension (e.g.
"emergency fund").
_Avoid_: Category (a Tag is ad hoc; a Category is the standard roll-up), Label

### Things held

**Account**:
One real instance of something with a balance that sits on the household balance sheet. It has its
own lifetime (opened and, possibly, closed), an owner (a Member or joint), a Category, an Account
type and a currency (AUD unless overridden).
_Avoid_: Asset (on its own), Item, Position

> **Note:** an Account is closed, never deleted. A closed Account counts in a Position only for
> dates when it was open (ADR-0009).

**Ownership**:
How an Account is held: **sole** (one Member at 100%) or **joint** (two or more Members with shares
summing to 100%, defaulting to an even split). Joint is a mode of the Account, not a Member.
_Avoid_: Split (an Ownership share is a stored fact; a split is what Position derives from it)

**Holding**:
A quantity of one instrument held within an Account, e.g. units of an ETF in an investment platform account.
_Also_: Position (within an Account)
_Avoid_: Investment, Asset, Lot

**Income stream**:
An entitlement to, or flow of, future payments that has no balance on the balance sheet but drives
projection. A sibling of Account, not a kind of it. It is **stored** (an amount the household states:
salary, later business income or a defined-benefit pension) or **calculated** (an entitlement derived
from the Rule pack, Member facts and the Position: the Age Pension, later a foreign State Pension),
and a calculated one is never a row. Each stored stream has a type, and each type has a tax-relevant
kind: employment, business, investment or government (ADR-0017).
_Avoid_: Account, Pension account, Benefit

### Valuation & import

**Valuation**:
A dated value of an Account, tagged with its source (manual, statement, derived or estimate) and its
provenance. An Account's history is its series of Valuations.
A Valuation is recorded as the figure shown on its source: a Liability Account's Valuation is the
amount owed, as a positive number. Its Class (via its Category) gives the sign in a Position.
A correction **supersedes** the earlier Valuation, which stays retrievable; Valuations are never
overwritten or deleted.
_Avoid_: Balance (on its own), Snapshot, Price

**Super statement**:
A super fund's annual statement for one Account and one financial year, recorded by the owner: the balance
roll, and optionally the outflow breakdown, contributions received and each Investment option's units,
price and value at 30 June. It records a Valuation at 30 June and is corrected by superseding (ADR-0025).
_Avoid_: Annual statement (on its own), Statement (a Valuation source or a PDF), Annual report

**Investment option**:
One option a super fund holds the Account's money in, with its units, unit price and value on a date, as
stated on a Super statement. The option mix is the set of them.
_Avoid_: Fund (that is the provider), Portfolio, Holding (that is an Instrument in an Account)

**Import**:
One ingestion of a source document through an Importer. It is staged, diffed, then committed, and
may later be superseded.
_Avoid_: Upload, Sync, Load

**Coverage**:
The source × Account × date range an Import is authoritative for. A later overlapping Import from the
same source supersedes the earlier one within the overlap.
_Avoid_: Period, Range (on its own)

**Stream**:
One kind of record feed within a source for an Account, e.g. an investment platform's *trade* stream and
*cash* stream, each delivered as its own file. Coverage and supersession apply per Stream.
_Avoid_: Feed, File (a file delivers a Stream; it is not one)

**Importer**:
A deterministic plug-in that interprets one source format.
_Avoid_: Parser (an Importer may contain one), Connector

**Import source**:
The binding of an Importer to one Account, with the configuration that Importer needs (for example a
column mapping). Created at the first Import and reused. Coverage and supersession apply per Import
source and Stream.
_Avoid_: Feed, Connection, Connector, Integration

**Account identifier**:
A reference that names an Account outside Finance360: a BSB and account number, a member or policy
number, a provider's account id. Optional, sensitive, kept only in the instance database and shown
masked unless the owner reveals it. An Importer's file reference resolves to an Account through it.
_Avoid_: Account number (on its own), Credential

**Import profile**:
A confirmed, saved mapping, typically first proposed by AI for an unrecognised file. It makes later
Imports of that format deterministic.
_Avoid_: Template, Mapping (on its own)

**Document profile**:
A saved description of one layout of a PDF or pasted-text document: the labels to look for, the
sections they sit in, how a value pairs with its label, and the sums that must agree. Reading with it
is deterministic. The counterpart of an Import profile for documents rather than column files; it may
be shipped in a release or saved in the instance once the owner activates it. Claude Code (over MCP) or an
optional in-app model may propose or repair one, never read a document itself (ADR-0024, ADR-0027).
_Avoid_: Template, Parser, Mapping (on its own)

**Proposed profile**:
A Document profile saved in the instance but not yet switched on: inactive, with the result of its test
against one Library document. The owner reviews it beside that document in Settings and activates or
retires it. Nothing a conversation proposes is live until then (ADR-0027).
_Avoid_: Draft (a draft is the unsaved JSON being tried), Pending

**Reading**:
What a Document profile finds in one document: the figures it found with where on the page, the figures
it did not find, and the result of each check. Nothing is stored from a Reading until the owner
confirms; confirming a Tax record's form or an Import's diff is that step. A Reading is not a record.
_Avoid_: Extraction, Scan, Result

**FX rate**:
A dated rate for one ordered currency pair, with source and provenance: units of the second currency
per one unit of the first. Corrections supersede, as for Valuations.
_Avoid_: Exchange rate, Conversion rate

**Audit trail**:
The append-only record of every change to household data: what changed, when, and from which source
(the owner, an Import, confirmed agent output).
_Avoid_: History (an Account's history is its Valuations), Log (the AI gateway keeps its own log)

### Instruments & look-through

**Instrument**:
Something that can be held in units and priced, e.g. an ASX-listed ETF. Holdings are quantities of
Instruments.
_Avoid_: Security, Product, Ticker (a ticker identifies an Instrument)

**Price**:
The end-of-day value of one unit of an Instrument on a date, in the Instrument's currency, with source
and provenance. Corrections supersede, as for Valuations.
_Avoid_: Quote, Rate (that is an FX rate), Valuation (that is of an Account)

**Provider**:
A plug-in that maps an outside source of reference data (Prices, FX rates) into canonical records,
for example `yfinance` for Prices and `rba` for FX rates. It returns candidates; only the reference
service writes them, recording the Provider as the source and its symbol or published figure as
provenance. A `manual` entry always wins a same-date tie. Not an Importer (that reads a file you
add), and not a financial institution.
_Avoid_: Feed, Connector, Integration, Source (on its own: a source names a Provider or `manual`)

**Fetch run**:
One attempt by a Provider to fetch a dataset (Prices or FX rates): when it ran, what started it
(the daily job, a start-up catch-up or Fetch now), its outcome and each Instrument's or currency's
result. A failed or partial run is shown, never hidden; it never blocks the app.
_Avoid_: Sync, Refresh, Job (the daily job is what schedules a Fetch run)

**Transaction**:
One dated record in an Account's trade or cash Stream (a buy, sell, distribution, fee, deposit and so
on). Holdings and an Account's cash balance are derived from current Transactions, never stored.
_Avoid_: Trade (that is one kind of Transaction), Entry, Movement

**Opening position**:
The Holdings and cash an Account already had before its first imported Transaction, recorded as
explicit dated Transactions of kind `opening_balance`. The Importer detects a missing one and
reports it; it never guesses one.
_Avoid_: Starting balance, Initial holdings

**Exposure profile**:
A dated breakdown of an Instrument across look-through dimensions (asset class, region, sector,
exposure currency) as percentage weights. A fund-of-funds' profile references its underlying funds'
profiles and is resolved recursively.
_Avoid_: Allocation (that is a target or a mix), Composition, Holdings list

### Projection

**Scenario**:
A named set of Assumptions over a time horizon, projected forward. Best, Likely and Worst are
seeded Scenarios, not special cases.
_Also_: What-if (in a projection; the tax equivalent is a Tax what-if)
_Avoid_: Case, Model, Simulation (Monte Carlo is an engine, not a Scenario)

**Assumption**:
One projection input (a return rate, inflation, retirement age, spending level…), layered with
inheritance from Household default → Category → Account, and overridable per Scenario.
_Avoid_: Parameter, Setting

**Life event**:
A dated point on a Member's or the Household's timeline that changes cash flows or rules, such as
retirement, preservation age, Age Pension age, mortgage paid off, or a pension transfer. Rule-fixed
(from the Rule pack and date of birth) or chosen per Scenario (retirement). In M3 it is a date derived
when a projection is prepared, not a stored record; stored Life events wait for their first slice
(ADR-0017).
_Also_: Moment that matters (an investment platform's term)
_Avoid_: Milestone (reserved for delivery milestones), Trigger

**Spending phase**:
A level of annual household spending that starts at the projection start or a number of years after
the last Member retires, and runs until the next phase. A Scenario's phases replace the Household
default phases as a whole list (ADR-0017).
_Avoid_: Spending stage, Budget period

**Drawdown policy**:
The ordered rule set deciding which Accounts fund spending in a projection.
_Avoid_: Withdrawal strategy, Decumulation plan

**Projection input**:
The complete, resolved value a projection is computed from. It holds the starting balances by
Member and Category as at a date, with a return rate on each, plus inflation, cash flows, drawdown
order and horizon. It is versioned and serialisable, and has no float in it. Assumptions resolve
into it before the engine runs. A saved projection run snapshots it, together with the engine
version (ADR-0015).
_Avoid_: Parameters, Config, Snapshot (that is a Backup term), Scenario (a Scenario is what
resolves into one)

**Line debit**:
A regular, non-discretionary amount taken from one Member's line in one projection year, with a reason
(Division 293, an insurance premium, a fund fee) and a label of what it is for. It is paid from the
line's balance and never below zero; any part it cannot pay is shown as unpaid (ADR-0028).
_Avoid_: Withdrawal (that is Drawdown meeting spending), Fee (one reason, not the thing), Deduction
(a tax term)

**Projection run**:
A saved projection: the resolved Projection input, its result, the engine version and the Rule pack
version, written once and never changed, so it can be reproduced and explained later (ADR-0016).
_Avoid_: Saved scenario, Snapshot (that is a Backup term), Simulation

### Tax

**Tax record**:
A dated entry in the tax register, owned by one Member (or, for a household fact, by none): an income
line, a deduction line, a depreciating asset, an income statement. It is evidence-bearing and kept across
years. Its financial year is derived from its dates, never stored (ADR-0019).
_Avoid_: Transaction (that is an Account's), Receipt, Entry

**Record type**:
A code-registered kind of Tax record that declares its input shape and a pure function from its records
and Rule pack values to contributions toward Return labels (`line`, `fact`, `income_statement`, later
depreciating asset). The tax calculation sees only Label totals, so a new Record type does not change it.
_Avoid_: Behaviour (that is for Account types), Plug-in (on its own)

**Return label**:
A registered item of the tax return (for example salary, or a deduction item) that a Record type
contributes to, with a value kind of money, days or flag. A `memo` label is on no return but feeds a view.
_Avoid_: Category (that is a roll-up classification), Field, Item

**Label total**:
A Member's total for one Return label in one financial year: the exact sum of Record contributions. It is
computed, never typed in and never stored, and it drills down to its records.
_Avoid_: Subtotal, Balance

**Work-use share**:
The part of a `line` Tax record's amount that counts toward its Return label, a rate held as an exact
count of parts per million (a cost used 40% for work counts 40% of its amount, rounded once per record).
_Avoid_: Apportionment, Percentage (on its own)

**Tax estimate**:
A saved, immutable calculation for the whole Household and one financial year. It holds its resolved
input (Label totals and the rules used), result and provenance, the engine version and the Rule pack
version, so it can be reproduced and explained later. Marked incomplete if a Return label with a figure
is not modelled (ADR-0019).
_Avoid_: Tax projection (a Projection run is the long-term kind), Tax return, Forecast

**Tax what-if**:
A named base plus structured overrides (set a Label total, exclude a label or record, add a hypothetical
record) for one financial year, calculated into Tax estimates and compared. Not a Scenario, which belongs
to the retirement projection. May be proposed by an AI as a hypothetical; it never writes records.
_Avoid_: Scenario, Tax scenario, Sandbox

**Lodged result**:
A Member's assessed outcome for a financial year, entered from a notice of assessment, against which a
Tax estimate is compared. A later notice supersedes the earlier, which stays retrievable.
_Avoid_: Actual, Tax return (the return is what is lodged, not what Finance360 keeps)

**Evidence**:
A Library document linked to a record to support it (a receipt or statement on a Tax record, a statement
on a Super statement). Evidence is a role of a Document link, not a separate store; the screen says
"Documents" (ADR-0026).
_Avoid_: Attachment, Receipt (a receipt is one kind of Evidence)

**Library**:
The area and the household's set of stored documents: PDFs, text and images, searchable, each showing
every record it is linked to. Documents can be gathered first and linked or filled in from afterwards.
_Avoid_: Document store, Vault, Files

**Library document**:
One stored file, content-addressed by its SHA-256 and kept once however many records link to it, with its
extracted text for search. Removing a record never removes it; only an explicit delete of an unlinked
document does. Covered by Backup.
_Avoid_: Attachment, Upload, File (on its own)

**Document link**:
The association of one Library document with one record (an Account, a Super statement, a Tax record, ...).
Records of any kind register to be linkable; a link follows a corrected record to its current version.
_Avoid_: Attachment, Reference

**Fill target**:
A form that a Reading can fill (the income statement, the Super statement), registered once with its
fillable fields. A Document profile names the target it feeds; a new layout for an existing target needs
no code (ADR-0026).
_Avoid_: Template, Destination

**Income reconciliation**:
A factual yearly view per Member and for the Household, at gross level, from income statement Records:
the total package (gross pay plus employer super) is 100%, split into tax withheld (which includes the
Medicare levy), super, novated lease costs, other deductions and take-home. Any gap is shown as **Unreconciled**, never hidden. Reportable
fringe benefits sit beside it, not in it (ADR-0019).
_Avoid_: Budget, Payslip analysis

### Knowledge

**Knowledge**:
A durable annotation or fact about the household's finances that is not a figure, with provenance
(stated, agent-inferred or from a document). Agent-inferred Knowledge is tentative until confirmed.
_Avoid_: Note (on its own), Memory, Insight

### Currency

**Base currency**:
The reporting currency all figures roll up into: AUD.
_Avoid_: Home currency, Default currency

**Holding currency**:
The currency an Account or Holding is valued in natively.
_Avoid_: Currency (on its own, when exposure currency could be meant)

**Exposure currency**:
The currency the underlying assets are actually exposed to, derived from look-through analytics.
An AUD-listed global ETF has AUD holding currency but largely non-AUD exposure currency.
_Avoid_: Currency (on its own, when holding currency could be meant)

### Product structure

**Capability**:
A cross-cutting engine applying to every Category, at any roll-up level (Account, Category, Member,
Household). Initially: Import & reconcile, Valuation & reference data, Household (the identity
domain: Household and Members), Position, Look-through
analytics, Projection & scenarios, Goals & targets, AI assistance.
_Avoid_: Module, Feature, Service

**Position**:
The Household's assets and liabilities at a point in time, or over history, sliced along any
classification.
_Also_: Financial position, Balance sheet
_Avoid_: Net worth (that is one figure derived from a Position), Snapshot

**Rule pack**:
A pluggable, versioned jurisdiction package that Capabilities consult rather than hard-code: dated
parameters by financial year (ages, Age Pension rates and thresholds, later tax tables) and pure age
functions of date of birth. It holds data, not benefit or tax algorithms. Its version, which a
Projection run records, reads `<pack>/<last known financial year>/r<revision>`, for example
`au/2026-27/r1` (ADR-0018).
_Avoid_: Jurisdiction module, Tax engine
_Note_: a **financial year** runs 1 July to 30 June and is named by the year it ends in (FY 2026-27 is
2027). The ATO calls it the income year; Finance360 says financial year. A pack figure that is announced
but not yet law is marked **provisional**, and anything calculated with one is flagged (ADR-0019).

**Captured figure**:
A Rule pack figure a person read from the official page and confirmed in the running app, stored with its
quote, page, date and who captured it, and layered over the shipped pack. It is withdrawn or superseded,
never edited. A captured figure wins over a shipped one that differs, and the difference is flagged
(ADR-0022). Its version shows as `au/2026-27/r6+3:a3f9c1` (captures counted, then their digest).
_Avoid_: Override (that is a Scenario Assumption), Manual figure

**Carried figure**:
A captured figure written by "Start from <FY>": an earlier year's value proposed for a missing key and
accepted by the owner, provisional ("Same as <FY>, not checked against the page") until confirmed against
the page. Confirming supersedes it; nothing is carried at read time (ADR-0022).
_Avoid_: Default, Inherited figure, Rolled-over figure

**Backup**:
A restic snapshot of the database (SQLite online-backup copy), the documents folder and a manifest,
encrypted client-side with the Backup key before it leaves the host (ADR-0011). The plaintext,
local-only pre-migration copy is not a Backup.
_Avoid_: Snapshot (a Position term), Export (the full data export is plaintext and separate)

**Backup key**:
The single secret that encrypts every Backup repository. Held in the password manager, never in the
repo, logs or beside a Backup. Losing it loses the data.

**Guide**:
The pages inside the app, at `/guide`, that explain what Finance360 answers, how its pieces fit and
what to provide and when. Written as Markdown in the repo, so they ship with each release. It is not
field-level help, which lives on the screens.
_Avoid_: Help, Manual, Docs, Wiki

**What's due**:
The home screen's list of **Due items**: places where the data Finance360 uses has gone stale or a new
period has arrived, such as a manual Valuation older than its usual gap or a missing income statement for
the last financial year. It is computed on demand from **Due rules**, each contributed by the Capability
that owns the data, and lists only what the household already uses. Items are worded as accuracy, never as
failure, and are **important** or **optional**; the screen leads with the important ones. Each links to
where it is fixed and can be **snoozed** (a week, a month, or skipped for that period); a snooze is stored
and audited, the items are not (ADR-0020).
_Avoid_: Reminders, Notifications, To-do, Alerts, Overdue (as a label)

**Due rule**:
A registered, deterministic check owned by one Capability: when its data is in use, it says what has gone
stale and where to fix it. It carries its own cadence for each Effort level, or none, and whether it is
important or optional. The expected frequency lives in the rule, not in a central list (ADR-0020).
_Avoid_: Reminder rule, Schedule, Plug-in (on its own)

**Effort level**:
A Household setting for how much Finance360 asks of the household: **Light**, **Steady** or **Thorough**.
Default Light. It scales how often each Due rule asks, and Light shows only important items. It chooses no
areas of interest and hides no screen or feature; it can be changed at any time and is audited (ADR-0020).
_Avoid_: Mode, Plan, Tier, Level (on its own), Frequency, Persona

**Ask panel**:
A panel opened from any screen that answers questions from the Guide (and, for a Written answer, the
current Due items), with sources the user can follow. It explains and points; it never changes data, and
says when it does not know. It works with nothing set up (Search only). When a model is used it sends no
balances, Valuations or Tax records to a provider (ADR-0021, ADR-0023).
_Avoid_: Chatbot, Assistant, Copilot, Guidance mode, Help

**Ask level**:
How much help Ask gives, chosen on the host and never per user: **Search only** (the default: it finds the
best Passages with no AI and sends nothing anywhere) or **Written answers** (a model writes the answer from
those Passages). Embeddings can improve Search only without changing the level (ADR-0023).
_Avoid_: Mode, Tier, Plan

**Passage**:
One section of a searchable source, currently a section of a Guide page, with a link to where it is. It is
the unit Ask searches, shows and cites (ADR-0023).
_Avoid_: Chunk, Snippet, Document

**Page reader**:
The deterministic matcher that proposes Rule figures from a pasted official page by matching words, when
no model reads it. It is not an AI call: nothing is sent or logged. Its proposals go through the same
checks as a model's and carry the origin "Matched by words on the page" (ADR-0023).
_Avoid_: Scraper, Parser (an Importer's parser reads a file), OCR

**AI gateway**:
The one internal layer every AI call passes through (keyword search is not an AI call and does not): it redacts names, numbers and amounts, admits only
the parts a data class allows, picks the provider and model from configuration, enforces the spend cap,
and writes the **AI log** (ADR-0005, ADR-0021).
_Avoid_: AI client, LLM wrapper

**AI log**:
The local record of every AI call: the redacted text exactly as sent, the reply as received, provider,
model, tokens, cost and reason. Separate from the Audit trail, which records changes to household data.
Kept for a configured number of days (ADR-0021).
_Avoid_: Audit trail (a different thing), Chat history

## Open questions

Concepts that **need a design decision before they get a name**.
They are deliberately not terms yet; see [`docs/catalogue.md`](docs/catalogue.md).

- Non-loan **obligations** (e.g. a novated lease and its residual).
- **Insurance policies** as contingent payouts.
- **Provisions** (money earmarked for a known future liability).
- **Reversionary** amounts on Income streams.
- **Dated terms** on loans (announced vs effective).
- **Redraw** as liquidity on a loan.

## Known but not adopted

**Asset class**:
In investment usage, the kind of underlying exposure (equities, fixed income, property, cash), used
as a *look-through analytics* dimension. It is **not** a synonym for Category: a Superannuation
Account belongs to one Category but spans many asset classes.

# Finance360 — Requirements & Scope

> Finance360's requirements, scope and working-model decisions, refined iteratively through
> structured elicitation sessions. Sections were added as each session completed; later sessions
> may revise earlier ones, and any such revision is dated inline.

**Status:** Draft. All four elicitation sessions are complete (2026-10-16). Roadmap status lives in
[`docs/roadmap/index.md`](roadmap/index.md).

| Session | Topic | Status |
|---|---|---|
| 1 | Vision & scope | Done 2026-10-16 |
| 2 | Functional requirements & roadmap | Done 2026-10-16 (§5a, §5b) |
| 3 | Non-functional requirements | Done 2026-10-16 (§5d) |
| 4 | Technology & hosting | Done 2026-10-16 (§5e) |

Domain terms in this document (Household, Member, Category, Account, Income stream, Capability, …)
are defined in [`CONTEXT.md`](../CONTEXT.md).

## 1. Purpose & origin

Finance360 is a **household wealth-planning platform** for one Australian household, built in an
Australian context. It is open source (AGPL-3.0) and designed to be self-hosted by a household that
wants to keep its financial data on hardware it controls.

It starts from a concrete need: investment platforms' own analytics are usually very simple. The
original idea was to extract holdings and transactions from those accounts and combine them with
publicly available product and pricing data, to see segmentation by market sector, asset class,
region and currency, mainly to spot **gaps and concentrations**.

The **primary motivation**, though, is **projection**. A household approaching retirement needs to
understand its **total financial position**: all assets and liabilities, with best/worst/likely
scenario projections. That supports planning for the investment and super balances it needs to
reach, and the retirement income it will need and for how long, with government entitlements such as
the Age Pension included.

Development explores individual financial areas in more depth over time, such as loans, property,
super and pensions from other countries. The architecture must allow that without rebuilding the
core.

## 2. Vision structure: Categories × Capabilities

The product is a grid. **Categories** are the kinds of things the household owns, owes or is
entitled to. **Capabilities** are cross-cutting engines that apply to every Category, at any level
of roll-up (Account, Category, Member, Household).

**Categories** (initial, open-ended):
Investments · Superannuation · Loans (including mortgage and offset) · Property · Vehicles · Cash &
savings · Pensions from other countries (private pensions and foreign State Pensions) · Age Pension
· later: income & expenses, tax, other debts.

**Capabilities** (initial, open-ended):

1. **Import & reconcile.** Structured and semi-structured sources (CSV, PDF statements, exports),
   with AI-assisted mapping, reconciled against statement totals.
2. **Valuation & reference data.** Prices, FX, fund composition, property estimates, interest rates
   and CPI.
3. **Position.** Assets and liabilities at a point in time and over history, sliced by Member,
   ownership, Category, currency and so on.
4. **Look-through analytics.** Sector, asset class, region and exposure currency, plus
   concentrations and gaps.
5. **Projection & scenarios.** Best/worst/likely cases, what-ifs (extra repayments, debt
   recycling, contribution levels), and retirement income needs and their duration.
6. **Goals & targets.** "What balance do we need by when," spanning the others. This is likely to
   become a capability in its own right.
7. **AI assistance.** Data wrangling, Q&A and narrative reporting over all of the above.

More of either may appear. Each Category plugs into the Capabilities. Going deeper in one Category
means adding richer rules and importers to it, not changing the core.

## 3. Principles

- **Household-first, open source.** Built for one household: no multi-tenancy, onboarding or
  productisation. The repository is open source under AGPL-3.0 and keeps the hygiene that makes that
  safe:
  - **No real financial data in the repo, ever.** Use synthetic sample data only.
  - **No personal identifiers in code or config.**
  - **Providers, importers and jurisdictions are plug-ins**, not hard-coded.
  - **Ways into the product.** The Guide's example starting points (such as keeping track of tax,
    of investments, or building the whole picture) make Finance360 easier for another household to
    adopt, without changing household-first.
- **Patterns over hard-coding.** Prefer the general mechanism (plug-in, rule pack, data-driven
  configuration) over special-casing a vendor, jurisdiction or account. Keep the *first
  implementation* thin: one plug-in, not a framework built for five.
- **Build the picture, then colour it in.** First get a broad, roughly right picture across every
  Category. Then deepen individual areas where need or interest leads. Depth waits. (Added in
  session 2.)
- **Thinnest change to an observable outcome.** Prefer reaching a visible result across the whole
  model over deepening one layer in isolation.
- **Household of Members, with ownership from day one.** Every Account and Income stream belongs to
  one Member or is jointly held. Super, preservation age, Age Pension eligibility (individual age,
  couple means test) and projections all depend on this. One login for now; separate logins only if
  ever needed.
- **AUD base currency, native currency per item.** AUD is the reporting currency and the default
  everywhere. An Account or Asset may override its own currency (e.g. a pension held in a foreign
  currency). Values are stored natively with dated FX rates.
  **Holding currency** (what an Account is valued in) and **exposure currency** (what the underlying
  assets are exposed to, derived from look-through) are separate dimensions.
- **Jurisdiction rules as pluggable rule packs.** AU first (super, Age Pension). Items governed by
  another jurisdiction start as valued assets with simple assumptions until deliberately deepened.
- **AI proposes, deterministic code disposes.** Every figure comes from deterministic code (parsers,
  valuation, projection engines). AI never writes figures directly. It:
  - **proposes** import mappings, which a person confirms and which then become reusable
    deterministic importers;
  - **explains and narrates** results;
  - **answers questions** by calling the system's own tools/APIs, possibly via MCP, rather than
    doing maths itself.

  The AI provider is swappable.

## 4. Non-goals

- **Transaction-level budgeting and spending categorisation.** Finance360 is a balance-sheet and
  planning tool, not a budgeting app. Expenses enter as **assumptions** and scenario inputs
  (e.g. current spend, and comfortable/modest retirement spend, possibly benchmarked against ASFA),
  not as a ledger. The maintenance burden of a ledger tends to kill motivation.
  Transactions are still imported where *investment accounting* needs them (buys, sells,
  distributions for cost base and performance). One narrow exception: a **register of deductible
  items**, kept through the year for tax planning. It records what is deductible, not all
  spending.
- **Trade execution or moving money.** Finance360 is read-only with respect to real accounts.
- **Lodging a tax return.** Tax planning is in scope: estimates and what-ifs for the income
  year, tax inside projections, and a worksheet that helps prepare the return and keeps the result
  for reference. Lodging with the ATO is not.
- **Financial advice.** It models scenarios. Decisions remain the household's. Whether tax guidance
  should go further than modelling is deliberately left open.

## 5. Sequencing direction (Path B: position first)

Agreed in session 1. Superseded in detail by the roadmap in §5b; kept for the rationale.

1. **Walking skeleton: household position.** Manually enter Accounts with dated balances across
   *all* Categories (investments, super, a mortgage and its offset, a property value, a pension in a
   foreign currency). Show them by Member, Category and Class with AUD roll-up. This proves the core
   model end-to-end in a UI.
2. **First import.** An investment platform's CSV exports → Holdings → live prices, replacing
   manual balances for that Account.
3. **First projection.** Deterministic growth on the household position, plus simple Age Pension
   and retirement-income assumptions. Best/worst/likely via varied return rates.
4. **Look-through analytics** on the imported Holdings.

After that the roadmap branches: going deeper on loans (mortgage/offset), super, property or foreign
pensions; Monte Carlo; Goals; AI-assisted importers.

**Chosen over Path A (analytics first):** Path A is deep on one Category, says nothing about
retirement until late, and risks shaping the data model around investments alone.

## 5a. Functional requirements (session 2, in progress, started 2026-10-16)

### Valuations & history

- Every Account's value over time is a series of **dated Valuations**. Each is tagged with its
  **source** and its **provenance** (which entry or import produced it). Sources:
  - **manual** or **statement**: observed;
  - **derived**: calculated from transactions × prices;
  - **estimate**: assumed, e.g. a house value.
- A Position at a date uses each Account's best available Valuation on or before that date.
- An Account may change source over its lifetime (e.g. manual → derived in M2) without losing
  history.
- **Reconciliation:** where an observed and a derived Valuation exist for the same Account and date,
  the difference is shown.
- **Seeing history (idea, noted 2026-11-08; not yet scoped).** The net-position history chart
  is one line. Later views that use every Valuation over time:
  - the **splits over time**: stacked by Category, Class or Member, through the same Position
    calculation, so the total at any date equals the Position;
  - an optional **CPI trend line**: the starting position grown by actual CPI (ABS), to show growth
    against inflation, and the same series behind a today's-dollars toggle for history;
  - **history and projection on one timeline**: actuals to today, then the Scenario's bands;
  - a mark where a manual Valuation was entered, since a manual Account steps rather than moves.
- The walking skeleton (M1) needs only manual Valuations. It shows a Position as at a chosen date
  plus a simple net-position-over-time view.

### Import & idempotency

- **Exact-file idempotency:** every source document is hashed. Re-importing the same file is a
  no-op.
- **Coverage-based supersession:** every Import declares its **Coverage** (source × Account × date
  range). A later Import overlapping an earlier one from the same source **supersedes the earlier
  one's records within the overlap**, rather than appending. Repeated "all time" exports therefore
  just work.
  - Chosen over append-only with row-level dedupe, because retail exports lack stable transaction
    IDs. Identical same-day transactions are legitimate.
- **Staged, then committed:** an Import lands staged, shows a diff against current data
  (added/removed/changed), and changes nothing until confirmed.
- Superseded Imports are retained (soft-replaced), so prior belief is always inspectable.
- **Importers** are deterministic plug-ins, one per source format. For unrecognised files, AI
  proposes a mapping. Once confirmed, the mapping is saved as a reusable **Import profile**, and
  later runs need no AI.

### Categories, Account types & Tags

- **Categories are data:** seeded with defaults and user-editable.
- **Account types are data that reference a code-implemented Behaviour**, e.g. *simple balance*,
  *holdings-based*, *amortising loan*, *offset-linked*, *super (rule pack)*. A new kind of account
  that fits an existing Behaviour (e.g. a pension from another country) is configuration. Only a
  genuinely new Behaviour needs code.
- **M1 implements only the *simple balance* Behaviour.** *Holdings-based* arrives in M2;
  *amortising loan* arrives when loan modelling is deepened.
- **Tags:** free-form, user-defined labels on Accounts (e.g. "retirement-earmarked", "emergency fund"),
  usable as roll-up dimensions. In scope from M1.
- **Accounts for children.** A Household may hold Accounts for children. How such Accounts are
  owned and rolled up is a modelling detail for the slice that introduces them.

### Ownership

- Ownership is a list of **(Member, share %)** entries summing to 100%. *Sole* means one Member at
  100%. *Joint* is an explicit ownership mode (joint tenancy passes to the survivor, which matters
  for future scenarios), defaulting to 50/50 but editable.
- Joint ownership is common, not an edge case: investment accounts, bank accounts and loans are
  often held jointly. It is a first-class ownership mode in the walking skeleton. Whether joint
  items need a non-50/50 split (e.g. for tax estimates) is a session 2 detail.
- Position offers switchable views: **by Member** (joint items split by share), **Joint** shown
  separately, and **Household** total.
- Some rules look at the Household (Age Pension means test uses combined couple assets); others look
  at the Member (super preservation, tax).
- Holding structures such as trusts or self-managed super funds are not modelled.

### Multi-file sources: Streams

Learned from real investment-platform exports. Real exports stay private; never commit them or
anything derived from them.

- One Account may be described by **several files**, each feeding a **Stream**. An investment
  platform typically has a *trade* stream (investment-transactions CSV: units, unit price,
  brokerage) and a *cash* stream (cash-transactions CSV: deposits, buy debits, distributions,
  interest, fees).
- **Coverage is source × Account × Stream × date range.** A fresh cash export supersedes only the
  cash Stream.
- **Cross-stream reconciliation:** every trade must match a cash debit (value + fee = cash
  movement). Mismatches are flagged in the staged diff.
- **Fees are recorded once.** A platform may report brokerage both as a column on the trade and as a
  separate cash fee row, and the buy's cash total may exclude the fee. Naive combination
  double-counts it.
- Importing one Stream without the other is allowed. The Account shows which Stream is stale or
  missing.
- **Export format quirks an Importer must handle** (observed in real exports):
  - `dd-MMM-yyyy` dates, possibly in descending order;
  - the cash file may be signed and the trade file unsigned;
  - distributions may carry the product *name* but no product identifier, so name→Instrument
    matching is needed;
  - distributions may be paid to cash rather than reinvested;
  - product type may differ by row kind (e.g. "ETF" vs "Share").
- **Validation:** Holdings derived purely from the trade stream must match the platform's displayed
  unit counts. Where exports reach back to account inception, no opening position is needed. The
  Importer must still *detect* and report a missing opening position rather than guess.
- **Transfers between Household Accounts** (e.g. a bank deposit funding an investment account) will
  double-count cash flows once both sides are imported. Recognising them is model-shaping but
  deferred (candidate milestone).

### Knowledge & the personal finance agent

- The long-term AI role is a **personal finance agent** that keeps track of the household's finances
  and gains capability as more data is assembled. The picture is *assembled over time*, and AI
  helps fill gaps that would be very hard for an individual to fill.
- **Knowledge** is first-class: annotations and facts, **never figures**. Each item attaches to an
  entity (Account, Import, Transaction, Member, Household) or stands alone, and carries provenance
  (*stated by a Member*, *inferred by the agent*, *from a document*).
- **Agent-inferred Knowledge is tentative until confirmed.** Confirmed Knowledge may be turned into
  structured, deterministic inputs (e.g. a confirmed opening position becomes a manual Valuation; a
  confirmed spending intention becomes a Scenario input).
- The agent acts only through the system's own tools/APIs, possibly via MCP.
- **Proactive behaviour** (watching and alerting) is undecided. It depends on scheduling/hosting
  (session 4).
- The M1 data model should allow notes on entities, so Knowledge is not a migration later.

### Projection model

- A **Scenario** is a named set of **Assumptions** over a time horizon. Best/Likely/Worst are
  **seeded Scenarios**, not special cases. Scenarios can be cloned and varied.
- **Assumptions are layered with inheritance:** Household default → Category override → Account
  override. A Scenario states only what differs.
- Return assumptions are **per Category in M3**, moving to **per asset class** once look-through
  (M4) exists.
- **Annual time steps in M3.** Monthly later, if loan modelling needs it.
- Results are shown in **today's dollars (real) by default**, with a nominal toggle. Inflation is
  itself an Assumption.
- **Deterministic in M3.** Monte Carlo is a later *engine* over the same Scenario/Assumption model.
- **Life events** on a per-Member timeline drive cash-flow changes. Some events are rule-fixed
  (preservation age and Age Pension age, from Rule pack + date of birth); others are Scenario
  choices (retirement age). Examples: retirement, preservation age, Age Pension age, a loan paid
  off, a transfer of a foreign pension, later downsizing, inheritance, death of a Member. Investment
  platforms offer similar "moments that matter" features.
- **Cash flows** follow from Life events: salary stops, super moves from accumulation to pension
  phase, Age Pension starts (means-tested annually against projected assets), and spending moves to
  retirement levels, optionally in **phases** (e.g. higher "active years").
- A **Drawdown policy** (an ordered, configurable list, varied by Scenario) decides which Accounts
  fund spending, e.g. income streams → cash → investments → super once accessible.
- **Headline outputs:** (1) do the funds last, and to what age, per Scenario; (2) sustainable
  retirement income in today's dollars; (3) gap to target. Output 3 requires solving backwards and
  belongs to the Goals milestone.
- **Pensions from other countries.** A pension held in another country may be kept as a
  foreign-currency income stream or transferred to an AU destination (e.g. super), in whole or in
  part, with FX applied at transfer. The transfer mechanics (e.g. eligibility rules, tax on
  transfer) belong to that jurisdiction's rule pack when the Category is deepened.
- **Salary-sacrifice super contributions** are supported as a contribution input to projections and
  to tax estimates.

### Look-through analytics

- Purpose: **understanding risk** (concentrations and gaps), not optimising individual lines.
  **Security-level look-through is out of scope**, not merely deferred.
- Each Instrument has a dated **Exposure profile**: percentage weights across asset class,
  region/country, sector and exposure currency, as published by the manager. **Fund-of-funds**
  (e.g. a diversified multi-asset ETF) reference their underlying funds' profiles and are
  **resolved recursively**.
- Composition data arrives through a reference-data provider plug-in, using the same Importer +
  Import profile pattern (download, parse, AI-assisted extraction, or manual). Mechanics: session 4.
- Outputs: breakdowns at Account/Category/Member/Household level by each dimension; concentration
  (top exposures); gap against an optional user-defined target allocation.

### Usage pattern

- **Episodic:** a monthly or quarterly "update the picture" session (imports, diffs, a glance at
  the Position), plus occasional planning sessions (Scenarios).
- **Desktop browser first.** Mobile is acceptable for read-only glancing; no native app.
- **One Member operates; others mainly view and discuss.** A shareable read-only view or exported
  point-in-time summary (PDF/CSV) may matter more than a second login.
- **Primary screens:** Home (headline position + projection) · Position · Accounts & Valuations ·
  Imports · Scenarios & projection · Analytics (from M4) · Settings (Members, Categories, Account
  types, Assumptions).
- **Automation where it earns its place**, without inflating the architecture.

## 5b. Roadmap (session 2, agreed 2026-10-16)

Milestones are sequential, demonstrable checkpoints. Expect refinement once real data capture
starts.

**M0 Financial inventory** (discovery; before M1 development; after sessions 3–4)
A structured session cataloguing every item that makes up the household's position. For each item:
what it is, owner(s), Category, candidate Account type/Behaviour, data source (CSV/PDF/web/manual/
API) and its change frequency, and how much it matters.
- **Private output:** the actual inventory, kept in a gitignored `private/` folder (set up before
  anything is written there), with sample documents in `private/samples/`. It is the anchor
  reference and the checklist for entering real data. It is also the **straw person for
  calculations**: app results are sanity-checked against it.
- **Repo outputs (non-identifying):** a seeded Category and Account type catalogue for M1, an
  Importer backlog ranked by value, and new glossary terms.

**M1 Household position (walking skeleton)**
Household and Members (name, DOB). Accounts (owners and shares, Category, Account type — all
*simple balance* — currency, Tags; create/edit/close). Dated manual Valuations with provenance.
Manual FX rates. Position screen as at a date: Assets/Liabilities/Net by Class → Category → Account;
Member/Joint/Household views; Tag filter. Net-position history chart. Seeded Categories and Account
types. A synthetic sample household in the repo.
*Done:* real Accounts entered in a household's own deployment show the true household net position,
by Member and Category, in AUD, today and over time.

**M2 Investment platform import**
Two-Stream Importer with the full idempotency model. *Holdings-based* Behaviour. Instrument
reference list (ASX-listed ETFs; the model stays open to unlisted funds). Daily end-of-day prices
back to the first transaction via a price-provider plug-in. Automated FX feed (manual override
kept). Reconciliation. Cash portion included in the Account value. **Deferred:** cost base/CGT and
performance (the platform's own basic charts suffice for now).
*Done:* dropping in the platform's exports and confirming the diff derives an investment Account's
value and history automatically, kept current by daily prices. Re-exports just work.

**M3 First projection**
Scenarios with layered Assumptions (seeded Best/Likely/Worst), Life events, salary and spending cash
flows, ordered Drawdown policy, **simplified Age Pension** (both tests, current indexed rates).
Annual steps, real dollars by default. Outputs 1 and 2.
*Done:* "are we on track for retirement?" is answerable, roughly, per Scenario.

**M4 Look-through analytics**
Exposure profiles with recursive fund-of-funds resolution, for **investments and super**. Each super
account's investment-option mix is entered manually for now. Breakdowns, concentration, gap vs
target. Per-asset-class return assumptions feed back into M3.
*Done:* what the household's invested money is actually exposed to (asset class, region, sector,
currency) is visible, with a fund-of-funds resolved through to its underlying funds.

**M5 Super import**
Super fund statements/exports (balance, contributions, fees, option switches) replace manual
super Valuations and option mix. Super is typically a household's largest investment.

**Candidate milestones** (unordered pool, picked when the current milestone closes):
- Tax planning: promoted to committed milestones "Tax planning" and "Tax in the plan", alongside M3 (ADR-0019; see `docs/roadmap/index.md`)
- Goals (solve for targets) — natural next pick
- Knowledge & agent v1 — natural next pick
- Mortgage & offset (*amortising loan*/*offset-linked* Behaviours, monthly steps, repayment
  what-ifs, debt recycling)
- Bank accounts and transfer recognition
- Property valuation
- Pensions from other countries (partial transfer, that jurisdiction's Rule pack)
- Age Pension in full
- Monte Carlo engine
- Investment accounting (cost base, CGT, performance)
- Representative budget (AI)
- Point-in-time export/summary for sharing

## 5c. Inputs noted for session 4 (technology & hosting)

- **Resources assumed available:** a development machine (optionally running a local model
  runtime such as Ollama) and a machine that can run Docker. Public or shared web hosting is
  optional and probably unneeded (see §5d). No need foreseen for AWS or other cloud.
- **Deployment optionality** across components is desirable, both for the household's own
  flexibility and for the open-source cause.
- **SQLite** is favoured for simplicity, but this stays open until all the elements are explored.
- Automation where it makes sense, without inflating the architecture.

## 5d. Non-functional requirements (session 3, agreed 2026-10-16)

### Data location & exposure

- **Real financial data lives only on hardware the household controls.** This covers the database,
  raw source documents and the `private/` inventory.
- **"Home" is an abstraction:** any host that can run a container (an Ubuntu-style Docker
  container). The development machine is also usable as a fallback host. Deployment optionality
  comes from this abstraction.
- **Remote access goes through a private overlay network** (e.g. a mesh VPN or WireGuard), never a
  public URL. Nothing holding real data is internet-exposed.
- **Public or shared hosting never holds real data.** It is limited to optional non-sensitive roles:
  project site/docs, a demo on the synthetic household, or a dumb store for encrypted backup blobs.
  Scheduled reference-data collection runs on the home host instead. An extra public compute host
  brings risk for little gain.

### AI data egress

- **Cloud AI is allowed, minimised and redacted by default.**
  - A redaction layer in front of every AI call tokenises identifiers (account numbers, names,
    addresses, payment references) and restores them in responses.
  - Figures and holdings are sent only when the task needs them.
- **Task-level data classes:**
  - *Import mapping:* file structure plus a few redacted sample rows, never whole files.
  - *Analysis/Q&A:* aggregates and holdings, never raw documents.
  - *Document extraction* (e.g. PDF statements): allowed, redacted where possible, flagged as the
    most sensitive class.
- **Every AI call is logged locally:** what was sent, which provider, why.
- **The provider is configurable per task.** A local model (e.g. Ollama) can take sensitive tasks,
  accepting a significant capability loss.

### Backup, recovery & durability

- **Precious:** the database, raw source documents (retained as imported so everything derived can
  be re-derived) and the `private/` inventory. Reference data is re-fetchable.
- **Backups are automated:** after every committed Import, plus nightly. They are **client-side
  encrypted** (e.g. restic/age) before leaving the host, with a key only the household holds.
- **Off-site target: a consumer cloud-storage service.** It stores encrypted blobs only. A second
  copy on a local machine gives fast local restore.
- **Restore is tested, not assumed:** a documented one-command restore onto a fresh container host,
  exercised at least once. This is part of M1's Definition of Done.
- **Targets:** at most one day of lost changes (in practice none); restore within an hour onto any
  container host.
- **Key loss means data loss.** The encryption key and recovery steps are kept outside the system
  (password manager) and are findable by every adult in the household who may need them.

### Correctness, auditability & reproducibility

- **Exact money arithmetic:** decimal or integer minor units, **never floating point**. Explicit
  rounding rules. Currency is always attached to an amount.
- **Audit trail for every change to real data:** create/edit/delete of Accounts, Valuations,
  Assumptions, and Import commits and supersessions. It records what, when and from which source
  (Member, Import, confirmed agent output). Nothing is silently overwritten.
- **Every figure is traceable** to its sources in the UI (e.g. units × price on date, from which
  Imports).
- **Reproducible projections:** a saved projection run records its exact inputs (Assumptions,
  Position as at date, Rule pack version, engine version), so past beliefs and the reasons
  forecasts changed are explainable. **Part of M3's Definition of Done.**
- **Rule packs are versioned and dated.** A projection names the rule set it used.
- **Testing:**
  - golden tests against known answers (platform unit counts, hand-checked amortisation, Services
    Australia's published Age Pension examples);
  - property-based tests for invariants (Holdings never negative; ownership shares sum to 100%);
  - the M0 inventory as an end-to-end sanity check.
- Judged worth the up-front weight: it pays for itself in use.

### Authentication, access & secrets

- **One local account:** strong password plus **optional TOTP**, with long-lived sessions on
  trusted devices. No external identity provider, OAuth or SSO.
- **Auth is a pluggable seam**, so alternative methods (e.g. reverse-proxy SSO) can be plugged in by
  others.
- **Roles:** *owner* and *viewer* designed from the start. Only *owner* is built in M1. *Viewer*
  serves a read-only household member and the shareable-view use case.
- **Secrets** (price-provider keys, AI keys, backup key):
  - never in the repo; supplied via environment or a mounted secrets file;
  - `.env.example` with placeholders committed;
  - pre-commit secret scanning (e.g. gitleaks), plus a guard blocking `private/` contents and
    known export file patterns (e.g. `*_transactions_*.csv`).
- **Dependencies:** updated by hand when chosen, with a weekly vulnerability scan in CI.
- **No telemetry or phone-home**, ever.

### Operability, portability & data ownership

- **One container host, minimal moving parts:** a single `docker compose up` on any container host,
  including locally for development. No Kubernetes, broker or extra services until one proves
  necessary.
- **Automatic versioned schema migrations on startup**, with an **automatic backup before every
  migration**. Upgrade = pull and restart; rollback = restore the pre-migration backup.
- **Scheduled jobs run inside the app**, not host cron: price/FX/composition refresh, backups,
  later proactive agent checks. Last-run status is visible. Silently stale data is a bug.
- **Observability scaled to one household:**
  - structured logs with no sensitive values;
  - a health endpoint;
  - a system status panel (last Import per Account, stale Streams, failed jobs, last backup, last
    tested restore).
- **Full export** of all data (Accounts, Valuations, transactions, Assumptions, Knowledge, audit
  trail) in an open, documented format (JSON/CSV plus raw documents). No lock-in.
- **Performance:** scale is small. The one target is that **projections recompute interactively
  (under ~2s)**. Monte Carlo may run in the background later.
- **Accessibility baseline:** keyboard navigable, readable contrast, charts with table equivalents.
  No formal compliance target.

### Repository visibility, licence & GitHub

- **The repository is open source under AGPL-3.0** (which protects against closed rehosting as a
  service, common for self-hosted personal-data tools). It holds **synthetic data only**; see the
  principles in §3 and the guards in §5e.
- **Issue types: `type:` labels**, not native GitHub Issue Types. Native types need an organisation,
  and one isn't worth creating for a single-maintainer repo. Reconsider a dedicated org if the
  project grows.
- **Public-repo hygiene:**
  - scan full git history (not just HEAD) for secrets and real data before any history is published;
  - keep a local, gitignored denylist of personal terms and run it over every change;
  - keep the README newcomer-facing, with a demo on the synthetic household.

## 5e. Technology & hosting (session 4, agreed 2026-10-16)

Captured as foundational ADRs 0001–0008 in [`docs/adr/`](adr/).

### Language & stack

- **Python backend, TypeScript UI, one deployable image.** Python covers the domain core (engine,
  Importers, API, MCP), where review is closest and fluency is highest. TypeScript only for the
  UI.
  - Backend: **FastAPI** with OpenAPI, built-in **`Decimal`**, **Hypothesis** (property-based
    tests), **uv**, **ruff**, **pytest**, **pyright** (strict).
  - UI: **React + Vite + TypeScript**. The **TS API client is generated from FastAPI's OpenAPI
    schema**, so the UI is typed end-to-end and backend changes break the UI build, not runtime.
  - **Single image:** a multi-stage build compiles the UI to static files that FastAPI serves
    (same origin).
  - Cost accepted: two toolchains (uv + npm) in one repo.

### Data

- **SQLite** (WAL mode, single writer process: the app including its jobs) via **SQLAlchemy 2 +
  Alembic**. Postgres stays possible as an alternative backend for others, but is not built for.
- **Money is stored as integer minor units plus a currency code**, through one custom column type.
  Never `Numeric`-on-SQLite, which silently becomes a float. **Unit quantities and prices are scaled
  integers or decimal strings** (ETF prices need more than 2dp, e.g. 55.0825). Round-trip exactness
  is enforced by property tests.
- **Encryption at rest comes from the host volume**, plus the always-encrypted backups. SQLCipher
  only if a threat later justifies it.
- **Raw source documents live on the filesystem, content-addressed by hash**, beside the database.

### Canonical data layer & reference data

- **A thin canonical (anti-corruption) layer:** all user-data Importers and reference-data providers
  map into a small set of canonical records (canonical transaction types, `Price`, `FxRate`,
  `ExposureProfile`, `CpiIndex`). Nothing beyond it knows the provider, except via provenance.
  Free data sources keep diminishing, so **every dataset's provider is assumed to change over
  time.**
- **Per-dataset provider precedence with fallback**, in config (e.g. prices: a free source → a paid
  provider → manual).
- **Instrument identifier crosswalk:** one canonical Instrument ID plus external identifiers and
  aliases (ticker, exchange-qualified ticker, ISIN, product name). This also resolves distribution
  rows that carry only a product name.
- **Initial providers:**
  - **Prices:** a free, unofficial price source (may break). Fetched prices are stored locally, so
    it's one request per Instrument per day after initial history. Upgrade to paid only if free
    fails.
  - **FX:** **RBA** daily rates, with a free secondary source as fallback.
  - **CPI:** **ABS**.
  - **Fund composition:** an AI-assisted factsheet Importer with manual confirmation, refreshed
    monthly or quarterly. Investigated with real data in M4.
- Every reference value carries provenance (provider, fetch time).

### AI integration

- **Claude by default** (Anthropic Python SDK). Model chosen **per task in config**: Sonnet 5.5 for
  most tasks, Haiku 5.5 for cheap high-volume classification, Opus 5.5 for hard document extraction.
- **Thin internal AI gateway** around every call: redaction, local AI log, provider/model selection,
  data-class enforcement. Ollama plugs in behind it. **No agent framework** (e.g. LangChain): the
  official SDK plus our own tools.
- **One service layer, exposed as tools two ways:** an **MCP server** (Python MCP SDK, read-only
  first, staged) and in-app tool use later.
- **Cost guardrails:** monthly spend cap in config; per-call cost recorded in the AI log.
- **Sequencing:** no AI in M1–M3. **A read-only MCP server is an early candidate milestone (soon
  after M1).** AI-written outputs wait for the Knowledge & agent milestone.

### Code architecture

- **Modular monolith** mapping the vision grid:
  - `core/`: domain model, Money, Ownership, audit.
  - `capabilities/`: one package per Capability (imports, reference, position, lookthrough,
    projection, goals, knowledge, ai).
  - `behaviours/`, `rulepacks/` (versioned by date), `plugins/` (importers, providers).
  - `api/` and `mcp/`: thin, over the service layer.
  - `jobs/`: in-app scheduler.
  - `frontend/`.
- **Explicit plug-in registry**, in-repo plug-ins first. Python entry points later if
  open-sourcing warrants third-party plug-ins.
- **Dependency direction enforced in CI** (import-linter): plugins → capabilities → core, never
  reverse. API/MCP/jobs call only the service layer.
- **Projection engine = pure functions over immutable inputs** (Position + Scenario + Rule pack
  version → result). This gives reproducibility; Monte Carlo is another engine.
- **One monorepo:** backend, frontend, `docs/` (requirements, ADRs, roadmap), `CONTEXT.md`,
  `.claude/agents/`.

### Frontend

- **shadcn/ui on Tailwind** (Radix-based, owned in-repo).
- **TanStack Query** (with the generated client), **TanStack Table**, **TanStack Router**.
- **Apache ECharts:** projection fan charts, stacked area, treemap/sunburst for recursive
  look-through, waterfalls.
- **Centralised money formatting** (currency, real/nominal labelling).
- **Light and dark themes from day one.**

### Development environment, CI & guardrails

- **Native development:** `uv` + `npm`, with one `dev` script (API auto-reload + Vite). The
  workflow works on Windows as well as Linux. Docker Compose locally only to test the production
  image.
- **Pre-commit:** gitleaks, the `private/`/export-pattern guard, ruff, prettier/eslint on staged
  files.
- **GitHub Actions on every PR:**
  - backend: ruff, pyright, pytest + Hypothesis, import-linter, Alembic up-to-date check;
  - frontend: tsc, eslint, vitest, **API-client drift check**;
  - security: gitleaks over the full PR range and the path guard, on every PR;
  - audit: pip-audit and npm audit, only when `pyproject.toml`, `uv.lock` or `frontend/package*.json`
    change (plus weekly);
  - Docker image build.
- **Dependency updates:** by hand; `pip-audit` and `npm audit` in the Audit workflow flag advisories.
- **Branch protection is instruction-only** unless the host plan supports real enforcement.

### Pre-merge testing: the review slot

Settled up front so testing happens **before merge, on the maintainer's machine**, not after
deployment.

- **Instance isolation** via `FINANCE360_DATA_DIR` and `FINANCE360_PORT`. No instance shares a
  database or port. Agents' automated tests use temp databases and random ports.
- **One fixed review slot:** a dedicated worktree at a fixed path, always on a fixed local port,
  with its own persistent data directory. Separate from the maintainer's main working copy and from
  agent worktrees.
- **`review <PR#>`**, a repo script run the same way every time:
  - `gh pr checkout` into the slot;
  - dependency sync if lockfiles changed;
  - back up the review DB, then migrate;
  - build the UI and start on the slot's port;
  - print the PR's "How to test" section.

  Also `review --stop` and `review --reset` (back to the synthetic household).
- **Data:** the synthetic sample household by default. **`--real`** restores the latest encrypted
  backup into a separate disposable data directory, wiped on stop.
- **"How to test" is mandatory in every PR** (PR template). Developer writes concrete steps with
  expected results against the synthetic household. **Tester validates them in a clean slot** before
  marking the PR ready.
- **Merge gate:** a PR template checkbox confirming the maintainer tested in the review slot. A
  Tester pass never substitutes for it.
- **The review tooling is built as one of M1's first tasks.**

### Deployment

- **The home host builds from git:** no container registry.
- **`deploy <tag>`** runs from the maintainer's machine over SSH (keys) via the private overlay
  network:
  - pre-deploy backup;
  - pull the tag;
  - `docker compose up -d --build` (startup migrations take their own backup);
  - health check;
  - report.

  Rollback = deploy the previous tag, plus restore the pre-migration backup if the schema changed.
- **Release tags, not every merge.** The maintainer chooses when real data moves to a new version.
- **Deploy script design.** Carry over:
  - gitignored per-host `target.local.env` with a fail-loudly placeholder check;
  - clean-tree check;
  - fast-forward-only pull;
  - re-exec after pull;
  - `GIT_SHA` in `/health`;
  - writing `.env` for ad hoc compose commands.

  Same-origin UI removes the build-time API-URL footgun.
- **Private overlay network** on the home host, the maintainer's machine and phones. The overlay's
  HTTPS serving feature provides HTTPS at a private name.
- **Container hardening:** non-root, the data volume as the only writable mount, secrets from a
  mounted file, a restart policy.
- **Backups to cloud storage via `rclone`**, configured once on the host. The in-app job encrypts
  before upload.

### Agent working model (inputs for repo setup)

- Role agents (Product Manager, Architect, Developer, Tester), CLAUDE.md
  conventions, git workflow (Claude never merges), ADR discipline, controlled vocabulary,
  milestones, sub-issues and `Inbox` triage, adapted to Python.
- **Sonnet by default** for role agents and routine passes (budget-conscious). Use Opus only
  where it counts (foundational design, hard ADRs, tricky debugging), flagged with a reason.
- Agents are spawned only when the maintainer asks.

## 6. Future ideas (recorded, not prioritised)

- **Mortgage & offset modelling:** repayment what-ifs, payoff projection, and offset as a
  first-class concept.
- **Debt recycling** as a scenario type.
- **Monte Carlo simulation** as an engine behind best/worst/likely.
- **Property valuation** feeds or estimates.
- **Pensions from other countries** (private pensions and foreign State Pensions) in depth:
  foreign-currency income stream vs lump-sum transfer.
- **Financial power of attorney and aged care, as a second perspective:** support someone
  managing another person's finances under a financial power of attorney, including aged care
  scenarios (care costs, accommodation payment options, the effect on pension entitlements), with
  that person's finances kept distinct from the household's own. A whole additional persona, not an
  extension of retirement projection.
- **AI-derived representative budget:** interrogate a bank export once to *propose* expense
  assumptions. Low priority.
- **Day-to-day budget advice** as an adjunct tool. Low priority; not to absorb major effort.
- **Open banking (CDR) aggregation** via an accredited data recipient. Probably not free for hobby
  use.
- **Third-party plug-ins.** A future decision, kept possible by the plug-in principles above.

## 7. Open questions (for later sessions)

Resolved 2026-10-16:

- **Retirement is modelled in Australia.** A foreign pension may be kept as foreign-currency income
  or transferred to AU, in whole or in part (see "Pensions from other countries" in §5a).
- **Joint ownership is common, not an edge case.** It is a first-class ownership mode in the
  walking skeleton (see "Ownership" in §5a). Holding structures such as trusts or self-managed super
  funds are not modelled.

Still open: none from the elicitation sessions. New questions go to GitHub issues (`Inbox`).

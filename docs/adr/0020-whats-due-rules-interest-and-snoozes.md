# 0020. What's due: Due rules owned by each Capability, Effort levels, implicit interest, stored Snoozes

**Status:** Proposed
**Date:** 2026-11-03
**Issue:** — (part of the Orientation milestone)
**Amended by ADR-0032:** the "Back to What's due" marker (section 5) becomes one per-tab return mechanism shared with Questions.

## Context

Users can lose the big picture, which loses confidence in the data, and then use stops. Nothing says
when a manual Valuation is old: Position quietly uses the last figure on or before the date. The
Guide's "What to provide, when" says as much and promises a screen "coming later".

What's due lists where the data Finance360 uses has gone stale or a new period has arrived, only for the
parts of the product the user already uses, and links each item to where it is fixed. Two stances shape
it:

- **Finance360 must not judge users by expecting more than busy lives allow.** Institutions rarely make
  export easy, so a fixed schedule is unreasonable. Wording is about accuracy ("your Position uses a
  super balance from April"), never failure ("overdue"), and the amount asked for is the user's choice.
- **Personalisation will come.** Nothing here may assume every user wants every prompt.

Constraints: ADR-0004 (plug-in registry, plugins → capabilities → core, import-linter), ADR-0009
(household changes are audited), ADR-0014 (page patterns), AGENTS.md rule 4 (no AI; prompts are
deterministic).

## Decision

### 1. Frequency lives in Due rules, contributed by the owner of the data

- A **Due rule** is a registry entry (ADR-0004 pattern, in-repo, registered in `main.py`, the composition
  root, like Importers and Behaviour hooks). It declares:
  - `key` (stable, dotted, e.g. `position.manual_valuation`);
  - `importance`: `important` or `optional`, **a fixed property of the rule** chosen by its author (the
    rule's accuracy cost if ignored), never inferred;
  - `cadence`: for each Effort level (section 3) a cadence or `None` ("not asked at this level");
  - `suits`: the Guide starting points it matters to (`tax`, `investments`, `whole`);
  - `guide_item`: the bold lead of its line in the Guide (section 5);
  - `interested(session) -> bool`: **implicit interest** (section 2);
  - `check(session, today, level) -> tuple[DueItem, ...]`: deterministic, reads only. `today` is the
    Household-time-zone date and `level` the Effort level, both passed in (no clock, no settings read
    inside). It returns nothing when the cadence for `level` is `None`.
- A **Due item** is `(rule_key, subject_key, importance, title, effect, due_on, link, suits)`.
  `subject_key` identifies the thing **and the period** (`account:12@2026-10`, `member:2@fy2026`), so
  snoozing one occurrence does not silence the next. `title` states what Finance360 is using ("Your
  Position uses a super balance from April"); `effect` says what refreshing improves ("A current
  Valuation makes your net position more accurate"). Neither says overdue, late or missed. `link` is an
  app path where it is fixed.
- The cadences sit in the rule's own module, as data beside the code that knows the domain, not in a
  central list. An owner with no cadence contributes no rule. A Category with no rule gives no prompt:
  silence, never a guessed default.
- `DueRule`, `DueItem` and the registry are pure types in **`core/due.py`**. Each owner puts its rules
  in its own package (`capabilities/position/due.py`, `capabilities/tax/due.py`), which imports core and
  its own Capability only.

**First-slice rules and cadences**

| Rule | Importance | Light | Steady | Thorough |
|---|---|---|---|---|
| `position.manual_valuation`: open Accounts in Cash, Loans & credit with no derive hook (value comes only from entered Valuations) | important | quarterly (92 days) | monthly (31 days) | monthly (31 days) |
| `position.super_valuation`: the same for Superannuation | important | half-yearly (183 days) | monthly (31 days) | monthly (31 days) |
| `position.slow_valuation`: the same for Property, Vehicles | optional | none | yearly (366 days) | yearly (366 days) |
| `tax.income_statement`: a Member with an `income_statement` Tax record in an earlier financial year and none overlapping the most recently ended one | important | from 15 July | from 15 July | from 15 July |

Why these:
- **Cash, super and loan balances are important** because Position and the projection start from them
  and they move, so a quarter-old figure is a visible error. Quarterly is the least that keeps them
  usable; monthly matches statements.
- **Super is half-yearly on Light:** super funds often send statements only half-yearly or
  yearly and rarely export, so asking quarterly would expect more than a Light user can easily give.
  It is its own rule, with its own Guide line, so its Light cadence can differ from cash and loans.
- **Property and vehicles are optional** because their figures are estimates that move slowly; nothing
  is asked on Light, yearly otherwise. An Account with no Valuation is due from its opened date; a
  closed Account never prompts.
- **The income statement is important** (it replaces the tax withheld and salary lines the Tax estimate
  relies on) and has one date because it is a yearly event, not a rhythm to scale: it arrives in
  mid-July, so it starts on **15 July**, not 1 July. Only the latest ended year prompts, so a
  first import of history gives no backlog.
- **Steady and Thorough are identical in this slice.** The level exists now so the setting is stable;
  Thorough will differ once rules whose rhythm depends on effort exist (each Import export, Prices and
  FX gaps, the restore exercise). No rule is invented to make them differ.

**Owners waiting** (each one rule in its own package, no design needed once this lands): Imports,
Reference (Prices and FX), Tax (a financial year with no Rule pack entry; other Record types have no
cadence), Backup (already in the sidebar footer), Income streams and Assumptions (a review, not a
cadence), the restore exercise (nothing records it). A per-Account cadence override waits.

### 2. Interest

- **Implicit (first slice, the only interest):** a rule is interested when data of its kind exists. No
  Tax records means no tax prompts, by construction. The rule decides, because only its owner knows
  what "in use" means.
- **Explicit interest does not exist in the first slice** and gets no name here. It has one seam: the
  service lists a rule when `rule.interested(session) or explicit_interest(rule.suits)`, where the second
  term returns false for now. It would only add prompts.
- **An Effort level is not interest.** It scales how much is asked of areas already in use; it does not
  choose areas and hides no screen or feature.

### 3. Effort level (a Household setting)

- An **Effort level** is **Light**, **Steady** or **Thorough**, stored as `household.effort_level` (a
  CHECK-constrained text column, additive migration, **default `light`**) and written through the
  Household service, so a change is audited as an ordinary Household update (ADR-0009). It can be changed
  at any time. Default Light means nobody is asked for more than the important items until they choose.
- Due Capability reads it from the Household row (core model) and passes it to each rule. Rules never
  read it.
- **Light shows only `important` items; Steady and Thorough show both.** That is the whole filter. The
  cadence table decides what is asked at all; importance decides what Light shows; they are separate
  because a Light user still has important items that are only asked quarterly or half-yearly.
- **Later, not designed here:** the app might suggest a different level from how the user behaves
  (for example, an item snoozed four times: "show it less often?"). It would always be a suggestion the
  user accepts, never a silent change. Stored Snoozes (section 4) make it possible later; nothing is
  built or reserved for it now.

### 4. Snoozing: stored and audited; prompts computed

- Prompts are **never stored**: evaluated on each request, so they cannot disagree with the data.
- One action, **Snooze**: one week, one month, or "skip this one" (`until_on` null: this period only,
  since `subject_key` carries the period). No separate "dismiss".
- **Stored:** `due_snooze(id, rule_key, subject_key, until_on nullable Date, created_at)`, unique on
  `(rule_key, subject_key)`; snoozing again updates it. Additive migration. An item is hidden while
  `until_on` is null or after `today`. Expiry is computed: no job, no cleanup. **Audited** (entity
  `due_snooze`; `create`, `update`, `delete`). Unsnooze deletes the row (audited).
- A rule that raises is caught per rule: the response lists it under `failed_rules` and the screen says
  "Could not check <rule>" in muted text. One rule's bug never blanks the list.

### 5. Where it appears, and tying it to the Guide

- **Home screen.** `/` stops redirecting to `/position` and renders What's due (one row of ADR-0014's
  route table changes). It is the first item of the sidebar group "Keep it current". Position stays one
  click away. *Placement changed later:* What's due, with its badge, now sits at the top of the
  sidebar beside the Guide link, and the Finance360 name links to it; a link from an item carries
  `?from=due`, and the app shell shows "Back to What's due" while it is present.
  - The screen **leads with the important items** under "What matters most", then "Also useful" (Steady
    and Thorough only). Rows show title, effect, the Position/Tax link and one overflow menu holding the
    Snooze choices (ADR-0014). A collapsed "Snoozed (n)" section offers Unsnooze. Empty state: "Nothing
    to refresh right now."
  - **Neutral presentation.** No warning, destructive or red styling anywhere on this screen, because
    nothing here is a fault. The sidebar count badge uses the **`info`** Badge variant (ADR-0014), not
    `warning`, and counts important items only. Existing lucide icons; no new tokens or iconography.
  - The **Effort level control** sits at the top of the screen: Light, Steady, Thorough, with one line
    saying roughly what each asks, and a link to the Guide question.
- **API:** `GET /api/due` (items, snoozed items, `failed_rules`, current `level`), `PUT
  /api/due/snoozes`, `DELETE /api/due/snoozes/{id}`; the Effort level is changed through the Household
  endpoint. Regenerate the API client.
- **No drift from the Guide.** The Guide stays prose in `frontend/src/guide/04-what-to-provide.md`. A
  backend test ties it to the rules. For every registered rule, there must be a Guide line that:
  1. contains `**<guide_item>**`;
  2. contains the cadence string **generated from the rule's table**, e.g. `Light: every 3 months ·
     Steady: monthly · Thorough: monthly`, with `not asked` for `None`;
  3. contains the marker `Shown in [What's due](/).`

  Conversely, a Guide line has the marker only if a rule has its `guide_item`. A new rule without a
  Guide line, a changed cadence the Guide doesn't state, or a promise with no rule fails CI.

### 6. Placement and dependency direction

- **`capabilities/due/`** owns What's due: `service.py` (evaluate the registry, filter by level and
  Snoozes, snooze and unsnooze, audit) and the `due_snooze` model in `core/models/`. It reads **no other
  Capability**; the owners' rules query their own tables.
- Import-linter edits (the Developer, in `pyproject.toml`):
  1. add `capabilities.due` to the auth and data-Capability independence lists, and forbid every data
     Capability from importing it;
  2. forbid `capabilities.due` from importing position, tax, projection, household, imports, reference
     (core only);
  3. `core.due` imports no Capability and no database model.
  No contract is weakened.
- `main.py` registers the rules beside the other registrations. A rule that needs a Rule pack looks it
  up in the registry (ADR-0018); none in this slice does.

### 7. First slice: two Developer passes, one branch, one PR

**Pass 1, backend.**
- `core/due.py` (types, registry, level and cadence helpers, cadence-string generator); `capabilities/due/`
  service; the migration (`due_snooze`, `household.effort_level`); audit; Household service accepts the
  level.
- The four rules above, with tests: injected `today`; cadence boundaries per level; `None` gives nothing;
  Light drops `optional`; closed Accounts; 14 and 15 July; interest absent gives nothing; snooze hide,
  expiry and per-period re-appearance; a raising rule is reported, not fatal; wording has no "overdue".
- The API, regenerated client, import-linter edits, the drift test (against the Guide as it will read; so
  pass 1 includes the `04-what-to-provide.md` edits it needs), and the fixture: `fixtures/sample_household/`
  gains a manually valued Account with an old latest Valuation, a Property or Vehicle Account likewise,
  and a Member with a prior-year income statement and none for the latest ended year. The fixture sets
  Steady so every rule shows; "How to test" flips to Light and back.

**Pass 2, screen.** The home screen, sidebar item and `info` count badge, the Effort level control,
Snooze menu and snoozed section, empty and failure states, screen tests, the remaining Guide changes
(section 8), and screenshots of the changed screens at 390px and desktop.

**Not in it:** notifications outside the app; AI; hiding or disabling any feature; explicit interest;
user-edited or per-Account cadences; learning from interaction or suggesting a level; an "upcoming"
look-ahead (`due_on` is on the item so one can be added); the waiting owners' rules; a background job; a
snooze history view beyond the Audit trail.

### 8. What the Guide must gain when the slice ships (the standing rule)

- **`01-start-here.md`:** a second question, **"How hands-on do you want to be?"**, describing Light,
  Steady and Thorough each as roughly how much time it takes and what accuracy you get back. Light asks
  only for the few things that move your numbers most, a few minutes every few months, and your figures are
  roughly right; Steady adds the slower-moving items, minutes a month; Thorough asks for everything
  Finance360 can use as often as it helps and is the most accurate. No level is better, and it can be
  changed at any time. The questions are about different things, so the existing starting-point question
  is unchanged.
- **`04-what-to-provide.md`:** replace "There is no reminder list yet…" with a pointer to What's due;
  remove "Nothing nags you if one is late"; give each rule's line the cadence string and the marker
  (section 5). Property and vehicles move from "Yearly" to a line that states "Light: not asked".
- **A short section** (in `04` or `06-position.md`) on the home screen: it is computed from what you
  have entered, asks only about what you already use, never judges, what Snooze does, and a check that
  cannot run is shown rather than hidden.
- **`03-how-the-pieces-fit.md`:** one line placing What's due; the accounts-and-imports page: one line.

## Consequences

- A new prompt is one rule in the owner's package plus a Guide line; the drift test makes both the line
  and its per-level cadence non-optional.
- Default Light means a new or busy user sees at most the few things that matter most.
- Stored state is the owner's Snoozes and one Household column, covered by backup and audit already.
- A late manual Valuation is finally visible, which is the stated failure.
- Interest is coarse: an existing manual Account; a Member with an earlier income statement. A first-year
  user gets no income statement prompt until one is on record. The Guide covers that case.
- Forecloses: a central frequency table; stored prompts; browser-only dismissal; AI-generated prompts;
  failure framing or colouring; hiding features through this mechanism; silent level changes.

## Alternatives rejected

- **Central rule list in the due Capability:** hard-coded, and it forces the Capability to import all the
  others, breaking the layering.
- **A fixed schedule for everyone:** judges busy users, as the Context says.
- **Cadence as editable data on Account or Account type:** right later for overrides, but needs a UI and
  migration with no first-slice gain.
- **Computed-only, no Snooze state:** dismissals return on reload and teach the user to ignore the list.
- **Stored prompts:** drift against the data.
- **An "areas I use" setting now:** unneeded for the examples and risks hiding behaviour the owner does not
  want hidden yet.
- **Importance varying per level or per Account:** more to explain, and not needed here.

## Not decided here / routine follow-on

- Exact copy, Snooze preset wording, lucide icon, API field names, and whether the level control also
  appears on Settings.
- The waiting owners' rules; the look-ahead; explicit interest and hiding of features (own ADR with
  personalisation); learning from interaction (a later slice, suggestion only); per-Account cadence.
- Snooze row clean-up (inert rows are harmless).
- Whether the Ask panel (O4) reads What's due: it may, through the same service.

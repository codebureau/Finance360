# 0032. Questions as the navigation spine: readiness, the next step, Ask prompts and Answer ratings

**Status:** Accepted
**Date:** 2026-11-23
**Issue:** #103 (epic #94; folds in the design of #72 and the Question part of #24)
**Amends:** ADR-0020 section 5 (the "Back to What's due" marker becomes one return mechanism, section 3)

## Context

People open Finance360 to understand their financial world. The maintainer (2026-11-23) calls the
risk make-or-break: if the owner feels they keep "feeding the machine", they stop. Every screen
today assumes the owner already knows which screen answers their question (#72), and nothing says how
far an answer can be trusted from what has been given, or what to give next.

The reference is 1Password Watchtower: stat cards, colour by severity, drill-in. A card per
Question ("Are we on track for retirement?") with a red, amber or green status, a reason and one
next step; the same card doubles as a canned prompt for Ask. Beside it, the owner's own say:
"did this answer your question?". Good data that does not answer the owner's questions still needs
improving, so usefulness is a second axis, not part of the colour.

A dead end that motivates the next-step rule (invented values): an Account's look-through card read
"Not analysed. The trade Stream's Coverage ends 2026-03-31, before 2026-06-30: it is stale. The cash
Stream has no committed Import." Two reasons, the less useful first, as prose, with no link to the
fix (`core/derived.py`, `stream_issue`).

What exists: Due rules and Effort levels (ADR-0020, a registry by owner Capability, computed items,
stored Snoozes, a drift test against the Guide); Ask with Search only by default and a Guide
golden test (ADR-0021, ADR-0023); tolerant judgments with named constants (ADR-0030,
`RECONCILE_TOLERANCE`); the stat tile with severity, status tones and Display preferences
(ADR-0031); the Guide page "What Finance360 answers", which lists each question in bold with its
screen. Constraints: ADR-0004 (registry, plugins → capabilities → core, import-linter), AGENTS.md
rules 3 to 5, ADR-0020's stance that nothing judges the owner.

The maintainer settled these calls (2026-11-23): Not started is its own info-toned state, not red;
stale data never makes a Question Not ready; the thresholds start at 5% and 25%; snoozed items do not
count; the Guide keeps its prose, guarded by a drift test; "Ask about this" asks at once; the term is
Answer rating; the return bar persists per browser tab.

## Decision

### 1. The Question registry, contributed by the Capability that answers it

A **Question** is a registry entry (ADR-0004, registered in `main.py` like Due rules). Pure types,
the registry and the readiness arithmetic live in **`core/questions.py`**. It declares:

- `key`: a stable slug, also the URL (`own-and-owe`, `on-track-for-retirement`, `tax-this-year`);
- `wording`: the Question in the owner's words, **the same string as its bold lead on the Guide page
  "What Finance360 answers"** (section 5);
- `screen`: the app path that answers it (`/position`);
- `area`: the sidebar group the screen sits in (for its icon chip colour, ADR-0031 section 5);
- `inputs`: an ordered tuple of **Question input** keys, in the order they are best provided;
- `ask_prompt`: the canned Ask question (section 4), words only.

A **Question input** is a second registry entry, owned by the Capability that owns the data, so
two Questions can share one (both Position and the projection start from Position's figures). It
declares `key` (`position.figures`), `name` ("Account figures"), `importance` (`important` or
`optional`, fixed, as a Due rule's), `due_rules` (the Due rule keys whose items it weighs, possibly
none), and `check(session, today, level, due_items) -> InputResult`, deterministic and read-only.
The Questions service passes in the current Due items for `due_rules`, already filtered by Effort
level and Snoozes, so staleness is judged once (by the Due rule) and never re-derived.

An `InputResult` holds `empty` (no data of this kind at all), `blocking` (the answer cannot be
computed), `total` (the input's whole value as exact Money in the Base currency, or none when it
has no value) and `reasons`. A **reason** is structured, never prose alone: `kind` (`missing`,
`stale`, `incomplete`), `subject_key`, `title` and `effect` (accuracy wording, ADR-0020: what the
answer is using and what fixing improves; never overdue, late or failed), `affected` (exact Money,
or none when the value is unknown), and `link` (the app path where it is fixed; required).

Placement and layering:
- **`capabilities/questions/`** evaluates the registry, orders reasons, holds the Answer rating
  table's service (section 6). It imports core and `due` (through the due service) only; it never
  imports an owner. Owners put entries in `capabilities/<owner>/questions.py`, importing core and
  their own Capability.
- Import-linter (the Developer edits `pyproject.toml`; none weakened): add `questions` to the
  independence lists; `questions` imports only core and `due`; nothing but the API and `main.py`
  imports it; `due` and the data Capabilities never import it; `core.questions` imports no
  Capability and no database model.

### 2. Readiness: computed, tolerant, value-weighted, no score

**Readiness** is how far a Question can be answered from what the household has given. It is
**computed on every request and never stored** (as Due items), so it cannot disagree with the data.
Five states, each with a status tone, an icon and a word (ADR-0031 section 4):

| State | Tone | Meaning |
|---|---|---|
| **Ready** | `success` | The answer uses what it needs; any gap is immaterial. |
| **Partly ready** | `warning` | The answer is there but rests materially on old, missing or incomplete figures. |
| **Not ready** | `destructive` | The answer cannot be computed, or leaves out so much it would mislead. |
| **Not started** | `info` | Nothing of this kind has been given yet; the next step is the first input. |
| **Could not check** | muted | A check failed; shown, never hidden, never reported as Ready. |

**Rating an input** (pure, in `core/questions.py`, thresholds as named constants):

- `MATERIAL_SHARE = 5%` and `MISLEADING_SHARE = 25%`, held as parts per million. A reason's share is
  `affected / total`, in integer arithmetic on exact Money. Shares are a judgment: no reason is
  dropped, and the Question page lists immaterial ones muted under "Small gaps".
- `empty` gives **Not started**. `blocking` gives **Not ready**.
- `missing` reasons (value the answer leaves out): above `MISLEADING_SHARE` in total, **Not ready**;
  above `MATERIAL_SHARE`, **Partly ready**.
- `stale` and `incomplete` reasons (the answer uses an old or partial figure): above
  `MATERIAL_SHARE`, **Partly ready**. **They never give Not ready**: an old figure makes an answer
  approximate, not wrong, and a red card for an unrefreshed balance is exactly the judging ADR-0020
  forbids.
- A reason with unknown value (`affected` none: an Account never valued, a label not modelled)
  gives at most **Partly ready**, unless its input says `blocking`.
- Otherwise **Ready**.

**Effort level and Snoozes.** An input weighs only the Due items What's due would show: a rule not
asked at this level returns nothing, Light drops `optional` rules, and a Snoozed item does not count
(the Question page lists it muted as "Snoozed"). An `optional` input never changes the state at
Light. So a Light household is never Partly or Not ready for what Light does not ask.

**A Question's state** is its inputs combined: any Not ready gives **Not ready**; else any Could not
check gives **Could not check** (an unchecked input is never assumed fine); else any Partly ready
gives **Partly ready**; else, if every important input is Not started, **Not started**; else
**Ready**. Once a Question has started, an important input still Not started counts as Partly
ready. Its **count** is the inputs that are not Ready. An input whose check raises is caught on
its own (ADR-0020 section 4) and reported by name.

**No numeric score, anywhere.** No percentage of readiness, no points, no weighting of Questions
against each other. The shares above are thresholds inside a judgment, never shown as a figure.

### 3. Navigation: Questions lead, and the next step is always one click away

- **The next step** of a Question is its first reason by this order: inputs in the Question's
  order, Not ready before Partly ready; within an input `missing` before `stale` before
  `incomplete`; then larger `affected` first, unknown value last. The card shows only the next
  step; the Question page shows every reason with its link. (In the dead end above, the cash Stream
  with no Import comes first, as one link to that Account's Imports.)
- **Question page** `/questions/$questionKey`: the readiness stat tile (state, count, one-line
  reason, "See the answer" to `screen`), then the inputs in order, each with its state, its reasons
  and their fix links, then "Ask about this" and the Answer rating (section 6).
- **Sidebar group "Your questions"**, first under What's due, one item per registered Question in
  registry order, the heading linking to the Guide page "What Finance360 answers". Each item's icon
  takes the colour of the group its `screen` sits in; no new token.
- **One return mechanism ("Back to your next step").** A fix link from a Question card or page
  carries `?from=q.<key>`; a What's due link keeps `?from=due`. On arrival the shell stores the
  return target **per browser tab** (`sessionStorage`) and shows a return bar on every screen until
  the owner goes back, dismisses it, or follows another return link: "Back to your next step: <the
  Question's wording>" or "Back to What's due". It survives navigating within the fix (an Account,
  then its Import, then a dialog) and a refresh, which the URL marker alone does not. This replaces
  the URL-only rule in ADR-0020 section 5. Returning refetches, so the card shows the improvement and
  the next step moves on.
- **Where cards appear:** Question cards lead Home (`/`) above the What's due items (V6, #104).
  #101 groups Due items by Question through each input's `due_rules`.

### 4. Ask prompts: a fixed string per Question

- "Ask about this" on a card and a Question page opens the Ask panel and **asks at once** the
  Question's `ask_prompt`, with `screen` set to `/questions/$questionKey`. The asked text appears as
  the owner's turn and follow-ups work as usual.
- `ask_prompt` is a **static string in the registry**: no readiness, no reason text, no Account or
  Member name, no figure. A backend test asserts no prompt contains a digit run or currency amount.
  So nothing reaches Ask that ADR-0021's `guidance` class does not already admit; Written answers
  still add the current Due items as today, and Search only sends nothing anywhere (ADR-0023).
- **Findability:** each `ask_prompt` joins ADR-0023's golden test, required to return the Guide
  section for its Question in the top three. This is the Question part of #24.

### 5. No overall gauge (for now)

Considered: one Watchtower-style gauge that only summarises the Question states. **Rejected for
now.** With three to six Questions every card is visible at once, so a gauge adds nothing the cards
do not show, while it invites working the number up instead of answering the Questions, hides
which Question is the problem, and reads as the data score the issue rules out. Instead the Home
card group carries a **headline sentence** computed from the states ("2 of 3 questions are ready;
Are we on track for retirement? is partly ready"). Revisit when more than six Questions are
registered: a summary would then only count states, never weigh them.

### 6. Answer ratings: the owner's say, stored apart

- An **Answer rating** is the owner's Yes, Partly or No on whether a Question's answer was useful,
  with an optional note ("what were you hoping to see?"). It is a second axis beside Readiness and
  **never combined with it**: a Ready Question rated No is the signal that matters most (good data,
  not useful), and blending the two would be a score.
- **Stored** in its own table, `answer_rating(id, login_id FK, question_key, answer CHECK in
  ('yes','partly','no'), note nullable ≤ 200 chars, readiness_at_rating, created_at)`, append-only:
  the latest row per Question is current, earlier rows are the history. `readiness_at_rating` is the
  state word at the time, so "rated No while Ready" can be read later. One additive migration;
  downgrade drops the table, so rollback needs no restore.
- **Not the Audit trail** (it changes no household data) and not a Display preference (it holds
  text). In the database, so in the Backup; never sent anywhere, not read by Ask, not exposed over
  MCP, no telemetry.
- **No financial values in the note.** The screen hints so; the service refuses a note with a
  currency amount or a run of six or more digits (ADR-0021's redaction patterns, reused), with a calm
  message, never a silent edit.
- An unrated Question shows "Not rated" in the muted tone, never red. Ratings are read on one
  screen, `/questions/ratings`, as plain text the owner can copy. Acting is the opt-in: nothing asks
  for a rating.
- API: `POST /api/questions/{key}/ratings`, `GET /api/questions/ratings`. `ratings` is therefore a
  reserved word, never a Question key.

### 7. A seam for notices, not a store

#102 ("you added X, which unlocked Y") may call the Questions service before and after a write to
see a state change. Readiness stays computed; this ADR adds no status history and no notices
table, and decides nothing about #102's store.

### 8. First slices

**#104 (R1, with V6 Home)** builds: `core/questions.py` (types, both registries, the rating and
ordering functions, the constants); `capabilities/questions/` service; `GET /api/questions` (each
Question's state, count, next step) and `GET /api/questions/{key}` (inputs and reasons);
import-linter edits; the Question page, Home cards (stat tile with severity, ADR-0031), the sidebar
group, the return bar, "Ask about this"; three Questions:

| Question | Screen | Inputs, in order |
|---|---|---|
| `own-and-owe` | `/position` | `position.accounts` (any open Account; else Not started) · `position.figures` (Accounts with no usable figure today, `missing`, weighted by last known value) · `position.freshness` (`stale`, from the three Valuation Due rules, weighted by value) |
| `on-track-for-retirement` | `/projection` | `household.birth_dates` (`blocking` without them) · `position.figures` · `position.freshness` |
| `tax-this-year` | `/tax` | `tax.records` (any Tax record; else Not started) · `tax.income_statement` (the Due rule) · `tax.rule_figures` (the Rule figure Due rules) · `tax.modelled` (unmodelled labels, `incomplete`, value unknown) |

Tests: injected `today`; each threshold boundary at 5% and 25% exactly; stale never Not ready; Light
ignores optional; a Snoozed item does not count; a raising check gives Could not check; reason
order; no digits in any `ask_prompt`; the Guide drift test (below). The fixture gains a state per
card: one Ready, one Partly ready (a stale super Valuation on a material share), one with a small
stale Account that stays Ready.

**#105 (R2)** builds the `answer_rating` table and migration, its two endpoints, the Yes / Partly /
No control on the Question page and card, the latest answer beside the state, and the ratings
screen.

**Waits:** more Questions and inputs (each is one entry in its owner's package plus a Guide line);
`reference` inputs (Prices and FX gaps); an answer headline on the card itself; AI interpreting a
free-text question into a Question (#72's step 2); a periodic rating check-in; a gauge.

**No drift from the Guide** (as ADR-0020 section 5). A backend test reads
`02-what-finance360-answers.md`: every registered Question's `wording` appears as `**<wording>**`
on a line that links its Question page (`/questions/<key>`), and every such link has a registered
Question. The rest of the page stays prose.

## Consequences

- A new Question is one entry in the answering Capability plus a Guide line; a new data gap is one
  input in the data's owner. Readiness logic and thresholds live in one pure module.
- The owner always sees why an answer is not ready, in order, with one link to the fix, and can
  return to it from anywhere. Wording describes the answer, never the owner.
- Value weighting means a small stale Account does not colour a Question; stale data alone never
  turns one red; Light is never marked down for what it does not ask.
- Readiness and usefulness stay separate, so a Ready Question the owner rates No is visible.
- Costs: one table, four endpoints, one new Capability, and owners' reasons must become structured
  (`stream_issue` returns kinds and links, its prose built from them).
- Forecloses: a numeric data score or readiness percentage; stored readiness; an overall gauge in
  this design; red for stale data; ratings in the Audit trail or sent off the instance; prompts
  carrying household figures or names; a central list of Questions.

## Not decided here / routine follow-on

- **From the issue's Outcome, routine:** exact copy for states, reasons and the headline; lucide
  icons for each state and for "Ask about this" (existing set, no new iconography); API field names;
  the Question page's layout within the view card pattern.
- **Design-language additions** (documented in `docs/ui-conventions.md` by #104): the Question card
  (a stat tile with severity, next step, "Ask about this"), the return bar, and the five readiness
  words with their tones. No new tokens.
- **Named passengers on #104:** `stream_issue` returns structured issues ordered missing before
  stale, with a link to the Account's Imports, and the look-through Not analysed card uses them (the
  dead end above); the What's due back link moves to the return bar.
- **Guide (standing rule, #104):** page 02 gains the Question page links and a short "How ready is
  each answer?" section (the five states, value weighting, Light, Snoozes); `01-start-here.md` points
  to Your questions; `03-how-the-pieces-fit.md` places them. #105 adds a line on Answer ratings.
- Whether Ask (Written answers) should also read readiness: not now; it would need its own data
  class review.
- The rest of #24 (its non-Question search tuning) and #72's AI routing.

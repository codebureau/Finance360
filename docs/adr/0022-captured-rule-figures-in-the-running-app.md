# 0022. Captured Rule figures: AI-guided capture in the running app

**Status:** Accepted
**Date:** 2026-11-05
**Amended by ADR-0023:** section 5 (the AI step) gains a non-AI page reader used when no model is configured or available; `method` gains `matched`.
**Amended later (Start from a prior year):** section 4 gains `carried` captures (start a year from last year's figures, provisional until confirmed) and exact source pages.
**Issue:** — (revisits ADR-0018 section 6)

## Context

ADR-0018 decided that rule data ships as code: a yearly figure reaches the app by script, PR, CI,
release and deploy, and the database may not hold rule data. That kept saved runs reproducible and
golden tests honest. It does not survive real operation. Rates move at least yearly (car and home office
from 1 July, the Age Pension twice a year), the official pages block automated fetches so a person must
supply the page anyway, and a household that cannot record a new car rate until the next release is
blocked on a number it can read today. The owner's verdict was "not sustainable", with a wish that it
could be AI-guided.

What already exists makes this small. `FigureSpec` (page, where to look, unit, plausible range,
year-on-year limit) declares each key; `rulepacks/capture.py` parses, range-checks and flags a jump;
`Param`, `retrieved` and `declared` carry the trace shown on a record ("How this is worked out");
saved runs and estimates already snapshot every value they used (ADR-0016, ADR-0018 section 4). The AI
gateway (ADR-0021) supplies redaction, the model, caps and `ai_log`.

Agreed with the owner before this ADR: the shipped pack stays the tested baseline; in-app captures layer on
top, audited; AI-guided is the main path, deterministic code checks, a person confirms; a typed fallback
exists; traceability holds; What's due flags uncaptured figures. This ADR decides the rest.

## Decision

### 1. Storage: one append-only table, merged in `core`

**`rule_capture`** (core model, additive migration):

| Column | Notes |
|---|---|
| `id`, `jurisdiction`, `key`, `fy` | The figure: a declared spec key for an FY (ADR-0018 key, FY year-ending). |
| `value_json` | The canonical `value_json` of `core.rulepack` (integers and strings only; no float). |
| `provisional_note` | Null for a firm figure; a note when announced but not yet law. |
| `quote` | The sentence the figure came from. **Optional for typed capture, required for AI capture** (amended 2026-11-05 with the owner: typing a figure from the page is its own evidence; an AI proposal needs the sentence to be checkable). |
| `page_url`, `retrieved_on` | The page the person read, and the day (defaults to today, editable). |
| `method` | `typed` or `ai` (ADR-0023 adds `matched`: read by the deterministic page reader). |
| `ai_log_id`, `ai_value_json` | For `ai`: the log row, and what the AI proposed, so a corrected value is visible. |
| `captured_at`, `captured_by` | UTC time and the actor (as `audit_event`'s `actor_kind`/`actor_ref`, ADR-0009/0010). |
| `ended_at`, `ended_by`, `ended_kind`, `superseded_by_id` | Null while active. `ended_kind` is `withdrawn` or `superseded`. |

- **Append-only.** A row is never edited, only ended. **Correcting** a figure captures a new row for the
  same `(key, fy)`, which supersedes the old one. **Withdrawing** ends a row with no replacement, so
  the figure falls back to the shipped value or to "not captured". At most one active row per
  `(jurisdiction, key, fy)` (a partial unique index). Every capture, supersession and withdrawal also
  writes an `audit_event` (ADR-0009).
- **The merge is a pure function in `core/rulepack.py`**: `layer(pack, captures) -> RulePack`, which
  returns a new pack with the active captures placed in `years`, `provisional` and `retrieved`, plus a
  per-figure origin. It passes through `RulePack.__post_init__`, so the merged pack is validated exactly
  as a shipped one. A new core module **`core/rulepack_store.py`** (core may use the database) reads the
  active rows and exposes `effective_pack(session, jurisdiction)`. **Services call that instead of
  `rulepack.get`** and still never import `rulepacks/`. Registration is unchanged.
- **The specs move to the registered pack.** `RulePack` gains `specs: tuple[FigureSpec, ...]` (trace
  metadata, outside the content hash, like `declared`), and the pure parts of `rulepacks/capture.py`
  (`parse_figure`, `format_figure`, `large_change`, `FigureError`) move to `core/figure_capture.py`, so a
  capability can validate without importing `rulepacks/`. The script keeps the code that renders a data
  module. No behaviour changes in the move.
- **A capture that no longer validates** (a release changed the key's kind or range) is ignored by
  `layer`, never raises, and surfaces as a Due item ("Captured figure X no longer fits; re-capture").
  A bad row must not stop the app starting.

### 2. Precedence: the captured figure wins, and a difference is flagged

Where a release ships a figure the instance already captured:

- **Same value:** the capture is redundant. `layer` skips it (computed at read, nothing written) and
  nothing is flagged.
- **Different value:** **the captured figure wins and a Due item flags the difference** ("Release r7
  ships a different car rate for FY 2026-27 from the one you captured") with two actions: **Use the
  shipped figure** (withdraws the capture) or **Keep mine** (records an acknowledgement on the capture
  against that revision, so it is not raised again until the next revision changes the shipped value).
- **Exception:** a *provisional* capture against a *firm* shipped figure loses. The announcement has
  become law and the release carries the settled number; the capture is treated as redundant and the
  Due item reads "now firm in r7".

Reasoning: a captured figure was read from the official page by this household, quoted and confirmed;
silently replacing it under a saved estimate would change numbers nobody chose to change. Equally a
shipped figure is maintainer-checked, so a difference is a real signal worth a human look, and it is
visible rather than resolved by whichever is newer. Rejected: shipped always wins (discards a confirmed
figure and hides the reason it differs) and newer wins (arbitrary and invisible).

### 3. Reproducibility and the version label

- `version_label` stays `<pack>/<last FY>/r<revision>` for a pack with no active captures, so every
  existing label and pinned test is unchanged. With captures it reads
  **`au/2026-27/r6+3:a3f9c1`**: `+<n>` is the number of active captures that change the effective pack
  and the suffix the first 6 hex of the **capture digest**, the SHA-256 of the canonical JSON of those
  captures' `(key, fy, value, provisional note)`, sorted. Any capture, correction or withdrawal changes
  the digest, so two runs with the same label used the same figures. `last FY` is taken from the
  effective pack, so capturing a new year moves it.
- `content_hash()` of the shipped pack is untouched (ADR-0018 enforcement still pins `(revision, hash)`
  of shipped data only). The effective pack's hash is shipped hash plus capture digest.
- Saved runs and estimates keep snapshotting every value used. Their provenance (ADR-0018 section 4)
  gains per figure `origin` (`shipped` or `captured`) and, for captured, the capture `id`. Rows are
  never deleted, so the id always resolves to the quote and page. `rule_pack_version` stores the longer
  label; nothing else about a saved run changes.
- The record trace card (`FigureUsed`) gains `origin`, `quote`, `captured_by`, `capture_id` and, when
  a shipped figure also exists and differs, `shipped_value`. The card shows "Captured here on <date> by
  <actor>: '<quote>'" or "Shipped in r6". The existing `retrieved` and `page_url` come from the row.

### 4. Which figures can be captured

- **Any key with a declared `FigureSpec`, for any FY**, in any jurisdiction whose pack declares specs.
  Today that is the AU `tax.` keys; the Age Pension (B11) and later keys join by adding specs, with no
  change here. Ages (`AgeRule`) are law functions of date of birth, not yearly figures, and **cannot be
  captured**. A key with no spec cannot be captured.
- **A new FY is just a capture for an FY the shipped pack lacks.** The allowed range of `fy` is from the
  pack's first FY to **the current FY plus one** (next year's announced figures, provisional). The year
  checklist lists missing required keys, so completing a tax year is the same screen as one figure.
  `build_rules` (exact-year, no carry-forward) and `figures_waiting` read the effective pack, and the
  "how to capture" text points to the capture screen instead of the script.
_Amended later: starting a year from earlier figures, and exact source pages._

- **`method = carried`** (a fourth value beside `typed`, `ai`, `matched`; the column's check constraint
  widens by migration). It is written **only by an explicit owner action on the year checklist,
  "Start from <FY>"**, which previews every proposal and writes nothing until accepted. Each accepted
  key becomes **one ordinary capture row** (same table, audit event, partial unique index) that is
  **provisional** with the note **"Same as <FY>, not checked against the page"**, no quote, the spec's
  `page_url`, and `retrieved_on` of the source figure.
- **`build_rules` stays exact-year.** Nothing carries forward at read time. A year with no capture row
  and no shipped figure still reads nothing and still says "not captured"; a carried figure is a visible
  row the owner chose to write. A test asserts no figure is ever read for a year lacking a row or a
  shipped figure.
- **Source: per key, the nearest earlier FY** whose effective pack (shipped or captured) holds that key,
  the same lookup as `previous_in` for the year-on-year check. Not one FY for the whole set, because a
  key added later (or a gap) would otherwise block the rest. The button names the latest earlier FY with
  any figure ("Start from 2025-26"), each preview row names its own source FY, and the note names that
  FY. A key whose nearest earlier figure is itself carried may be carried again (the note names the
  immediate source and the row is still provisional); a provisional source note is appended. A key with
  **no earlier figure** is not proposed: it stays missing, listed "no earlier figure; capture it". Keys
  already holding an active row for the target FY are never touched.
- **Confirming is a supersession, not a status flip.** The carried row is ended (`superseded`) and a new
  row is written for the same `(key, fy)` with `provisional_note` null, through the existing
  `correct_figure` path and validator. Reasons: the model is append-only with one audit event per
  change, so a flip would be the first in-place edit of a row; and the ended carried row stays as
  history ("carried from 2025-26, confirmed on <date>"). Variants:
  - **Unchanged, without re-typing:** same value, `method = typed` (the owner attests, as typed entry
    already is: quote optional), `page_url` the spec's page, `retrieved_on` today.
  - **Paste the page:** the page reader (ADR-0023) runs over the carried keys; an agreeing figure is
    written `matched` with its quote; a differing one is shown as changed and needs the usual explicit
    tick (year-on-year flag applies).
  - **Changed:** corrected as today (`typed`, `ai` or `matched`).
  - **Per source page in one action:** all carried keys sharing a `page_url`, confirmed unchanged in
    one transaction, one audit event per key. A figure whose spec has a scheduled change (below) is
    **excluded from the bulk action** and must be confirmed on its own row.
- **Provisional warning reuses the existing mechanism.** A carried row has a `provisional_note`, so
  `layer` already puts it in `provisional`, and the Tax estimate's `provisional` list (ADR-0019) already
  names each key with its note. No new code path. The carried note is the note, so the warning reads
  "<figure>: Same as 2025-26, not checked against the page". Confirming clears it (the digest and label
  change, as for any capture). Release interaction is unchanged: a carried figure is provisional, so a
  differing **firm** shipped figure replaces it (section 2 exception), and an equal one makes it
  redundant.
- **`FigureSpec.scheduled_change: str | None`**, plain text, guidance only (outside the content hash,
  like `where`): e.g. "Rate is legislated to fall from 1 July 2027". Shown on the candidate row, on the
  carried row until confirmed, and beside the Confirm control; it is a maintainer-written note that a
  release keeps current, not a rule evaluated by code. It is never used to alter a value.
- **Exact source pages.** A `FigureSpec.page_url` is the page that holds that figure, not a parent
  index. Several keys may share one page; that is what "per source page" groups on. The Low income tax
  offset keys point to
  `https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/tax-offsets/low-income-tax-offset`;
  the Developer checks every other spec against the same rule. URLs are guidance (outside the hash), so
  a correction needs no revision bump. Nothing fetches them.
- **Not in this amendment:** fetching pages (sites block it; the owner pastes); predicting or computing
  legislated changes; Age Pension keys (they join by spec with no change here).
- All value kinds are accepted (`money`, `rate`, `int`, `brackets`), because they share one text grammar
  and parser; the first screen leads with scalar keys, and brackets arrive through the same fields.

### 5. The AI step

_Amended by ADR-0023 section 6: with no model, or one that is off, capped or failing, a deterministic page reader proposes the candidates under the same checks, and the "Off or capped" fallback below becomes that reader rather than typed entry only._

- **Task `rule_capture`, a new data class `reference`** (extends ADR-0021 section 3, which fixes
  `guidance` to Guide, screen and Due items; that section is amended by a one-line pointer).
- **Inputs, one call per FY:** the **specs asked for** (key, label, kind, unit, where to look, plausible
  range; public product text) and the **pasted page text** (up to 40,000 characters) or **one image**
  (PNG or JPEG, up to 5 MB). Up to 12 keys per call; a longer list is several calls.
- **Redaction.** The pasted text is user-supplied and may include a signed-in banner, so vocabulary
  redaction (Household, Member, Entity, Account names; e-mail addresses) applies as for `guidance`.
  **Amount and digit-run stripping is switched off for this part only**, because the figures are what
  the call is for; the data class declares that, and the gateway refuses any other part with it. No
  Position, Valuation, Holding or Tax record is ever in this class. An **image cannot be redacted**:
  it is sent as is, so the screen shows the image preview and the words "Sent exactly as shown; crop
  to the page" before sending, and the log keeps its SHA-256, size and type, not its bytes. Image
  input is the second slice (section 9); the provider Protocol needs an image part first.
- **Model.** `FINANCE360_AI_MODEL_RULE_CAPTURE`, defaulting to the gateway default **Sonnet 5.5**, not
  Haiku: extraction from messy page text is where a wrong figure costs most, and volume is a handful of
  calls a year, so cost is immaterial against the existing caps.
- **Output contract** (forced structured reply, no tools):
  `{proposals: [{key, fy, value, quote, provisional, note, confidence}], not_found: [key]}`. `value` is
  **canonical figure text in the same grammar the typed field accepts**, so one parser serves both.
  `confidence` is `high|medium|low` and is **display only**: a model's self-assessment never gates
  anything. A key may appear once; a duplicate or an unasked key is dropped.
- **Deterministic checks on every proposal, before it is shown as a candidate:** it parses to the
  spec's kind and unit; it is within the spec's plausible range; the `quote` is non-empty and (for pasted
  text) **appears in the pasted text** after whitespace and case normalisation; and the parsed number
  **appears in the quote**. A proposal failing any check is shown as "not found; enter it" with the
  reason, never as a candidate. For an image the quote cannot be matched, so the row carries "Check
  this sentence against the page yourself".
- **Logging.** Through the gateway into `ai_log` exactly as ADR-0021 section 7 (`why` = "Rule figure
  capture"); `rule_capture.ai_log_id` links the row, and `ai_value_json` keeps the proposal beside the
  confirmed value.
- **Off or capped.** The screen is fully usable: the same checklist and typed fields without the "read
  the page for me" step, and a message why (off, or the cap and when it resets). A provider error
  falls back the same way and loses nothing typed.
- **The user confirms every figure.** Nothing is written until the person presses Confirm on a row
  (or Confirm all after reviewing every row). The write goes through the same deterministic validation
  as typed entry (AGENTS.md rule 4).

### 6. Validation and provisional figures

One validator (`core/figure_capture.py`), the same for typed and AI input:

- **Unit and kind:** a unit that contradicts the spec (a weekly figure for an hourly key) or an
  unparseable value is a hard error.
- **Plausible range:** from the spec, hard-blocked, as the script does today. A real figure outside the
  range means the spec is wrong: the message says so, and a maintainer corrects the range in a release.
  Rejected: an override box, which makes the check decorative.
- **Year-on-year jump:** the spec's `max_change_ppm` (default 20%) against the nearest earlier FY in the
  effective pack. A jump is a **soft flag**: the screen shows last year's figure beside the new one and
  requires an explicit "Yes, this is right" tick. A key with no earlier figure is not flagged.
- **Provisional:** a "this is announced, not yet law" checkbox with a required note, as the script does.
  A provisional capture is flagged in every estimate that uses it (ADR-0019) and is replaced, not
  edited, when confirmed: capturing the firm figure supersedes it.

### 7. What's due and the capture screen

- A Due rule owned by the capture Capability (ADR-0020), reading the effective pack and the records or
  estimates that need figures: **"FY 2026-27: car rate not captured"** when a record or estimate reads
  a key for an FY the effective pack lacks. Also the three conditions above (a release now differs from
  a capture; a capture no longer fits; a provisional capture is now firm). It links to the capture
  screen filtered to that key.
- **One screen, Rule figures** (Settings area, with a Guide link): per FY a checklist of declared keys
  with last year's figure, the page link and "where to look"; **Read the page for me** (paste text) and
  typed entry on each row; Confirm; each captured figure's history with Withdraw. **No new
  iconography or design-language additions**: existing Sheet, Table and form patterns. Plain words:
  "figure" and "captured", with CONTEXT.md's term below.

### 8. The script and releases

- **`scripts/rulepack_update.py` stays**, now the **maintainer's path to a release**, not the household's
  path to a figure. Its parse and validation come from `core/figure_capture.py`, so there is one
  validator. Its prompts are unchanged.
- **Release flow:** a maintainer updates the shipped pack (revision bump, pinned hash, as ADR-0018
  section 6) so a new install, or an instance that never captured, has the figure. An instance that did
  capture sees section 2. A captured figure is **never exported into a release automatically**; if the
  maintainer wants it shipped they run the script with the same page. This keeps the shipped pack
  verified by someone with the page open.
- ADR-0018 section 6 is amended: **"rule data ships as code" remains true for the baseline, and the
  database may now hold audited captures that layer on top**. The rejection of a reference-data
  *provider* that scrapes the official pages and the revision-and-hash discipline for shipped data stand.

### 9. Sequencing

The gateway (ADR-0021) comes first; the typed slice does not need it and can proceed in parallel.
Slices, each a thin vertical step:

1. **Store and typed screen.** `rule_capture` and migration; `layer`, `core/rulepack_store.py`,
   `core/figure_capture.py` (the move); services read `effective_pack`; version label with digest;
   typed entry, withdraw, supersede, audit; the trace card shows origin and quote; saved-run provenance
   `origin`; `figures_waiting` text; Guide page and sample-household captures. Invariant work: the merge
   and the label/digest get property and mutation-proof tests.
2. **What's due rules and release difference.** The four Due items and the Keep-mine acknowledgement.
3. **AI proposal, text.** Data class `reference`, task `rule_capture`, the proposal contract and its
   checks, "Read the page for me", off/capped fallback. Needs the gateway.
4. **Image input.** The provider image part, preview and warning, log by hash.

## Consequences

- A new official figure reaches the running app the day a person reads it, with its quote and who
  confirmed it, without a release. Traceability is stronger than today: the trace now names a sentence.
- Saved runs stay reproducible: values were already snapshotted, and the label now says exactly how
  many captures and which set.
- Cost: one table and migration, a `layer` step in every service that reads a pack, a spec carried on the
  pack, a data class with a deliberate exemption from amount stripping, and a validator moved into `core`.
- The household now owns a small piece of maintenance (reading pages each 1 July), made as light as it
  can be by the checklist and What's due.
- Forecloses: rule data edited in place; deleting a capture; capturing ages or keys with no spec;
  AI output written without confirmation; fetching the official pages; shipping a captured figure in
  a release without a person opening the page; a capability importing `rulepacks/`.

## Alternatives rejected

- **Keep ADR-0018 unchanged:** the maintainer-only path stays a release per figure; the problem the owner raised.
- **Overlay stored as a modified shipped pack file:** needs a write path into the image and breaks
  revision discipline.
- **Shipped always wins, or newest wins:** section 2.
- **Reuse Assumption overrides (`rule.`):** those are Scenario what-ifs resolved into a run, not
  statements of what the law says, and carry no quote or provenance.
- **Haiku for extraction:** a cheaper model for the one task where a wrong value is costliest.
- **An override of the plausible range:** section 6.

## Not decided here / routine follow-on

- **For the Start-from-a-prior-year slice (Developer):** the migration widening `method`; the "Start from <FY>" preview and accept
  and the page-level confirm endpoints and screen; per-spec `scheduled_change` text and the URL audit of
  every `FigureSpec`; Guide "Rule figures" page (Start from last year, what provisional means); a sample
  household year with some carried figures confirmed and some not; tests (accepting stores provisional
  `carried` rows with the note, the estimate's Provisional list names them, a page confirm clears them,
  a changed figure supersedes, no figure read without a capture row or shipped figure, bulk excludes
  scheduled-change keys, LITO URL).
- The slice issues, their sizes and order beyond the sketch (main session with the maintainer).
- Exact column types, the partial unique index form, API routes and field names, screen copy, the Guide
  page text ("Rule figures": how to read a page and what is sent), the `reference` prompt wording, the
  JSON schema, and the digest's exact canonical form.
- The `FINANCE360_AI_MODEL_RULE_CAPTURE` entry in `.env.example` and the pricing check (routine, with the gateway).
- Bulk import of several FYs at once, and an auto-suggested "page changed" nudge on 1 July: later, not needed.
- Age Pension specs (B11) and a pack for another jurisdiction: they add specs and a pack; the mechanism is unchanged.
- Out of scope: any fetching of the official pages; AI on figures other than Rule pack specs (manual
  imports of overseas pensions, payslips and scanned documents reuse the gateway, with their own data classes).

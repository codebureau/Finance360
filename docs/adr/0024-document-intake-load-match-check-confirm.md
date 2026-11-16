# 0024. Document intake: load, match, check, confirm

**Status:** Proposed
**Date:** 2026-11-07
**Issue:** — (part of the Tax planning epic; bears on the super-fund, overseas pension, payslip and image-import Importers)

## Context

The main evidence and exports for statements, income statements and payslips are PDFs. Today the household
types the figures in. the first consumer wants the ATO income statement PDF to fill the income statement form; the
super-fund statement Importer and the payslip Importer need the same kind of reading later; the
image-import path imagined AI extraction of every document.

The owner asked "AI vs baked-in rules". The answer already established by ADR-0022 and ADR-0023 is that the
no-AI path comes first and must be good: a deterministic reader proposes, deterministic checks police it,
a person confirms, and a model is an optional upgrade. A statement is read the same way every time, so
reading it with a model on each upload would be slow, costly, non-reproducible and a privacy cost for no
gain. A model earns its place only when there is no reader for a layout yet.

What exists and is reused: Evidence files, content-addressed under `documents/tax/` and covered by Backup
(ADR-0019 section 7, `capabilities/tax/evidence.py`, which already checks a PDF's first bytes and caps a
file at 10 MB); the Importer registry and the pure bytes-to-records Importer protocol (ADR-0013); the
check-then-confirm pattern and the validator in ADR-0022; the gateway, redaction and `ai_log` (ADR-0021);
the no-model fallback principle and the deterministic word matcher (ADR-0023); `jsonschema`, already a
dependency. The ATO PDF is a browser "Print to PDF" of the ATO page, so it has a text layer. The real
sample is in the gitignored `private/samples/ato-income-statement/` and was not opened for this ADR.

## Decision

### 1. The pipeline

**Load, match, check, confirm, store.** Nothing is stored until the owner confirms.

| Step | Does | Where |
|---|---|---|
| Load | bytes (PDF) or pasted text into one `Document` | `capabilities/documents/loader.py` |
| Match | a Document profile picks the figures out of the Document | `capabilities/documents/matcher.py` |
| Check | parse, range, sums that must agree, period | `capabilities/documents/checks.py` |
| Confirm | the owner reviews the **Reading** and saves | the target's own screen (section 3) |
| Store | the figures through the target's normal write path; the file as Evidence | existing services |

Load, match and check are **pure**: bytes in, a Reading out. No database, no filesystem, no network, no
audit, no AI. They are a function of the document and the profile, so a golden test pins them.

### 2. The Document shape and the PDF library

- **`Document`** (frozen dataclasses, `capabilities/documents/model.py`): `pages: tuple[Page, ...]`; a
  `Page` has `number`, `width`, `height` and `words: tuple[Word, ...]`; a `Word` has `text`, `x0`, `x1`,
  `top`, `bottom` (origin top-left, reading order by `top` then `x0`). **Lines** are derived, not stored:
  words whose `top` is within a tolerance share a Line. The Document keeps no file name and no bytes.
- **Geometry is floating point, and that is allowed.** AGENTS.md rule 3 forbids floats for money, quantities
  and prices. A coordinate on a page is neither, and never becomes a figure. Say so in the module docstring
  so a reader does not "fix" it. **Every value read is text until `Money`/`Decimal` parses it.**
- **Pasted text** loads into the same shape: one page, one Line per text line, `x0` the character column.
  Position-based pairing is unavailable on it, so a profile that needs it reports "not found". This is the
  fallback for a PDF with no text layer. A PDF with no text at all fails with "this PDF has no text; scans
  and images are the image-import path".
- **Library: `pdfminer.six`, pinned exactly** (the Developer pins the then-current release and confirms
  the licence in `uv.lock`). It is pure Python, MIT, and layout analysis yields every character with its
  bounding box, which is what positional pairing needs. It runs no JavaScript and no network. Its
  dependencies are `charset-normalizer` and `cryptography`; the latter is a compiled wheel from PyPI used
  for encrypted PDFs, the same kind of dependency as `argon2-cffi`. It is not a bundled rendering engine.
  Both go through the existing `pip-audit` job. The Developer confirms it installs on the image's
  Python and states the added image size.
- **Rejected:** `pdfplumber` (a thin layer over pdfminer.six that adds Pillow and `pypdfium2`, a native
  PDFium engine); `PyMuPDF` (native, AGPL); `pypdf` (pure and dependency-free, but exposes positions only
  per text-show operation through a visitor callback, with no line or word grouping, so positional
  pairing would be hand-built. It is the fallback if pdfminer.six fails to install on the image).
- **Limits**, so a hostile or huge file cannot hold the server: the existing 10 MB cap, **at most 20
  pages**, a time-boxed parse, and an encrypted PDF is refused with a plain message (no password prompt).
- **Loaders are a plug-in point, and OCR is kept open.** Slice 1 builds the PDF text-layer loader and the
  pasted-text loader only. An OCR loader (the image-import path: a scan, a photo, a PDF with no text layer) is a later
  plug-in that produces the **same `Document`**: OCR engines already emit words with boxes, which is
  this shape. Everything after Load (match, check, confirm, store) is unchanged for it. When it comes,
  an OCR `Word` gains an optional per-word confidence so a check can flag a figure read from a
  low-confidence word. Nothing in slice 1 may assume a Document came from a text layer.
- **Show the text, as well as reading it.** Because every loader produces a Document, the screen can
  later show a document's text (in reading order, selectable) for the owner to copy into a field when
  no profile fits, or for a one-off document. This saves reading a statement and retyping it by eye.
  It needs no profile and stores nothing. It is not in slice 1, and nothing in the design may prevent it.

### 3. A Document profile: data, with the matcher in code

A Document profile is **data**: a JSON document validated against a JSON Schema (`jsonschema`) on load.
Data, not code, so a shipped profile, a saved profile and an AI proposal are one format, and a proposal
cannot contain behaviour. The **matcher and its vocabulary are code and closed.**

```
{ "key": "ato-income-statement", "version": 1, "title": "...", "target": "tax.income_statement",
  "claim": ["phrase that must appear", "another"],
  "sections": [{"key": "income", "start": "label", "end": "label"}],
  "fields": [{"key": "gross_amount", "type": "money", "labels": ["variant", "variant"],
              "section": "income", "pair": "same_row", "required": true,
              "absent_means_zero": false}],
  "sums": [{"total": "gross_amount", "plus": ["...", "..."]}] }
```

- **`claim`**: phrases (all must appear on the document, compared after case, whitespace, punctuation and
  non-breaking-space normalisation) that say "this profile is for this layout". It is the profile's
  fingerprint and what separates "wrong profile" from "profile broke".
- **`fields`**: `type` is `money`, `fy` (a financial year) or `text`; `labels` lists the variants a label
  has been seen as; `section` optionally confines the search to the Lines between a section's `start` and
  `end` labels; `required` marks a figure the Reading cannot do without; `absent_means_zero` marks a line
  a statement omits when it is nil.
- **`pair`** is one of a **closed set**, each a few lines of matcher code: `same_row` (the value is on the
  label's Line, to its right) or `next_line` (the first value on the Line below). A positional rule
  (`below`: the nearest value under the label in its column) is added **only if the real sample needs it**,
  as code plus a schema value, never as profile freedom.
- **Matching is exact on the normalised label** (a label variant is the whole Line or the start of it),
  not fuzzy. **A label that matches more than once in its scope is ambiguous**: the figure is not filled
  and the Reading says why. (If the ATO statement lists several payers, the Developer stops and reports
  before building anything for it; see Not decided.)
- **`sums`**: `total` equals the exact sum of `plus` in minor units, tolerance zero. If a part is not found
  and is not `absent_means_zero`, the check is skipped and the Reading says "could not check: <field>
  missing", never treated as zero.
- **A profile holds no values and no personal text.** A label, a phrase or a section marker that contains a
  digit run of six or more characters is invalid on load. That keeps a name, an ABN or an account number
  from ever being saved inside a profile, which matters once AI proposes them (section 6).

**Where profiles live.** Shipped profiles are JSON files in a plug-in package, registered at start-up
from `main.py` into `capabilities/documents/registry.py` (the Importer registry's shape). **Saved
profiles** (confirmed AI proposals) live in a new append-only table, built with the AI slice (section 9):
`document_profile` (`key`, `version`, `profile_json`, `created_at/by`, `method`, `ai_log_id`, `ended_*`),
with the ADR-0022 discipline (never edited, only ended; audited). **Precedence follows ADR-0022
section 2:** a saved profile with the same `key` as a shipped one wins, and a release that ships a
different version of that key is flagged to the owner (a Due item) rather than silently replacing or being
ignored. A different `key` simply adds. Slice 1 has shipped profiles only, no table and no migration.

### 4. The checks, and the Reading

The checks are the ADR-0022 set, applied to what the matcher found:

- **Parse:** a `money` value parses to exact `Money` (accepts `$`, thousands separators, a leading minus
  and brackets); an unparseable value is "not found", never rounded or guessed.
- **Range:** the profile or the target bounds a figure (a money figure is not negative unless the field
  says it may be; an `fy` is a plausible year).
- **Sums** (section 3) and, for a target with a period, **the document's period equals the form's**
  (a statement for another year is a warning with both shown).

The **Reading** (`capabilities/documents/reading.py`) carries `outcome`, the profile `key` and `version`,
each figure found (value, page, matched label), each figure not found (with a reason and whether it was
required) and each check's result. Outcomes:

- **`unrecognised`**: no profile's `claim` matches. The form stays as it is; the screen says so. At a
  level where AI is on (section 6) it offers to propose a profile.
- **`needs_repair`**: a profile claimed the document but a **required** label is missing or ambiguous.
  **Nothing is filled**, and the message says this profile needs repair (and for whom: a release, or
  the owner's saved profile). This is the loud failure when a layout changes. Never a guess.
  _Amended later: the figures that **were** found are filled and the missing required ones are
  named; the document stays beside the form (as for every outcome, including `unrecognised`) so the
  owner copies the rest. It is still never a guess: nothing unread is filled._
- **`warnings`**: required figures found; a **sum disagrees**, a period differs, or an optional figure is
  not found. The found figures **are** filled, each warning is shown beside the figures involved, and the
  owner must tick that they have checked before saving a sum disagreement (ADR-0022 section 6's soft flag).
  An optional figure not found stays blank for typing.
- **`ok`**: everything found and every check passed. Still not stored.

Sum disagreement is a warning with the figures filled rather than a failure, which refines "fail
loudly": the values are exactly what the page states, so nothing is guessed, and refusing to show them
would leave the owner no way to see why they disagree. the first consumer's outcome ("if the parts don't add up, the user
is warned") is this.

### 5. Is filling from a document an Import? Per target

**A Reading is a front end. What it feeds decides.**

- **A Tax record (slice 1): a pre-fill plus Evidence. Not an Import.** A Tax record is a single dated
  register entry the owner already creates and edits on a form (ADR-0019), with its own validation,
  supersession and audit. An Import is bytes to canonical records, with an Import source, an Account,
  Coverage and a staged diff; none of that applies to a form with one record and no Account. The form is
  already the confirm step, so a staging table and a diff would be a second copy of it. **Flow:** the
  screen posts the file to `POST /api/documents/read` (read-only, stores nothing, no audit), fills the form
  from the Reading and holds the file in the browser; on **Save** the client creates the record, then
  attaches the same file through the existing Evidence endpoint (which re-hashes and stores it). If the
  attach fails the record exists without Evidence, the screen says so and offers a retry, and the Evidence
  health check (ADR-0019) already covers the rest. A discarded form stores nothing.
- **Canonical records (Valuations, Holdings, Transactions, a super-fund statement and any later statement): an Import.** A
  document Importer implements the **existing, unchanged Importer protocol**: `claims(content, name)` is
  "a profile claims this document" and `parse` is load, match and check, mapped to a `ParsedFile`.
  Staging, diff, Account resolution, Coverage and supersession are ADR-0013's, untouched. A statement's
  "balances don't add up to the total" (a super-fund statement) is a profile `sums` entry becoming an Importer warning. How
  the source file is retained for an Import is ADR-0013's existing handling and not changed here.
- **CONTEXT.md.** Import stays "one ingestion of a source document through an Importer". Reading a document
  to pre-fill a Tax record is not an Import and not called one (the _Avoid_ on Import already bars
  "Upload" and "Load"). Two terms are added: **Document profile** and **Reading**. **Import profile** stays
  the saved column mapping for CSV-style Import sources and is not stretched: CSV is already served by
  Importers and Import profiles and does **not** go through Document profiles.

### 6. The AI role (slice 2, not built in slice 1)

AI is optional and sits at ADR-0023's Ask level, never in the reading path.

- **What a model does:** given **one** document the owner chooses, it **proposes a Document profile** for an
  unrecognised layout (`unrecognised`) or a **repair** of one that `needs_repair` (the profile and the
  new document are both sent). It does not read the figures. After the owner confirms the profile once and
  it is saved, every later document is read deterministically. With no model, a maintainer writes the
  profile as JSON in a release (a short how-to lives beside the plug-in). The screen then says "no profile
  for this layout" and the owner types the figures; nothing is greyed out or broken.
- **A proposal is checked against the same document before it is shown.** The gateway returns JSON in the
  profile schema (untrusted, as ADR-0022 section 5). Deterministic code then: validates it against the
  schema and the no-long-digit-runs rule; checks every proposed phrase and label **appears in the
  document**; **runs it** over the document and requires the outcome to be `ok` or `warnings` with every
  required field found; and shows the owner the resulting Reading beside the profile ("this profile reads
  these figures from this document"). A proposal failing any check is not offered for saving. The owner
  confirms the profile by confirming this Reading, and it is saved append-only with its `ai_log_id`.
- **Data class: new, `document`**, not `reference`. ADR-0022's `reference` exemption is for public page
  text; a statement carries names, an employee number, an ABN or TFN, account references and the owner's
  own figures. The class: vocabulary redaction (Household, Member, Entity, Account names), pattern
  redaction of ABN, TFN, e-mail and any digit run of eight or more that is not a money amount, **amounts
  kept** because the model needs them to propose a sum, and a cap on text length. Free-text names not in
  the vocabulary (an employer) cannot all be caught, so the screen shows the **exact redacted text to be
  sent**, and nothing goes until the owner presses Send. The log keeps what was sent (ADR-0021 section 7),
  so redacted document text is in `ai_log`; the Guide says so. The class is extended by an ADR amendment
  note in ADR-0021, one line, as ADR-0022 did for `reference`. **No value from a model is ever a figure**:
  AI output is a profile, and profiles hold no values.
- Through the gateway always (AGENTS.md rule 4), task `document_profile`, the caps and the daily limit as
  for Ask answering.

### 7. Where the code lives

Plug-ins depend on capabilities, capabilities on core, never the reverse (ADR-0004, enforced).

| What | Where |
|---|---|
| `Document`, loader (pdfminer.six), text loader, matcher, checks, `Reading`, profile schema and validation, profile registry, the `read(...)` service function | **new capability `capabilities/documents/`**, pure apart from the loader's library |
| Tax evidence storage, attach, hash check | `capabilities/tax/evidence.py`, unchanged |
| `ato_income_statement` profile (JSON) and its registration | **`plugins/documents/ato_income_statement/`**, registered from `main.py` |
| `POST /api/documents/read` | `api/documents.py`, calling only `documents.service` |
| The upload control and the fill on the income statement form | `frontend/` |
| `document_profile` table, saved-profile precedence (slice 2) | `core/models` and `capabilities/documents/store.py` |

`capabilities/documents` is a sibling of `tax` and imports neither, nor the reverse; the screen composes
the two through the API, so Tax stays unaware of documents. The profile's `target` names the form it feeds
and its field `key`s are that form's field names (the income statement breakdown form); the API checks
the profile's target matches the endpoint's request. `capabilities/documents` joins the pure-module
forbidden-imports contract in `pyproject.toml` for `matcher`, `checks`, `model` and `reading` (no database,
audit or service), so purity is enforced, not hoped for. The Developer adds the import-linter entries.

### 8. Testing and privacy

**Testing.**
- A **synthetic fixture PDF in the ATO layout with invented values**, generated by Chromium from an
  invented HTML page (Playwright, already a dev tool) by a script in `scripts/`, and **committed**. The
  tests never regenerate it, so CI does not need a browser. It also goes in `fixtures/sample_household/`
  so a reviewer can upload it (the standing synthetic-household rule). A browser-printed PDF is the same
  kind of file as the real one.
- **Golden tests:** fixture PDF to an exact Reading (every figure as `Money`); the same through the text
  loader. Matcher tests run on in-memory Documents so each outcome (`unrecognised`, `needs_repair`,
  `warnings`, `ok`; an ambiguous label; a missing optional figure; a sum that disagrees; an absent part)
  needs no PDF.
- **Invariant work (money maths):** the sum check and the money parser get Hypothesis tests (every string
  `format` produces parses back to the same minor units, for brackets, minus, `$` and separators; a sum
  of parts always equals its total), and the sum check is broken once to prove the test fails.
- **Real-sample check:** `backend/tests/test_document_real_samples.py`, the pattern of
  `test_importer_real_samples.py`: it reads each file in `private/samples/ato-income-statement/` and
  asserts the PDF loads with a text layer, the profile claims it, the outcome is not `needs_repair` and
  required figures are found and sums hold. **It skips where the folder is absent and asserts only
  structure, never a value, and prints no document text, even on failure.** The slice is not done until it
  passes locally. The Developer, not this ADR, checks text-layer extraction against the real sample, and
  only locally.

**Privacy.**
- **Stored:** the uploaded PDF as Evidence (`documents/tax/<sha256[:2]>/<sha256>`, referenced by hash, in
  Backup), and the figures the owner saves on the form. Nothing else: no Reading, no extracted text, no
  profile match trace.
- **Never logged:** document text, labels' values, figures read or the uploaded file name. Application logs
  and error messages name a profile key, a field key, a page number and a reason, never a value (as an
  Importer's errors never echo an account reference). The Developer tests that an error path does not
  contain a planted value.
- **At the no-AI level nothing leaves the host**: no gateway call, no `ai_log` row, no network. The read
  holds the bytes in memory for the request only.
- Test fixtures and the committed PDF carry invented values only (AGENTS.md rule 1).

### 9. Sequencing

1. **Slice 1: the pipeline for one profile.** `capabilities/documents` (model, loader for PDF and
   text, matcher with `same_row` and `next_line`, checks, Reading, registry, `read` service); the ATO
   income statement profile in its plug-in; `POST /api/documents/read` and the regenerated API client;
   upload and fill on the income statement form, with the per-outcome messages and the sum-warning tick;
   Evidence attached on save; the pinned dependency; import-linter entries; the fixture PDF and generator,
   golden, property and real-sample tests; the Guide page for tax records (what to upload, what is read,
   that nothing is stored until saving) and the Developer's short "adding a profile" note beside the
   plug-in. No new iconography or design-language additions: the existing file-input and form patterns.
   Depends on the income statement form's field names.
2. **Slice 2: AI profile proposal and repair.** The `document` data class and redaction, the gateway task,
   the send preview, the proposal checks, `document_profile` and its migration, precedence and its Due item.
   Needs ADR-0023 slice 2 (a model to configure) to be useful, but not to build.
3. **Later, each its own issue:** the super-fund statement as a document Importer (it needs a **table construct** in the profile
   schema, header labels, column ranges and repeating rows, which is a closed addition to the schema and
   matcher, decided when it is designed); payslips as another Tax-style pre-fill profile, one per
   payroll layout, where slice 2 repairs earn their keep.

**Do NOT build** (slice 1 or 2): OCR or image reading (the image-import path); a profile editor in the app; a visual
profile designer; fuzzy label matching; regular expressions in profiles; profile "scripts" or any
executable content; a CSV path through Document profiles; the table construct or row-repeating
sections; payer blocks beyond what the income statement form takes; a second PDF library; stored Readings, staged
documents or an upload queue; a server-side temporary store of uploaded files; AI reading figures
directly; AI on every upload; auto-saving a profile; matching more than the ATO income statement profile;
payslip or super-fund profiles; a framework for plug-in document types (a profile is data and the matcher is
one module).

## Consequences

- The ATO income statement fills the form with no AI, no cost and nothing sent anywhere, and the same
  document reads the same way every time, so a golden test and a real-sample structure test are meaningful.
- A layout change fails loudly (`needs_repair`) with a fix path (a release now, an AI-assisted saved
  profile later), instead of drifting into wrong figures.
- AI has a narrow, checkable job whose output holds no values and is verified against the document it was
  proposed from, so a wrong proposal is rejected by code, not by trust.
- The super-fund statement and payslips are further profiles and, for the statement, one table construct, not new pipelines. The Importer protocol,
  staging and Coverage are untouched.
- Cost: one capability, one pinned dependency (with `cryptography` as a transitive compiled wheel), a
  profile schema and matcher to maintain, a binary fixture in the repo, and a JSON profile a maintainer
  edits by hand until slice 2.
- A browser "Print to PDF" layout can change when the ATO redesigns its page; that is the case
  `needs_repair` exists for, and the profile's `claim` keeps a different document from being read by it.
- Forecloses: reading a document with a model on every upload; a hand-written parser per document;
  profiles with behaviour; unconfirmed storage of a Reading; sending a statement to a model without the
  owner seeing the redacted text; a capability importing a plug-in; routing CSV through document profiles.
- Keeps open: an OCR loader for scans and images (the image-import path) producing the same Document, with the rest of
  the pipeline unchanged; showing a document's text for the owner to copy when no profile fits.

## Alternatives rejected

- **AI reading every document.** Slow, paid, not reproducible between runs, and sends the owner's most
  sensitive text to a model every upload for a result a profile gives for free. Checks would still be
  needed, so the checks would exist anyway.
- **A hand-written parser per document.** Code for every layout and every layout change, tested ad hoc,
  with the label-and-sum logic duplicated. A profile is the same knowledge as data that a model can
  propose and a person can read.
- **OCR now.** A native engine (or a service) for documents that are not needed yet. It is deferred, not
  ruled out: it arrives as another loader (section 2), and scans stay on the image-import path.
- **A native PDF engine** (PyMuPDF, PDFium via pdfplumber): a bundled binary on the image, a licence
  question for PyMuPDF, and more than a text-layer reader needs.
- **A generic framework for every format at once.** AGENTS.md rule 5: one profile in one plug-in; the
  table construct and further layouts arrive with the issue that needs them.
- **Staging every Tax-record fill as an Import.** A second diff screen over a form that is already the
  confirmation, with no Account or Coverage to give it meaning.
- **Profiles as code only.** Cannot be saved by an instance, cannot be proposed by a model without
  generating executable code, and cannot be validated against a schema.
- **Failing the whole Reading on a sum disagreement.** Hides the values the owner needs to see why.
- **Reusing the `reference` data class.** Its amount exemption was granted for public page text.

## Not decided here / routine follow-on

- Exact JSON field names, the schema file, the line-grouping tolerance, the label normalisation details,
  money-string grammar details, error and warning wording, the API request and response field names, and the
  upload control's copy.
- **Payer blocks.** If the real ATO sample or the income statement form shows several payers in one statement, the
  Developer stops and reports; whether a Reading becomes a list of blocks, and how the form picks one, is
  then a short design call and not guessed. This ADR assumes one payer per document.
- Whether the positional `below` pairing is needed (the real sample decides; section 3).
- Whether the saved record notes that it was read from a document (profile key and version). The
  attached Evidence is the link for now.
- The `document` data class's exact redaction patterns, the length cap, the prompt and the proposal JSON
  schema (slice 2).
- The Due item wording for a release version that differs from a saved profile (slice 2).
- Whether the Evidence directory name `documents/tax/` generalises when a statement Importer retains statements.
- Retention of a source PDF for an Import (ADR-0013's handling stands).
- The slice issues' sizes and order beyond the sketch (main session with the maintainer); the Guide text.
- Out of scope: the super-fund statement and payslip Importers themselves; scans and images (the image-import path); CSV (Importers).

# 0026. Library: one document store, generic links, and pre-fill beside the source

**Status:** Proposed
**Date:** 2026-11-08
**Issue:** — (amends ADR-0019 section 7 and ADR-0024 sections 5, 7 and 8)

## Context

Documents are the household's raw material, and three things are missing:

1. **Evidence is tax-only.** `tax_evidence` and `tax_record_evidence` (ADR-0019 section 7) store files under
   `documents/tax/`. Statements need keeping with an Account and a Super statement; any later record kind
   would want the same.
2. **No place to start from a document.** The owner wants to gather documents first, then fill information in
   from them, and to start from either end: a Library document, or a record's own form.
3. **Pre-fill asks for trust.** ADR-0024 fills a form from a Reading; the owner cannot tick the figures off
   against the page without leaving the screen.

Reused: content-addressed files, the type allowlist and 10 MB cap (`capabilities/tax/evidence.py`);
Document profiles and Readings (ADR-0024); `documents/` already backed up whole (ADR-0011); the plug-in
registry shape (ADR-0004); `SupersessionMixin` (ADR-0009). Requirement: a new document type or variant is
never a code change; only a new record kind or fill target is.

## Decision

### 1. Vocabulary

- **Library**: the area, and the household's set of stored documents. **Library document**: one stored
  file. Code: `LibraryDocument`, `library_document`, `capabilities/library/`.
- ADR-0024's in-memory `Document` (pages of words) is a **code-internal** type; it is never user-facing and
  not in the glossary. Prose says "loaded text" for it. No rename: the clash is avoided by the glossary
  term being the longer, qualified one. (If it confuses in code, rename to `PageText` as a routine change.)
- **Evidence** keeps its meaning but becomes a **role, not a store**: a Library document linked to a record
  as support. The screen says "Documents". **Document link**: one document-to-record association.
- **Fill target**: a form a Reading can fill (section 4). CONTEXT.md is updated in this branch.

### 2. Storage: one content-addressed store

- Files: `documents/library/<sha256[:2]>/<sha256>`, never overwritten, path from the hash alone (as today).
  Table `library_document`: `id`, `sha256` (unique), `file_name`, `media_type`, `size_bytes`, `added_at`,
  `text` (nullable), `text_status` (`extracted` | `none` | `too_large`). A duplicate upload is the same
  row; the screen says "already in the Library" and shows its links.
- Limits unchanged: 10 MB; PDF, PNG, JPEG, text, CSV, checked against the first bytes. Text is extracted
  at add time by the ADR-0024 loaders (PDF text layer, plain text) within ADR-0024's 20-page and time
  limits; images and over-limit files get no text. **OCR (the image-import path) fills the same `text` column later.**
- **Extracted text is stored** (for search). This **amends ADR-0024 section 8** ("no extracted text
  stored"): the Reading is still never stored; the document's plain text is, in the database, so it is in
  Backup. It is never logged and never sent to an AI except through the gateway's `document` class
  (ADR-0024 section 6).
- **Migration.** Alembic renames `tax_evidence` to `library_document` (adding the new columns, `text`
  backfilled as `none`) and converts `tax_record_evidence` rows to `document_link` rows of kind
  `tax_record`; downgrade rebuilds the tax tables from them. Rows only; Alembic does no file I/O. An
  idempotent start-up step copies each `documents/tax/` file to `documents/library/`, verifies the hash,
  then removes the old one. Until moved, reads fall back to the old path, so a half-finished or
  restored-from-old-backup state works. Invariant work: a populated-database upgrade test (slow tier) and a
  test that a failed copy loses nothing.
- **Removing a record never deletes its document.** The link goes; the document stays in the Library as
  unlinked (it may be the only copy of a statement). A document is deleted only by an explicit action,
  only when it has no links, with its file removed and the action audited. The existing Evidence health
  check (missing or changed file) becomes the Library's.
- **Backup:** `documents/` is backed up and restored whole (ADR-0011), so `documents/library/` is covered
  with no change. Slice 1 adds a test that a restored instance finds a Library file.

### 3. Generic links and the kind registry

One table, no table per kind: `document_link(id, document_id FK, target_kind, target_id, added_at)`, unique
on all three. Writes are audited. A document links to many records; a record has many documents.

**Referential integrity without per-kind foreign keys.** `target_kind` is a registered key and
`target_id` is validated at write by that kind's service. Records are superseded, not deleted (ADR-0009),
so a link is never orphaned by the usual path. Each kind supplies, in the Library's registry:

```
LinkKind(key, title, exists(session, id), lineage(session, id) -> ids, describe(session, ids) -> {id: (label, route, removed)},
         candidates(session, text) -> [(id, label)])
```

- A record's documents are the links to **any id in its lineage** (so a corrected Super statement keeps its
  documents); a document's links are **described by the current record in the lineage**, with the route to
  it. A record whose lineage ends in removal shows as "removed" with its link kept (traceability), and can
  be unlinked.
- `candidates` backs "link to an existing record".
- Registered kinds in the first slices: `tax_record`, then `account` and `super_statement`.

**Where it lives.** `capabilities/library/` sits **below position and above documents**, so the layers
contract becomes `projection > tax > position > library > documents > household` (ADR-0004; the Developer
edits the contract, strengthening it). The Library imports `core` and `documents` (the pure loaders) and
**no record-owning capability**. Tax and position import `library.registry` to build their own `LinkKind`
and the `main.py` entry point registers it, as Importers are registered. The API calls only
`library.service`; validation of a link goes through the registered kind, never the Library reaching into
tax. The tax capability's own Evidence functions are removed (not shimmed); its API routes move to
`/api/library/...` and the frontend and generated client change in the same slice.

### 4. Fill targets and the handoff

A **Fill target** is registered once by the capability that owns the form, in `documents.registry`:
`key` (e.g. `tax.income_statement`, `position.super_statement`), `title`, the fillable `fields`
(key, type `money` | `fy` | `text`), the `LinkKind` the saved record gets, and whether it has a period.
Profiles stay data (ADR-0024): a profile's `target` must name a registered target and each profile field
`key` must be one of its fields, **validated on load**. A backend test pins each target's field keys to
its form's. (As built: the owning capability declares its target as plain data, since it may not import
Document intake, and `main.py` registers it before the profiles; a profile is validated when it registers.) A **new document variant is a new JSON profile for an existing target: no code**. A new
target (a form) is code, and these will plateau.

**One flow from either end.** Each target form accepts `?fromDocument=<id>`:

- *From the Library:* the document view offers "Start a record from this document", listing targets (the
  recognised profile's target first). It opens the target's new-record form with `fromDocument`.
- *From a form:* the form's "Read from a document" lets the owner pick a Library document or upload a
  file; both end in the same state as the first path.
- The form calls `POST /api/documents/read` with either a file (as ADR-0024) or a `document_id`; it
  stores nothing and is audited nowhere. **Save** creates the record, then links the document
  (`POST /api/library/links`). An uploaded file that is not yet in the Library is added at that Save, so a
  discarded form still stores nothing (ADR-0024 unchanged). A failed link shows a retry.

### 5. Side-by-side checking

On any pre-fill, the page is a **form pane and a document pane**:

- Desktop: the form beside the document, both scrolling independently. At 390px: two tabs, **Form** and
  **Document**, the form first, switching without losing form state. The document is also one click from
  the form without a pre-fill (the record's Documents list).
- The pane embeds the browser's own viewer (`<iframe>`) on the file route. **No pdf.js** or new frontend
  dependency in the first slice. Text and images display natively.
- **Serving, safely:** `GET /api/library/documents/{id}/file`, behind the Session like all of `/api`.
  `Content-Type` is the stored allowlisted type, never derived from the name; `X-Content-Type-Options:
  nosniff`; `Content-Disposition: inline` with a sanitised file name (download is a separate link);
  text and CSV served as `text/plain`; a restrictive `Content-Security-Policy` (`default-src 'none'`, plus
  only what the built-in PDF viewer needs, proved in a browser check) and `Cross-Origin-Resource-Policy:
  same-origin`; framing allowed only for the app's own origin on this route. The Developer proves the
  headers in a test and the viewer in Chromium. **Proved in the first slice** (Chromium 156 with its PDF viewer): the CSP as
  built, `default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'`, renders a
  stored PDF in the `<iframe>` with no change; `sandbox` stays out.
- **Deferred:** highlighting where a figure was found. A Reading already carries page and word boxes, so
  it needs a rendered page (pdf.js) and an overlay; that is a later issue once the viewer is in use.
  The Reading's per-figure page number is shown beside each figure now ("page 2"), at no extra cost.

### 6. Where a document is shown: the Library screen and the reusable list

**Library screen** (`/library`, a new sidebar entry; one nav icon from the existing set, no
design-language addition): a list with file name, type, added date and link count; search on name and
extracted text (SQL `LIKE`; SQLite FTS5 only if that proves slow); filters for type, linked-to kind and
**unlinked**; "Add documents" (one or several files). The **document view** shows metadata, the embedded
document (section 5) and its **links** (kind, the record's label, a link through to the record), with
actions: link to an existing record, start a record (section 4), unlink, delete (only if unlinked).

**Records show their documents with one component.** A `LinkedDocuments` component, given a kind and id,
lists that record's documents and offers **Add** (upload, which files it in the Library and links it
there) and **Link existing** (search the Library), per-row open (viewer) and a link through to the
document's Library view, and unlink. It reads `GET /api/library/links?kind=&id=` and is driven by the kind
registry's titles and routes: **no per-screen code**. It replaces the tax record's Evidence list, and the
Account page, Tax record and Super statement views each mount it with one line.

### 7. Profiles as data, shown in Settings

Profiles stay as ADR-0024 set them: shipped JSON in `plugins/documents/` and, from the `document_profile`
table, saved ones. Settings gets a **Document profiles** page, read-only first: key, title, version,
target, shipped or saved, claim phrases and field keys, and (from slice 7's pairing with the Library) the
Library documents each recognises. Editing is later; saving a profile the owner pastes is the first edit
(validated against the schema and test-run over a chosen Library document, append-only as ADR-0024 section
3), and AI proposals remain ADR-0024 slice 2.

### 8. Slices (each one or two Developer sub-issues)

1. **Store, links, migration, `tax_record` kind, `LinkedDocuments` on the Tax record.** Tables, migration,
   file adoption, kind registry, file route, API, the component replacing the Evidence list, the fixture
   and Guide updates. No Library screen yet.
2. **Library screen:** list, search, filters, add, document view with links, unlink, delete.
3. **Side-by-side on the existing income statement pre-fill** (the pane, tabs at 390px) and the Fill
   target registry, with `tax.income_statement` as its first entry.
4. **Super statement and Account:** the read-only Super statement view and `LinkedDocuments`
   on the Super statement and on the Account page, `account` and `super_statement`
   kinds. **Absorbs the earlier issue on documents for statements** (its design-first section is this ADR).
5. **Library-initiated pre-fill:** `fromDocument`, "Start a record from this document", pick-from-Library on
   forms, `document_id` read.
6. **A super-fund statement profile** on the `position.super_statement` target. **Changed:** it
   depends on 4 (not only the statement form), keeps the PDF through the Library link, and gets the side-by-side pane
   with no work of its own.
7. **Settings: Document profiles** (read-only), then pasted-profile saving with the `document_profile`
   table.

**Non-goals:** OCR (the image-import path); AI profile proposals (ADR-0024 slice 2); a profile editor or designer beyond
paste-and-test; figure highlighting and pdf.js; folders, tags or collections; versions of a document; a
document being edited in the app; sharing or e-mailing; auto-linking a document to a record because it was
recognised; full-text search beyond `LIKE`; bulk "gather then fill" queues (the Library list with
filter "unlinked" is the queue).

## Consequences

- Any record kind gains documents by registering a `LinkKind`; any document layout is a JSON profile.
  Traceability both ways is one table.
- One more migration on live data (rows renamed, files adopted), made safe by row-only Alembic, hash-verified
  idempotent file moves and a read fallback.
- Extracted text now sits in the database and Backup, which is a privacy cost for search; it is local only.
- Tax loses its own Evidence code; the frontend and generated client change in slice 1.
- **ADR-0019 section 7** ("Evidence" files in `documents/tax/`, tax-only tables) and **ADR-0024 sections
  5, 7 and 8** (file as tax Evidence; no extracted text stored; tax evidence storage) are amended as above.
  Neither decision's intent is reversed: files stay content-addressed and in Backup, a Reading is still
  never stored, and nothing is stored from a discarded form.
- Forecloses: a document store per record kind; a table per link kind; deleting a document when its record
  goes; a profile change needing a code change.

## Not decided here / routine follow-on

- Table and column details, constraint names, file-route and API shapes, the exact CSP string, error
  wording, search ranking, and how many files an "Add documents" accepts at once.
- Whether the `profile_key` a document claims is stored or recomputed (needed by a "recognised profile"
  filter; deferred to slice 5 or 7, with the list filter).
- The Evidence health check's screen, the Guide pages (Library, Documents on each area, "What to provide,
  when"), and the fixture's invented synthetic documents (standing rules).
- Orphan-file clean-up beyond the explicit delete.
- **Related, not designed here: Tags.** The Library filter can gain a Tag facet later (including
  showing a document's linked Accounts' Tags). If Tags become general, the kind registry can carry a
  tag link rather than a per-kind tag table.

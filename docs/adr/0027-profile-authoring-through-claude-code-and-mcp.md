# 0027. Profile authoring through Claude Code and MCP: access tokens, a thin MCP server, proposed profiles

**Status:** Proposed
**Date:** 2026-11-08
**Issue:** — (amends ADR-0005, ADR-0010, ADR-0024 sections 3 and 6, ADR-0026 section 7)

## Context

A new document provider (a second super fund with a different statement layout) must be readable **without a
code change or a release**. Profiles are already JSON data, validated against a Fill target (ADR-0024,
ADR-0026 section 4), but today a profile can only ship in the app. ADR-0024 section 6 planned an in-app
model to propose one; that needs a configured model and costs API money (ADR-0023: no cost by default).

The owner's first AI route is different: **the owner talking to Claude Code**, on their own machine and subscription, in
the context of their own installation, guided by structure. Nothing in the app calls a model. What is missing
is a safe way for that conversation to see a Library document's words, try a draft profile and hand it back.
ADR-0005 planned a read-only MCP server (Python MCP SDK) over the service layer; `mcp/` is an empty
package. ADR-0010 deliberately made MCP local and in-process with no HTTP Session, and said an HTTP-reachable
MCP needs its own token ADR. Claude Code runs on the owner's workstation and the instance on a separate host
(LAN HTTPS, ADR-0012), so it must be HTTP-reachable. This is that ADR.

## Decision

### 1. The flow

1. The owner adds the document to the Library (ADR-0026).
2. In Claude Code, the repo skill **`author-document-profile`** (`.claude/skills/author-document-profile/SKILL.md`)
   drives: `list_fill_targets` (the form and its fields) and `get_profile_schema`, `list_library_documents`
   (find the document), `read_document_words` (the words with positions), draft a profile, `test_profile`,
   iterate, then `propose_profile`.
3. The proposal appears in **Settings -> Document profiles** as *Proposed*, with the test result and the
   Reading re-run live beside the Library document (the side-by-side pane, ADR-0026). The owner reviews it and
   **activates** it, or retires it.

**Nothing goes live from the conversation.** Activation is only in the app, only with a Session (section 3).

The skill specifies: the order above; the profile rules (closed `pair` set, exact labels, no digit run of
six or more, no personal text, no values); always test before proposing; stop and report to the owner after
three failed iterations rather than loosening a `required` field to pass; never write document text, words
or figures into a repo file, commit, issue or PR; use invented values in any example. The skill is a
Developer slice; this ADR only specifies it.

### 2. The MCP server

- **Python MCP SDK, streamable HTTP, stateless, mounted in the app at `/mcp`** (ASGI sub-app from
  `finance360.main`, so it sits behind the existing HTTPS sidecar and the allowed-hosts check, ADR-0012). No
  second process: one more process on the same SQLite file and a second set of config would be the cost for
  no gain. The SDK's own DNS-rebinding protection is configured from the same allowed-hosts list. **stdio
  is rejected** as the client and database are on different machines.
- **Thin (ADR-0004):** `mcp/` holds only tool registration, argument/response shaping and the bearer
  check. It calls **only service functions**, enforced by an import-linter contract the same as `api/`'s.
  Logic that needs the Library and Documents together lives in `library.service` (the Library sits above
  Documents in the layer order and may import it): `test_profile_on_document` and `propose_profile`.
- **First tool set (profiles only; scope `mcp:profiles`):**

| Tool | Does | Writes |
|---|---|---|
| `list_fill_targets` | targets, their fields (key, type) | nothing |
| `get_profile_schema` | the profile JSON Schema and the closed vocabulary | nothing |
| `list_profiles` | key, version, target, status, claim phrases; the JSON of shipped profiles as worked examples | nothing |
| `list_library_documents` | id, file name, type, size, added date, text status, recognised profile key. Optional name filter. **No link labels, no text** | nothing |
| `read_document_words` | one named document: `pages` (at most 5 per call) of words with positions; masked as in section 4 | an audit row |
| `test_profile` | draft JSON + document id -> outcome, per field found / not found / ambiguous with page and matched label, each check's result, and overlap with other profiles' claims. Runs the same pure `read_document` path as the app. | an audit row |
| `propose_profile` | draft JSON + document id -> validated, **re-tested server-side**, stored as a Proposed profile (section 5) | one proposed row |

- **No tool writes a figure, record or link, or activates, edits or retires a profile.** This amends
  ADR-0005's "read-only first": `propose_profile` is the single write, to an inactive row that holds no
  values (profiles hold none, ADR-0024 section 3). Any later tool is a new decision.
- `test_profile` returns figures as text amounts (no more sensitive than the words already exported, and
  the "wrong number paired" failure needs them). It writes nothing, and the Reading is still never stored.

### 3. Access: a personal access token

ADR-0010's Session is a browser cookie and stays the only way into `/api`. `/mcp` takes a **bearer
token**, and only there.

- **Access token:** created and revoked in **Settings -> Access tokens** (owner Session only; a token can
  never create, revoke or activate anything). Random 256-bit, format `f360_` + url-safe text, **shown
  once**, stored as a SHA-256 hash (high entropy, so no argon2) with a display prefix, a label, **scopes**
  (first and only: `mcp:profiles`), an **expiry** (default 90 days, maximum one year) and `revoked_at`.
  Table `access_token`.
- **Default deny holds.** `/mcp` has its own guard dependency; the existing route guard test learns that
  `/mcp` is token-guarded and nothing else is. No token, an expired or revoked one, a wrong scope: 401.
  With no active token the server is, in effect, off (no flag, per ADR-0010).
- **Audit** goes to ADR-0010's append-only `auth_event` table (kinds `token_created`, `token_revoked`,
  `mcp_call`), `detail` = token id, tool, document id, pages, word count, outcome. Never content. A call
  that causes a database write (`propose_profile`) also writes the normal `audit_event` with
  `actor_kind="agent"` and the token id, as ADR-0010 provided. Settings shows each token's last use from
  these rows. This **amends ADR-0010's MCP bullet** (no longer only local and in-process) and adds the
  token kinds to its event set.
- Modest per-token rate limit (a routine value for the Developer). Tokens work on `/mcp` only; whether
  `/api` ever accepts one (scripts) is a separate decision.
- **Claude Code setup (documented in `docs/mcp-setup.md`, nothing committed):** the user runs
  `claude mcp add --transport http finance360 <instance /mcp URL> --header "Authorization: Bearer ..."` at
  user scope, with the token read from an environment variable on that machine. No `.mcp.json` in the repo
  (it would carry the host and make every role agent try to connect). The machine must trust the sidecar's
  certificate as the browser does (ADR-0012).

### 4. Privacy and the AI rule

- **No AI call is made by the app.** AGENTS.md rule 4's gateway governs the app's own calls to a model
  (redaction, `ai_log`, caps). Here the owner, through an authenticated client of their own, pulls a
  document's words; the client sends them to Anthropic under the owner's account. That is an authenticated
  **data export the owner initiates**, not an AI call, so it does not pass the gateway, and the app cannot
  see or log what the client does next. The `mcp_call` audit (what was exported, when, to which token) is
  the record. The Guide and the Settings page say plainly: *reading a document through Claude Code sends its
  words to Anthropic*.
- `read_document_words` is **explicit and per document**: one named Library document, at most five pages
  per call, only where text exists, and never a bulk export. `list_library_documents` returns no text.
- **Masking.** The tool applies the `document` class redaction of ADR-0024 section 6 (vocabulary names,
  ABN, TFN, e-mail), with the digit-run threshold at **six** to match the profile rule: a run of six or
  more digits that is not a money amount (no `$`, `,` or `.`) becomes `#` of the same length. Account
  numbers and member numbers are masked; money is kept (the draft needs it). Masking is irreversible and not
  tokenised. Free-text names and addresses not in the vocabulary cannot all be caught; the Guide says so.
  `test_profile` runs on the **unmasked** document server-side, so masking never changes a Reading. Because
  a profile cannot contain a six-digit run, nothing masked is ever needed in a label.

### 5. Proposed profiles: storage, loading and containment

Brings forward ADR-0024's `document_profile` table. `key`, `version`, `profile_json`, `source`
(`mcp` | `pasted` | `ai`), `status` (`proposed` | `active` | `retired`), `proposed_by` (login or token),
`created_at`, `test_document_sha256`, `test_snapshot` (JSON), `activated_at/by`, `ended_at/by/reason`,
`ai_log_id` (nullable; for ADR-0024 slice 2). **Shipped profiles are not rows**; Settings lists them with
source "shipped".

- **Append-only:** `key`, `version` and `profile_json` never change; a revision is a new `version` row;
  the lifecycle columns are each set once; every change is audited (ADR-0009 conventions, ADR-0022
  discipline).
- **`test_snapshot` holds the outcome, per-field found/not-found/ambiguous and check pass/fail, never a
  value or text**, so a Reading is still not stored. The review screen re-runs the profile live on the
  Library document to show values beside the page.
- **A proposal is accepted only if** it passes schema and Fill-target validation, and the server-side test
  gives `ok` or `warnings` with every required field found (ADR-0024 section 6's rule). Otherwise
  `propose_profile` returns the failure and stores nothing.
- **Activate (Session only):** re-runs the test; shows which other profiles also claim the test document
  (an overlap is a warning, since the first claiming profile by key reads); then registers it. Activating a
  key retires that key's previously active saved row.
- **Runtime:** at start-up `main.py` loads active rows after the shipped profiles, and activation or
  retirement registers or unregisters in the running process. **Precedence is ADR-0024 section 3's**: a
  saved active profile with the same `key` as a shipped one wins; a different key adds. (The Due item for a
  release shipping a different version of that key stays deferred to the flagged place in ADR-0024.)
- **Containment:** every profile is validated on save and again on load (schema, target, field keys,
  no-digit-run rule). A row that fails on load is skipped, logged by key and reason only, shown in Settings
  as "failed to load", and **never stops start-up** or affects other profiles. The matcher is closed code,
  so a saved profile has no behaviour to misbehave; a pathological one is bounded by the existing page and
  time limits.
- Paste-and-test uses the same table with `source=pasted` and the same Activate step.

### 6. Effect on the backlog

- **The super-fund statement profile slice** becomes "author and activate a super-fund statement profile through this flow". It is
  the dogfood slice, needs the `position.super_statement` Fill target and the Library's links, and ships **no
  profile in the app**: the profile lives in the owner's instance (`source=mcp`). Its synthetic fixture PDF and
  the tests that check the flow's tools against it stay.
- **The paste-and-test slice** gains the review and activate UI for Proposed profiles (side-by-side with the test result),
  retire, and failed-to-load display, and brings `document_profile` forward.
- ADR-0024 slice 2 (in-app model proposals) is **optional and later**; it writes to the same table with
  `source=ai`.
- A profile-less best-effort read (the common labels per Fill target field, with no profile) is a possible
  later aid, **not required** here.

### 7. Slices

(a) `document_profile` table, store, runtime loading and containment, Settings list with Activate and
Retire, paste-and-test. (b) `access_token` table, Settings -> Access tokens, bearer guard,
`mcp_call` audit, the `/mcp` mount with `list_fill_targets`, `get_profile_schema`, `list_profiles`,
`list_library_documents`, `read_document_words` (with masking) and `test_profile`, `docs/mcp-setup.md`, the
Guide; one or two sub-issues. (c) `propose_profile`, the repo skill and the profile-writing how-to.
(d) the super-fund statement profile as the dogfood.

**Non-goals:** MCP tools beyond profiles (a general read-only MCP over the household is a separate later
milestone; the mount, bearer guard, scopes and audit here are built so it adds scopes and tools without
change); in-app AI; auto-activation; a token accepted on `/api`; per-tool or per-document permissions
beyond the scope; more than one scope; token rotation UI; viewer-role tokens.

## Consequences

- A new layout becomes a conversation plus one review click, with no release and no API cost, and
  the model is never in the reading path: the owner's instance holds a deterministic profile afterwards.
- The first HTTP-reachable non-browser surface. Its exposure is bounded: a scoped, expiring, revocable
  hash-stored token, default-deny everywhere else, no write that goes live, every call audited.
- Document words leave the host for Anthropic whenever the owner reads one this way. Masking narrows but
  does not close this, and the gateway's `ai_log` does not see it; the audit shows what was exported.
- Amends **ADR-0005** (MCP read-only first: one inactive proposal write), **ADR-0010** (MCP no longer only
  local and sessionless; token kinds added to `auth_event`; `/mcp` token-guarded), **ADR-0024 sections 3
  and 6** (saved profiles arrive with this flow, `source` includes `mcp` and `pasted`; AI proposal is
  optional) and **ADR-0026 section 7** (pasted profiles share the table and Activate step).
- Forecloses: a profile going live without the owner activating it in the app; a token that can
  activate; a model or tool writing figures; bulk document export through MCP; a committed MCP config with
  the owner's host.

## Not decided here / routine follow-on

- Token and table column details, the rate-limit value, the exact masking patterns beyond those named, the
  SDK's mounting and stateless-mode specifics (Developer confirms the pinned version mounts under
  FastAPI), error wording, the snapshot JSON shape, and whether `list_profiles` paginates.
- One-line clarification of AGENTS.md rule 4 ("the gateway governs the app's own AI calls; an owner-initiated
  MCP export is audited separately"), left to the main session since this pass edits `docs/` only.
- The Guide (MCP and Access tokens, the Settings profiles page, "What to provide, when"), the synthetic
  fixture and its use in the tool tests (standing rules), and the skill's exact text.
- New iconography or design language: none; Settings uses the existing list and form patterns.

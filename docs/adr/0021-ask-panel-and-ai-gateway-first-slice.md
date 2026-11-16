# 0021. The Ask panel and the AI gateway's first slice

**Status:** Proposed
**Date:** 2026-11-03
**Amended by ADR-0022:** adds a second data class, `reference` (Rule figure capture), which admits user-pasted page text with amounts left in.
**Amended by ADR-0023:** Ask works with no AI (Guide search); answering is an optional provider over retrieved Passages. Sections 1, 4, 5, 6 and 10 are amended where marked.
**Issue:** — (part of the Orientation milestone)

## Context

A user can lose the big picture in the UI. A "guidance mode" driven by AI chat was floated. The agreed
shape is an **Ask panel** on every screen, not a mode switch, answering from the written Guide
(Markdown in `frontend/src/guide/`, bundled into the frontend) and from What's due (ADR-0020). AI
what-if questions come later and build on the panel; they are not decided here.

**Real current state.** No AI code exists: `grep -ri gateway backend/` finds nothing, there is no `ai`
or `knowledge` package, no AI setting in `config.py`, no `anthropic` dependency, no log table. ADR-0005
decided the gateway (redaction, data classes, per-task provider and model, local log, monthly cap) and
its "No AI in M1-M3" has now lapsed. This is the **first AI feature**, so this ADR also fixes the
gateway's first thin form, which later tasks (import mapping, document extraction, what-ifs) reuse.

Constraints: AGENTS.md rules 1 to 4 (no real data in the repo, no secrets, exact money, AI proposes and
code disposes, every call through the gateway), ADR-0004 (plug-in registry, plugins -> capabilities ->
core, import-linter), ADR-0005, ADR-0009 (audit is for changes to household data), ADR-0010 (secret
handling pattern), ADR-0014 (page patterns), ADR-0020 (Due items).

## Decision

### 1. What the Ask panel is

An **Ask panel** is a right-hand Sheet (ADR-0014 primitive) opened from a button in the app shell, so it
exists on every screen behind the auth gate and keeps its thread while the user navigates.

_Amended in review:_ from the `lg` breakpoint up the panel is **docked**, not a Sheet. It is a
column beside the page with no scrim and no focus trap, so the household can read and use a screen
while asking about it; a followed source opens beside it. Below `lg` it stays a Sheet. The thread and
an unsent question live in the panel's state, so closing loses nothing and asks nothing. It:

- **answers from two sources only** _(ADR-0023: Due items feed Written answers only; Search only searches the Guide)_: the Guide, and the current Due items (section 3);
- **explains and points.** An answer is plain text plus **sources**: Guide pages and screens, shown as
  links the user can follow. It has no tools, writes nothing and calls no service that mutates
  (AGENTS.md rule 4). The model's reply is data that deterministic code validates (section 5);
- **knows where the user is** from two values the client sends: `screen`, the route *pattern* (for
  example `/accounts/$accountId`, never the id), and `guide_slug`, set only when the user is on a Guide
  page (so on a `path-*` page that is the starting point they chose to read). A starting point is not
  stored (the Guide says it is not a setting), so nothing else is known or needed.
- **says "I don't know"** when the Guide and Due items do not cover the question, and points to
  [Start here](/guide).

### 2. Placement, dependency direction and the Guide's source

- **`capabilities/ai/`: the AI gateway.** Imports core only (and the provider SDK through a plug-in, below).
  Modules: `config` (settings), `gateway.py` (`complete(...)`), `redact.py` (pure), `vocabulary.py`
  (loads names from core models), `providers.py` (the `Provider` Protocol and registry), `pricing.py`,
  `caps.py`, `log.py`. The `ai_log` model lives in `core/models/`.
- **`capabilities/ask/`: the panel's backend.** `guide.py` (load and parse the Guide, extract link
  targets), `prompt.py`, `service.py` (`ask(...)`). Imports `ai`, `due` and core only. It reads **no data
  Capability** (Position, Tax and so on); reading them is a what-if concern with its own ADR.
- **`plugins/ai_providers/`:** `anthropic.py` (official SDK, no agent framework) and `sample.py`.
  Registered in `main.py` with the other registrations. One real provider; Ollama later is one more
  file behind the same Protocol, not built now.
- **Import-linter** (the Developer edits `pyproject.toml`; none weakened):
  1. `capabilities.ai` imports no other Capability; `capabilities.ask` imports only `ai`, `due`, core;
  2. nothing imports `ask`; `due` and the data Capabilities do not import `ask`; later Capabilities
     (imports mapping) may import `ai` but never `ask`;
  3. add `ai` and `ask` to both lists of the auth-independence contracts;
  4. `ai.redact` and `ai.pricing` are pure (no database, SDK or Capability);
  5. plug-in `ai_providers` imports `ai.providers` and the SDK, not the database, `ai.gateway` or any
     other Capability; the API reaches `ask` and `ai` only through their services.
- **The Guide has one source: `frontend/src/guide/`, unmoved.** The frontend glob and the existing link
  tests (`guide.test.tsx`, ADR-0020's drift test) are untouched. The image copies the directory a second
  time: one `COPY frontend/src/guide /app/guide` line in the runtime stage (the docker job already
  triggers on `frontend/`). The backend reads `FINANCE360_GUIDE_DIR`, defaulting to `/app/guide` in the
  image and to the repository's `frontend/src/guide` when run from source. The file format (`NN-slug.md`,
  first `# ` line is the title) is parsed again in Python in `ask/guide.py`; a backend test asserts its
  slugs and titles equal the directory listing and that it finds every link target, so the two parsers
  cannot silently diverge. Rejected: moving the Markdown to a shared top-level folder (churns frontend
  tests and Docker's `ui` stage for no gain), and the browser sending Guide text with each question
  (lets the client choose what reaches a provider).

### 3. What reaches the provider (data class `guidance`)

The gateway gains its first **data class**, `guidance`, the narrowest: it admits only these parts, and the
call refuses a part not declared. (ADR-0022 section 5 adds a second class, `reference`, for Rule figure
capture: it admits the user's pasted official page with its amounts left in, and the gateway refuses that
exemption for any other class or part.)

| Part | Public? | Notes |
|---|---|---|
| The Guide (all pages) | yes | Product text, no household data. Sent unredacted as a cached system block. |
| `screen` and `guide_slug` | yes | Validated: `screen` against `^/[a-z0-9/$_-]{0,80}$`, `guide_slug` against the loaded Guide. |
| Due items: `rule_key`, `importance`, `title`, `effect`, `link` for the current Effort level, important first, at most 10 | no | Already worded without amounts by ADR-0020 (accuracy wording, no balances). They can name an Account or Member, so they are redacted. |
| The user's question and earlier turns of the in-session thread | no | Free text, so redacted and amount-stripped. |

- **Guide-only questions need no household data at all.** The Due items are included on every call
  because "what should I do next" is the panel's main job and they carry no figures; it is a deliberate
  inclusion visible in the log, not an accident of the prompt. The Developer must not add Position,
  Valuations, Holdings, Tax records or any Money amount to this class. That is a different data class
  (`analysis`) needing the what-if ADR.
- **Redaction (the first real use of ADR-0005's layer).** Parts marked non-public pass through
  `redact(text, vocabulary)`, which tokenises (`[ACCOUNT_1]`, `[MEMBER_2]`, `[ENTITY_1]`,
  `[NUMBER_1]`, `[EMAIL_1]`):
  - the names of the Household, Members, Entities and Accounts, plus Account identifiers (exact match,
    longest first, case-insensitive on names);
  - any run of 6 or more digits and any e-mail address;
  - currency amounts (`$1,234`, `AUD 500`, a `k`/`m` suffix) become `[AMOUNT]` in the question: Ask never
    needs a figure, and a user who types one is not exempted from the rule.
  Tokens are stable within one call. The reply is **restored** (tokens back to names) before it goes to
  the client; `[AMOUNT]` and `[NUMBER_n]` restore to nothing, and an unknown token is left as it is.
  History sent back from the client is re-redacted, so earlier restored answers cannot leak a name.
  The panel's footer says: "Account names and numbers are removed before your question is sent."
- **Redaction is correctness-critical** (AGENTS.md invariant work): a Hypothesis property test that
  `restore(redact(x))` equals `x` for text built from the vocabulary, a golden test that a scripted
  provider never receives any vocabulary string or long digit run, and one mutation proof (disable
  redaction, see the golden test fail, restore).

### 4. Provider and model configuration

_Amended by ADR-0023 section 5: unset now means Search only, not off; `sample` is removed; `openai-compatible` replaces the "later `ollama`"._

Environment only (the repo holds placeholders in `.env.example`; real values live in the host's `.env`
or a mounted secret file, never committed):

```
# FINANCE360_AI_PROVIDER=anthropic          unset = AI off (the default)
# FINANCE360_AI_API_KEY_FILE=/run/secrets/finance360_ai_api_key   preferred (as ADR-0010)
# FINANCE360_AI_API_KEY=
# FINANCE360_AI_MODEL=claude-sonnet-5-5     default model for every task
# FINANCE360_AI_MODEL_ASK=claude-haiku-5-5  the Ask panel's default (per-task override)
# FINANCE360_AI_MONTHLY_CAP_USD=5.00
# FINANCE360_AI_DAILY_QUESTIONS=50
# FINANCE360_AI_LOG_DAYS=365
```

- Provider values: `anthropic`; `sample` (section 6); later `ollama`. An unknown value or a missing key
  with `anthropic` fails loudly at start-up (as `FINANCE360_BACKUP_TIME` does); unset is a quiet off.
- **Model defaults:** the gateway default is **Sonnet 5.5, `claude-sonnet-5-5`**, per requirements
  section 5d (Sonnet for most tasks; Haiku 5.5 for cheap classification; Opus 5.5 for hard extraction).
  **The Ask panel defaults to Haiku 5.5, `claude-haiku-5-5`** (the maintainer, 2026-11-03): answering from the
  Guide is simple and Haiku is cheaper. Sonnet is one variable away (`FINANCE360_AI_MODEL_ASK`). Both IDs
  are current; the Developer verifies the prices in `pricing.py` against the current pricing page in
  pass 1 and records that in the PR. The IDs are never hard-coded outside the default strings and
  `pricing.py`.
- `pricing.py` holds input, output and cache-read prices per million tokens for the known model IDs
  (Developer verifies against the pricing docs). An unknown model is priced at the dearest known entry
  and the log marks the cost `estimated`.
- The API key is read once into the provider object; it never appears in logs, the AI log, error
  messages, `/api/ai/status` or the client.
- New runtime dependency: the official `anthropic` SDK, exact-pinned; the Developer records the licence
  check; the `audit` workflow gates it.

### 5. Answer contract and guardrails

_Amended by ADR-0023 section 4: the model answers over the top retrieved Passages, not the whole Guide; the Guide size tripwire below is retired for a golden retrieval test. Validation is unchanged._

- The gateway returns text; the Ask service asks the model (system prompt, forced single structured
  reply, no tools) for `{answered: bool, answer: str, sources: [{kind: "guide"|"screen", ref: str}]}`.
  Max output 600 tokens. Not streamed in this slice.
- **The prompt rules:** answer only from the supplied Guide and Due items; say what you rely on; if
  they do not cover it, `answered: false`; never give tax, legal or investment advice or predict
  returns; do not ask for or repeat figures; plain text, no links in the text.
- **Deterministic validation (AI proposes, code disposes).** The valid `ref`s are a closed set built from
  the Guide itself: its page slugs, and the in-app link targets that appear in the Guide's Markdown
  (screens the Guide already points to, plus `/`). The service:
  1. drops any source outside that set;
  2. if `answered` is true but no valid source remains, or the reply is not parseable, replaces it with
     the fixed text "I couldn't find that in the Guide. Start here may help." with the Start here link,
     and records `outcome = unsupported` in the log;
  3. builds link labels itself from the Guide page titles, so the model chooses *which* page, never the
     wording of a link or a URL.
  The answer is rendered as plain text (no HTML, no Markdown, so injected content from an Account name
  can do nothing).
- **Testing without a real provider.** Tests inject a scripted `FakeProvider` (in `backend/tests`)
  through the provider registry. Cases: a good cited answer; an unknown source dropped; unanswerable
  becomes "I couldn't find that"; malformed JSON; provider error maps to a calm 502-style message;
  the captured payload has no vocabulary string, digit run or amount; the cap and the daily limit refuse
  without calling the provider. **CI never calls a real provider and holds no key.** Agents never do;
  the one real-provider check is the maintainer's, in the review slot with their own key.
- **Guide size budget.** At about 30 KB the whole Guide is roughly 8,000 tokens, small against the model's
  context and, as a cached block, cheap to repeat. A backend test fails if the Guide exceeds 60,000
  characters, so growth forces a deliberate return to this ADR (retrieval by link-to-screen and
  keyword overlap, still no embeddings) rather than silent truncation.

### 6. Disabled, capped, and review-slot behaviour

_Superseded by ADR-0023 sections 4 and 5: Ask is always on (Search only is the default), and the `sample` provider is deleted. The capped and error behaviour still holds for Written answers._

- **Finance360 works fully with AI unset.** `GET /api/ai/status` returns `{enabled, reason}` and nothing
  secret. The Ask button still shows. With AI off or capped, the panel shows a message and a list of Guide
  pages (the Guide's own table of contents) instead of a question box: "Ask isn't switched on. The Guide
  has the same answers; start here." plus a line that it is set up on the host (the Guide page says how).
  Hidden would leave the feature undiscoverable and the Guide unreferenced; a pointer is useful with
  nothing configured. `POST /api/ask` returns 409 when off or capped.
- **`sample` provider** (precedent: `FINANCE360_PROVIDER_TRANSPORT=sample`, ADR-0013): for the review slot
  and demos, no network. It returns a canned, labelled answer built from the Guide page whose title best
  overlaps the question. Not a quality bar, only a way for the maintainer to try the panel without a key.

### 7. The local AI log

- **`ai_log`** (core model, additive migration), separate from `audit_event` (ADR-0009: audit records
  changes to household data; a call changes none). Columns: `id`, `occurred_at` (UTC), `task`, `data_class`,
  `provider`, `model`, `outcome` (`ok`, `unsupported`, `error`, `refused_cap`), `sent` (the **redacted**
  text of the non-public parts exactly as sent, tokens and all), `public_parts` (names and sha256 of the
  public parts, for example the Guide's digest and page slugs, not their text), `received` (the model's
  reply **as received, still tokenised**), `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cost_usd_micros` (integer; no float), `cost_estimated` (bool), `why` (a short fixed reason such as
  "Ask panel question").
- So the log never holds a name, number or amount that was not sent, and since the sent text is already
  redacted, never one that was. Restoration happens after the log write and is not stored. A refused or
  failed call is logged too.
- **Retention:** `FINANCE360_AI_LOG_DAYS` (default 365). Rows older than that are deleted when a new row
  is written: no job. The table is in the database, so Backups cover it.
- **Inspectable:** `GET /api/ai/log?limit=` (newest first) and one read-only screen, **AI log**, at
  `/settings/ai-log`, linked from the Settings page and from the panel footer ("See what was sent").
  Existing table pattern and tokens; no new iconography.

### 8. Conversation scope

A short **in-session thread**: the client keeps up to the last 3 exchanges in React state and sends them
with each question. The server is stateless and stores nothing but the log. Closing the app or
reloading clears the thread; "New question" clears it earlier. Limits per request: question at most 500
characters, history at most 6 messages. Persisting conversations is rejected: it stores free text beyond
the log, needs retention and deletion design, and nothing in the first slice needs it.

### 9. Cost and limits for one household

- **Monthly spend cap**, `FINANCE360_AI_MONTHLY_CAP_USD` (default `5.00`; a decimal string parsed to integer
  micros, as ADR-0005 requires). Computed by summing `cost_usd_micros` over the log for the calendar
  month (Household time zone); no separate counter to drift. At the cap the gateway refuses
  before calling the provider and the panel shows when it resets.
- **Daily question limit**, `FINANCE360_AI_DAILY_QUESTIONS` (default 50), counted from the log. It counts
  Ask questions only (amended later: Rule figure capture, a few calls a year, neither uses it up nor is
  stopped by it); the monthly spend cap counts every task. One request
  at a time per instance (a lock; a second concurrent ask gets "one moment").
- At the Guide's size, even at Sonnet pricing, a question costs a few cents at worst and much less with prompt
  caching on the Guide block, so the defaults are safety nets, not a budget.

### 10. First slice: two Developer passes, one branch, one PR

_The "Do not build" list below is amended by ADR-0023: retrieval, embeddings and an OpenAI-compatible (Ollama) provider are now decided there._

**Pass 1, backend.** `capabilities/ai` (config, gateway, redact, vocabulary, providers registry, pricing,
caps, log) with the `ai_log` model and migration; `plugins/ai_providers` (`anthropic`, `sample`); `ask`
(guide loader, prompt, validation, Due items through the due service); `GET /api/ai/status`,
`POST /api/ask`, `GET /api/ai/log`; regenerated API client; `.env.example` placeholders and the compose
passthrough; the Dockerfile `COPY`; import-linter edits; the `anthropic` dependency; all tests in
section 5 plus config tests (unset is off, bad value fails, key never in `repr`/logs); the Guide page
`09-ask.md` (section 11) so the drift test can be written.

**Pass 2, frontend.** The shell button and Sheet panel, thread state, loading, error, off and capped
states, source links (a followed link closes the panel on narrow screens), "New question", the AI log
screen, screen tests with `fetch` stubbed, the remaining Guide edits, and screenshots of the panel (open,
off, answered) and the log at 390px and desktop. Existing lucide icon, existing Sheet and Table
patterns, existing tokens: **no new iconography or design-language additions.**

**Do not build:** what-if questions or any Position, Valuation, Holding or Tax data in a prompt; any
action that changes data, or tool use; streaming; voice; saved or server-side conversations; background
jobs; embeddings, vector stores or any retrieval index (the Guide fits in context; section 5 sets the
tripwire); the Ollama provider; an MCP server; per-user settings or UI for configuring the provider,
key or caps (host environment only); document extraction or import mapping; AI-generated Due items.

### 11. What the Guide must gain when the slice ships (the standing rule)

- **New `09-ask.md` ("Ask a question")**, in pass 1: what the Ask panel is and that it answers only from
  the Guide and What's due; plainly what is sent to the provider (the question, the Guide, and the
  wording of What's due items, with names, account numbers and amounts removed) and what is not (balances,
  Valuations, Holdings, Tax records); that it never changes anything and can be wrong, so check the page
  it cites; that it is off until set up on the host (the variables, by name, with no values) and that the
  Guide is the same content; the daily and monthly limits, **generated from the config defaults and
  checked by a backend test in the style of ADR-0020's drift test**; and where "AI log" shows exactly what
  was sent.
- **`01-start-here.md`:** a line under "What next" linking `09-ask.md`.
- **`02-what-finance360-answers.md`:** a question "I can't find something. Where do I ask?" pointing to
  the panel and to the AI log.
- **`03-how-the-pieces-fit.md`:** one line placing the panel.
- **The synthetic household** gains nothing: the log is not household data, so the fixture rule is met
  without change.

## Consequences

- One small gateway now carries every future AI task; adding a task is a data class, a model setting and a
  caller. The redaction layer is real and tested from its first use.
- Everything the provider sees is auditable in the AI log in the exact form it left the instance.
- Guide quality now has a consumer: a vague Guide gives vague answers, and the Guide size test forces the
  next design step before it silently degrades.
- Cost: a new dependency (`anthropic`), one table, one extra directory in the image, and a price table that
  needs upkeep when models change.
- Forecloses: letting the browser choose what is sent; storing conversations; model-authored links;
  figures in this data class; hiding the panel when AI is off.

## Alternatives rejected

- **A "guidance mode" toggle:** a mode to remember, the problem the Guide already addresses; the panel is
  available when asked and absent when not.
- **Hide the panel when AI is unset:** undiscoverable, and the pointer to the Guide is the right fallback.
- **Embeddings or a vector store for the Guide:** a moving part for a corpus that fits in one cached prompt.
- **Move the Guide into the backend or a shared top-level folder:** churn for no gain over a second `COPY`.
- **Redaction by the caller, not the gateway:** exactly the drift ADR-0005 exists to prevent.
- **A single `capabilities/ai` package holding the panel:** it would have to import `due`, forcing the
  gateway (which Imports will use) above a data Capability.
- **Token caps instead of a USD cap:** ADR-0005 decided a spend cap; the price table is the small cost.
- **Persisted conversations, streaming:** extra state and plumbing with no first-slice gain.

## Not decided here / routine follow-on

- **From the issue's Outcome, deliberately out:** AI what-if questions (and any `analysis` data class, tool use
  or reading Position, Tax or Projection), which get their own ADR on top of this panel.
- Exact wording of the system prompt, the panel copy, the button label and lucide icon, the AI log
  columns' display, the exact JSON/tool schema, token and price figures (verified by the Developer), the
  `sample` provider's matching, API field names, whether a Settings page also shows AI status.
- Ollama and per-task local routing; the MCP server; moving the Ask panel to Sonnet if Haiku's answers
  prove not good enough.
- Personalising answers to the Effort level or starting point beyond what the panel already sends.

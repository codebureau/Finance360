# 0023. Ask levels: search first, AI optional

**Status:** Proposed
**Date:** 2026-11-07
**Issue:** — (amends ADR-0021 and ADR-0022 section 5; no new issue)

## Context

ADR-0021 built Ask and ADR-0022 built "Read the page for me" on one assumption: a hosted Claude model
is configured. That assumption is wrong for many households. A Claude Pro subscription does not
include API access; the API is billed separately, and the owner may not want these features to cost money
by default. Today, with `FINANCE360_AI_PROVIDER` unset, Ask shows only a contents list and Rule figure
reading is greyed out, so out of the box both features are off.

The design-target host is a modest, CPU-only self-hosted machine: an older 4-core desktop-class CPU, about
8 GB RAM of which about 5 GB is free, no usable GPU, Linux, Docker compose. A local generative model of about 3B parameters takes 1 to 2 minutes per answer there;
an embedding model (33M to 137M parameters) takes well under a second.

What is already true and is reused: the Guide is a small, well-structured corpus (13 pages, about
60 KB) with a closed set of valid sources (ADR-0021 section 5); CONTEXT.md lists the words people use
and the term the app uses (`_Avoid_`); the gateway already redacts, caps and logs (ADR-0021); the
`sample` provider already contains a sentence matcher for Rule figures that the real deterministic
checks (ADR-0022 section 5) already police.

Constraints: AGENTS.md rules 3 to 5, ADR-0003, ADR-0004 (plug-ins, import-linter), ADR-0005, ADR-0011
(Backups), ADR-0021, ADR-0022.

## Decision

### 1. Two plug-in points, each with a "none"

Ask is two independent steps. Each is chosen on the host by environment, never per user.

| Step | Job | Options |
|---|---|---|
| **Retrieval** | Find the **Passages** relevant to a question | Keyword search (always on); plus embeddings when an embedding model is configured |
| **Answering** | Turn Passages into a written answer | **None** (show the best three Passages with links); or a gateway provider (local, Anthropic, other) |

With nothing configured: keyword retrieval, no answering. That is **Ask level: Search only**, the
default, costing nothing and working on a modest host. Configuring an answering provider gives
**Written answers**. Retrieval and answering do not depend on each other: embeddings can improve
Search only, and a model answers over keyword Passages if no embedding model is set.

### 2. Retrieval: keyword search, no AI

- **SQLite FTS5 (BM25)** over **Passages**. A Passage is one `##` section of a Guide page (a section
  over about 1,500 characters is split at paragraph breaks), carrying `corpus`, `page` slug, `heading`,
  `text` and the in-app link. Tokenizer `porter unicode61`; column weights favour title and heading over
  body (starting at 4, 3, 1; tuned by the golden test below).
- **The index is in memory**, a private `sqlite3` connection (`:memory:`), built from the Guide files on
  first use and rebuilt when a digest of the corpus changes. It is derived, so it is never in the data
  directory: no migration, no Backup content, no restore step, and a new release's Guide is live
  immediately. Building about 100 Passages takes milliseconds. Rejected: a table or file in the data dir
  (needs a migration and invalidation for data that can be rebuilt on demand).
- **Deterministic vocabulary expansion** from CONTEXT.md. Each `_Avoid_` word, and each `_Also_` word,
  maps to its term (Withdrawal strategy, Balance, Feed and the rest). A query token that matches
  adds the term to the query with OR; **the user's own word is never replaced**, so ranking decides.
  Only plain entries expand. An entry with a qualifier in brackets ("Account (an Account is a financial
  holding)") is a disambiguation, not a synonym, and is skipped. CONTEXT.md reaches the image with one
  `COPY` (the Guide's precedent, ADR-0021 section 2); the path is `FINANCE360_CONTEXT_FILE`, defaulting
  as the Guide does. If it is absent, expansion is off and search still works.
- **Query building:** alphanumeric tokens of the question, a short stop-word list removed, each quoted,
  joined with OR. No hits at all is the "I couldn't find that" result with the Start here link
  (ADR-0021 section 5), never an empty list.
- **Quality bar:** a golden test of about 25 plain-words questions, each with the page it must return in
  the top three. It is what lets the Guide grow safely: a Guide edit that breaks findability fails it.
- **The corpus grows by registration.** `capabilities/ask/corpus.py` defines a small `Corpus` Protocol
  (`passages() -> Iterable[Passage]`, a `public` flag, a digest) and a registry; `Guide` is the only
  implementation built. A new source (a Capability's help, notes) registers one more. **A corpus that
  is not `public` is searchable at Search only but its Passages may not be sent to a model** until an
  ADR gives them a data class. Nothing else about a second corpus is decided here.
- **Not AI, so not through the gateway.** Keyword search takes no provider, writes no `ai_log` row,
  counts against no cap and sends nothing anywhere.

### 3. Embeddings (optional retrieval)

- **Configured by** `FINANCE360_AI_EMBEDDING_MODEL` (unset: off) and `FINANCE360_AI_EMBEDDING_BASE_URL`
  (defaults to `FINANCE360_AI_BASE_URL`). It uses the OpenAI-compatible `/embeddings` call (section 5).
  Anthropic has no embeddings API, so the embedding endpoint is configured separately from the
  answering provider.
- **No vector database.** At a few hundred Passages, brute-force cosine over vectors held in a Python
  list is milliseconds. LanceDB, Chroma and the like would be a second store to back up, restore and
  migrate for a corpus smaller than one image. Vectors are held **in memory with the index**, computed
  on first use (one batched call per 64 Passages) and recomputed if the corpus digest or the model name
  changes. No table, no migration.
- **Fusion:** reciprocal rank fusion of the keyword ranking and the cosine ranking (k = 60), top results
  kept. Deterministic, no tuning knob.
- **Vectors are floats, and that is allowed.** AGENTS.md rule 3 forbids floating point for money,
  quantities and prices. An embedding is a similarity score over text; it is never money, never stored
  as a figure and never feeds one. State it in the module docstring so a reader does not "fix" it.
- **Through the gateway.** An embedding call is a model call: new data class **`retrieval`**
  (`passages` public, `query` not public, redacted as any question), tasks `embed`, logged in
  `ai_log` (the Passages by digest, as any public part; the redacted query as sent). `gateway.embed(...)`
  sits beside `complete(...)`; a provider that can embed implements an `Embedder` Protocol in
  `ai.providers`. **Failure falls back to keyword search silently** (logged as `error`); retrieval never
  fails a question because an embedding service is down.

### 4. Answering

- **Prompt over retrieved Passages, not the whole Guide.** The model receives the top six Passages (plus
  the Due items, unchanged from ADR-0021 section 3) and the question. This is the "return to this ADR"
  that ADR-0021's 60,000-character tripwire reserved, and it is what makes a slow local model usable:
  about 2,000 tokens in, not 15,000. It applies to every provider, Anthropic included, so there is one
  prompt shape. The size test is replaced by section 2's golden test. The valid sources stay the closed
  set of Guide pages and screens, and validation (ADR-0021 section 5) is unchanged.
- **Two steps in the API, because answering can take minutes.** `POST /api/ask` runs retrieval and
  returns the Passages at once (plus `answering: true` when a provider is configured). The panel shows
  them immediately. It then calls `POST /api/ask/answer` with the same question, history, `screen` and
  `guide_slug`; the server **re-runs retrieval itself** (the client never names Passages or text, so it
  cannot choose what reaches a provider) and returns the written answer, shown above the Passages with
  "Written by <model> from the passages below." A failure or a refusal (cap, busy) leaves the Passages
  in place with a calm line. Rejected: one blocking call, which would show nothing for 1 to 2 minutes.
- **Level wording.** At Search only, the panel says plainly what it is: header "Search the Guide", the
  best three Passages (page, heading, a short excerpt, link), the footer "Searches this Guide on your
  server. Nothing is sent anywhere." The footer about names being removed (ADR-0021 section 3) and the
  "See what was sent" link to the AI log appear **only when a model is used for retrieval or
  answering**. "Ask isn't switched on" no longer exists: Ask is always on.
- **Due items at Search only** are not searched; a "what should I do next" question lands on the Guide
  pages about What's due, with a link to the home screen. Written answers keep ADR-0021's use of Due
  items.

### 5. One `openai-compatible` provider

- **`plugins/ai_providers/openai_compatible.py`** speaks the OpenAI chat-completions and embeddings wire
  format over `httpx` (already a dependency). Ollama (`/v1`), LM Studio, the llama.cpp server, vLLM and
  many hosted services all answer it, so **levels "starter local" and "other models" are one plug-in**
  (AGENTS.md rule 5). `anthropic` stays, as the Claude choice. No new dependency.
- **Configuration** (follows `FINANCE360_AI_*`):

```
# FINANCE360_AI_PROVIDER=openai-compatible   (or anthropic; unset = Search only, the default)
# FINANCE360_AI_BASE_URL=http://ollama:11434/v1
# FINANCE360_AI_API_KEY_FILE / FINANCE360_AI_API_KEY   optional for this provider (Ollama needs none)
# FINANCE360_AI_MODEL=<answering model>      required for this provider (no Claude default applies)
# FINANCE360_AI_MODEL_ASK / _RULE_CAPTURE    per-task overrides, as today
# FINANCE360_AI_EMBEDDING_MODEL=<embedding model>        optional (section 3)
# FINANCE360_AI_EMBEDDING_BASE_URL=                      optional, defaults to BASE_URL
# FINANCE360_AI_TIMEOUT_SECONDS=180                      per call; raise for a slow CPU model
# FINANCE360_AI_PRICE_INPUT_PER_MTOK_USD / _OUTPUT_      optional decimal strings; unset = free
```

  A missing `BASE_URL` or `MODEL` with this provider fails at start-up, as ADR-0021 section 4 does for a
  missing key. Existing variables, the cap and the daily limit keep their meaning.
- **Structured output.** The request carries a JSON schema (a new optional `json_schema` on
  `ProviderRequest`, which the Anthropic provider ignores because it already forces its structured
  reply). The provider sends `response_format: {type: "json_schema", json_schema: {...strict}}`, plus
  `temperature: 0`. If the server answers 400 to `response_format`, the provider retries once without
  it and remembers that for the process. **Either way the reply is untrusted data**: the existing
  tolerant `_parse` and the deterministic validation apply (ADR-0021 section 5, ADR-0022 section 5), and
  the prompt always also says "reply with JSON only". A small model that returns malformed JSON
  produces the fixed "couldn't find that" result with the Passages still shown; it does not break the
  panel.
- **Cost and caps for local models.** A provider that does not price (`openai-compatible` with no price
  variables) logs `cost_usd_micros = 0` and `cost_estimated = false`. The monthly cap therefore does not
  bound it; it bounds a hosted service only if the two price variables are set, which the Guide says.
  **The daily question limit still applies** to answering (it is a load limit as much as a spend limit
  on a CPU-bound host), counting Ask answers as today. Embedding calls count toward neither the daily
  limit nor cost.
- **Redaction applies to local models too.** The gateway does not know or care that the URL is on the
  compose network: names and identifiers are removed before any model sees them. That keeps one rule and
  one log, and a household that later points `BASE_URL` at a hosted service has not weakened anything.
- **The `sample` provider ends.** It existed because Ask and Rule figure reading needed a key to be
  tried. At Search only, both now work for real with no key, so a fake model has no job: the review slot
  shows the real default. Delete `plugins/ai_providers/sample.py`, its factory entry and its price row;
  `FINANCE360_AI_PROVIDER=sample` becomes an unknown value and fails at start-up like any other. Tests
  keep their scripted `FakeProvider`. (The Developer greps `.env`, compose, fixtures and scripts for the
  value and removes it.)

### 6. Rule figure reading follows the same split

- **With a model** (any answering provider): ADR-0022 section 5 is unchanged.
- **With none:** a deterministic **page reader**, `capabilities/rulecapture/reader.py`, the sample
  provider's sentence matcher promoted to a real, pure module. It takes the specs and the pasted text and
  returns proposals in the **same shape** (`key, fy, value, quote, provisional, note`) with no
  `confidence`. They go through the same `_check` as a model's: kind and unit, plausible range, the quote
  must appear in the pasted text, the number must appear in the quote. Then the user's Confirm. Bands are
  not read (as today); a figure it cannot match is "Not found; enter it".
- **It is not an AI call:** no gateway, no `ai_log` row, no cap, nothing leaves the instance, so the
  pasted text needs no redaction. The pasted text is not stored.
- **Origin is recorded, not blurred.** `rule_capture.method` gains **`matched`**
  (`typed` | `ai` | `matched`). On `matched` rows `ai_log_id` is null and the proposal is kept in the
  existing `ai_value_json` column (so a correction is still visible; renaming the column is optional, and cheap in the
  same migration that widens the `method_known` CHECK). The screen labels the origin "Matched by words
  on the page" for `matched` and "Read by AI" for `ai`, and the trace card (ADR-0022 section 3) prints
  it. No confidence is shown for `matched`.
- **Model unavailable falls back to the reader.** If a provider is configured but off, capped or
  failing, "Read the page for me" uses the reader and says why, instead of greying out. Typed entry is
  unchanged.
- Rule figure reading is no longer greyed out in any configuration. The Guide's "needs Ask to be set up"
  sentence goes.

### 7. Deployment: optional Ollama

- **`docker-compose.ollama.yml`**, an override file, adds an `ollama/ollama` service on the compose
  network only: **no published port**, a named volume for models, `OLLAMA_KEEP_ALIVE` short so memory is
  returned between questions. It is not started by the default or direct files. The app reaches it at
  `http://ollama:11434/v1`. The model volume is **not** a Backup (models are re-pullable; ADR-0011
  scope is unchanged).
- **Host constraints the slice must respect:** about 5 GB free RAM and CPU only. A generative model
  must be about 3B to 4B parameters at 4-bit quantisation, with a small context (retrieval keeps prompts
  near 2,000 tokens); an embedding model of 33M to 137M parameters. Choosing the exact names is
  configuration, not code, and is the slice's call; they must be stated in the deploy docs with the
  measured time per answer on the host. **A 3B model is not the default**: it costs 1 to 2 minutes an
  answer on a host like this.
- The Caddy sidecar and any proxy timeout (ADR-0012) must allow the configured call time; the Developer
  checks and documents it.

### 8. Sequencing

**Slice 1: Search only, deterministic reader.** No new dependency (SQLite FTS5 from the standard
library; the Developer confirms it is compiled in on the image's Python and on CI, and says so if not).
`corpus.py` and the Guide corpus; the in-memory FTS index; vocabulary expansion and the `COPY` of
CONTEXT.md; the two-step API (`POST /api/ask` returns Passages; `POST /api/ask/answer` does what
`ask` does now, over retrieved Passages); panel states for Search only and Written answers; the golden
test; the page reader and `method = matched`, the origin label, the fallback; `sample` removed;
`/api/ai/status` extended (`retrieval`, `answering`); the Guide pages (`13-ask`, `09-rule-figures`,
`02-what-finance360-answers`, `01-start-here` where they name AI as required) and the "What to provide,
when" schedule if it changes; regenerated API client; screenshots of the panel at Search only and with a
scripted answering provider. An invariant-style test for the reader's checks reuses ADR-0022's.

**Slice 2: models you run yourself.** The `openai-compatible` provider (chat, embeddings, JSON schema
and its fallback); `gateway.embed`, the `retrieval` data class and the in-memory vectors with rank
fusion; the new settings and start-up validation; optional price variables; `docker-compose.ollama.yml`
and `docs/` deploy notes with measured times on the host; `.env.example`; the Guide's setup text.
Tests run against a stub HTTP server (httpx mock transport); CI never contacts a model.

**Neither slice builds:** a vector database or any persisted index; a corpus other than the Guide;
streaming; GPU support; model download or management from the app; per-user or in-app provider settings;
fine-tuning; reranking models; a cost estimator for hosted OpenAI-compatible services beyond the two
optional price variables; changes to redaction or data classes beyond `retrieval`.

## Consequences

- Ask and Rule figure reading work for the household today at zero cost, and the AI log is empty until
  someone chooses a model. A modest host needs nothing new.
- A model becomes an upgrade to a working feature, not a prerequisite. Local, Claude and any
  OpenAI-compatible service are one configuration choice behind one gateway.
- Guide quality now matters for findability, and the golden test and vocabulary expansion give it a
  deterministic floor that does not drift with a model.
- Cost: an index module, a corpus registry, an `Embedder` Protocol, one provider, one reader, a widened
  `method` value (a migration: `method_known` is a CHECK constraint), a second compose file, and a rewritten Ask prompt.
  `sample` is deleted. Existing Ask unit tests change shape (Passages in, validation unchanged).
- Search only cannot synthesise across pages or reason about the household; it points. That is the
  honest limit, and the panel says it.
- Forecloses: a vector database; a persisted search index; sending a client-chosen Passage to a model;
  sending a non-public corpus to a model without a data class; presenting keyword search as AI; an AI
  call outside the gateway; a 3B-class model as the host default.

## Alternatives rejected

- **LanceDB, Chroma or another vector DB:** a second store to back up, restore and migrate for a few
  hundred Passages that brute force searches in milliseconds.
- **An Ollama-only provider:** the OpenAI-compatible wire format covers Ollama and the rest for the same
  code. A provider per vendor is the framework rule 5 warns against.
- **Keeping Ask AI-only:** the status quo, and the feature is dead on a host with no key and no budget.
- **A 3B model as the default on the host:** 1 to 2 minutes an answer on an older CPU, and it consumes
  most of the free RAM. Opt-in, with the times published.
- **A persisted FTS table or vector table:** needs migrations and invalidation for derived data.
- **Whole-Guide prompt for answering:** 15,000 tokens a question is the cost that makes a slow model
  unusable; retrieval is the point of this ADR.
- **Keeping `sample`:** once the real default needs no key, a fake model only blurs which answers are
  real.
- **Logging the deterministic reader in `ai_log`:** it is not an AI call; the AI log means "what was
  sent to a model", and the capture row already records origin and quote.

## Not decided here / routine follow-on

- Slice issues and their sizes (main session with the maintainer); whether Slice 1 splits into search and reader.
- Exact stop-word list, column weights, snippet length, top-k values, prompt wording, JSON schemas, API
  field names, panel copy, heading anchors in Passage links, the golden question list.
- The embedding and generative model names, measured times, memory limits and the exact deploy notes
  (Slice 2).
- Any second corpus, and a data class for one that is not public.
- Whether `ai_value_json` is renamed; the migration shape for `method`.
- Ask what-ifs over household data (ADR-0021 still defers them to their own ADR).
- Streaming, if answers on a slow model prove too slow even after the Passages appear.

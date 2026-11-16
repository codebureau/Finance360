# Finance360 — Agent Instructions

This file follows the open [AGENTS.md](https://agents.md) convention. It is the **canonical,
tool-agnostic** set of working rules for any coding agent in this repo (Claude Code, Codex, Cursor,
Copilot, Gemini CLI, …). Tool-specific mechanics live in that tool's own file (e.g. `CLAUDE.md`),
which imports this one.

## What Finance360 is

A **personal-first, open-source-ready** household wealth-planning platform for one Australian
household: total financial position, look-through investment analytics, and retirement projection
with best/likely/worst Scenarios. Read these before substantive work:

- [`docs/requirements.md`](docs/requirements.md): vision, principles, functional and
  non-functional requirements, technology decisions, roadmap. Built from four `/grill-me`
  elicitation sessions.
- [`CONTEXT.md`](CONTEXT.md): the **controlled vocabulary** (see below).
- [`docs/adr/`](docs/adr/): decisions and their reasoning.
- [`docs/roadmap/index.md`](docs/roadmap/index.md): current milestone and candidates.

## Non-negotiable rules

1. **No real financial data in the repo, ever.**
   - `private/` (the household inventory and sample documents) and all instance data directories
     are gitignored.
   - Test fixtures are **synthetic**, shaped like real exports but invented.
   - Never copy real values, account numbers, names, payment references or file names from a
     real export into code, tests, docs, issues, PRs or commit messages.
   - If you're unsure whether something is real, treat it as real.
   - **No real figures, names or dates from the owner's records in issues, PRs, comments or commit
     messages.** Examples use invented values. A private denylist check (`scripts/guard_denylist.py`)
     enforces this on added lines, commit messages and PR text.
2. **No secrets in the repo.** Use environment variables or a mounted secrets file, with
   placeholders in `.env.example`.
3. **Money is exact.**
   - Store money as integer minor units plus a currency code, through the shared Money type.
   - Unit quantities and prices are scaled integers or decimal strings.
   - **Never use floating point for money, quantities or prices**, including SQLite `Numeric`,
     which silently becomes a float.
   - Currency is always attached to an amount.
4. **AI proposes, deterministic code disposes.** No AI output writes figures directly. AI
   proposals pass through human confirmation or deterministic validation. Every AI call the app
   makes goes through the AI gateway (redaction + local log). An owner-initiated export over MCP
   to the owner's own AI client is not an app AI call: it is token-guarded and audited (ADR-0027).
5. **Patterns over hard-coding, thinnest first implementation.** Vendors, jurisdictions and account
   kinds are plug-ins (Importers, providers, Behaviours, Rule packs), not special cases. But build
   one plug-in, not a framework for five.
6. **Build the picture, then colour it in.** Prefer the thinnest change that reaches an observable
   outcome (a screen, an API response, a real calculation) over deepening one layer in isolation.
   If correctness genuinely requires depth first, say so explicitly.

## Controlled vocabulary (mandatory)

`CONTEXT.md` is authoritative for what every domain concept is called. **Use its terms exactly** in
identifiers, routes, UI copy, issue titles, ADRs and prose. Its `_Avoid_` lists bind code as much as
prose.

- It is a glossary, not a decision record.
- **Adding a term that only names something already decided is routine:** do it in the PR that
  needs it.
- **Adding a term that makes a decision needs an ADR first.**
- When an ADR renames something, update `CONTEXT.md` in the same PR.
- An absent term means "not yet settled," not "has no name."

## Architecture boundaries

A **modular monolith** (ADR-0004):

- `core/`: the domain model.
- `capabilities/`: one package per Capability.
- `behaviours/` and `rulepacks/`.
- `plugins/`: Importers and reference-data providers.
- `api/` and `mcp/`: thin layers over the service layer.
- `jobs/`: the in-app scheduler.
- `frontend/`.

Rules:

- **Dependency direction:** plugins → capabilities → core, never the reverse. API, MCP and jobs call
  only the service layer. This is enforced in CI (import-linter); don't weaken the contract to make
  a change pass.
- **Every external source maps into canonical records** (ADR-0003). Nothing past the canonical layer
  knows which provider supplied a value, except through provenance.
- **The projection engine is pure functions over immutable inputs.** No I/O inside it.

## Commands

Commands marked *planned* don't exist yet; the issue that makes each real updates its row.

| Purpose | Command |
|---|---|
| Backend deps | `uv sync` |
| Frontend deps | `npm ci` (in `frontend/`) |
| Dev (API auto-reload + Vite) | `uv run python scripts/dev.py` (honours `FINANCE360_PORT` and `FINANCE360_DATA_DIR`; Vite prints its own port) |
| Frontend only (Vite) | `npm run dev` (in `frontend/`; proxies `/health` to `FINANCE360_PORT`, default 8000) |
| Run built app (no reload) | `npm run build` (in `frontend/`), then `FINANCE360_STATIC_DIR=frontend/dist uv run python -m finance360.main` |
| Single image (direct publish, no HTTPS proxy) | `docker compose -f docker-compose.yml -f docker-compose.direct.yml up --build`; a second instance: `FINANCE360_PORT=8123 docker compose -p finance360-b -f docker-compose.yml -f docker-compose.direct.yml up --build`. Plain `docker compose up` is the deploy-host shape: HTTPS proxy on `FINANCE360_HTTPS_PORT` (ADR-0012) |
| Lint / format | `uv run ruff check .` · `uv run ruff format --check .` · `npm run lint` (in `frontend/`) |
| Types | `uv run pyright` · `npm run typecheck` (in `frontend/`) |
| Tests | `uv run pytest` · `npm test` (in `frontend/`) |
| Boundaries | `uv run lint-imports` (contract in `pyproject.toml`) |
| Backup now (host) | `docker compose exec finance360 python -m finance360.backup run --reason manual` (setup: `docs/backup-setup.md`). The backup integration tests are tier 3 (`pytest -m platform`): they need `restic` and `rclone` on PATH, and Linux |
| Restore (host) | `docker compose stop`, then `docker compose run --rm finance360 python -m finance360.backup restore [--from local\|remote\|<repo>] [--backup latest\|<id>]`, then `docker compose up -d` (`docs/restore-runbook.md`). Refuses (exit 3) while the app serves the data dir |
| Regenerate API client | `npm run generate:api` (in `frontend/`; needs `uv`). Run after any API route or response-model change and commit `frontend/src/api/schema.d.ts` |
| API client drift | `npm run generate:api` (in `frontend/`), then `git diff --exit-code frontend/src/api`; must produce no diff |
| Docker image | `docker build -t finance360:ci .` |
| Install commit hooks (once per clone) | `uv tool install pre-commit`, then `pre-commit install` (gitleaks, `private/`/export-path guard, personal-terms denylist on the staged diff and on the commit message, ruff, eslint). Run all: `pre-commit run --all-files`. Needs `uv` and `npm ci` done. Blocked-path patterns: `scripts/guard_patterns.txt` |
| Personal-terms denylist | `python scripts/guard_denylist.py` (staged diff) · `--base <ref>` (added lines since a ref, as CI) · `--all` (every tracked file, the final gate) · `--text <file>` (a commit message or PR body). Reads `private/denylist.txt` (gitignored; override with `FINANCE360_DENYLIST`): one term per line, `#` comments, case-insensitive on word boundaries, `docs:` prefix = prose paths only. Output never prints a term (`--show` does, locally). No file locally passes with a notice; in CI the `FINANCE360_DENYLIST` Actions secret is required (fork PRs skip) |
| Reset a lost password (host) | `python -m finance360.auth reset` (run via `docker compose exec finance360 ...`; ADR-0010) |
| Deploy a release (maintainer only) | `deploy <tag>` · `deploy <tag> --first` (`deploy/deploy.py`; host details in gitignored `deploy/target.local.env`; see `docs/deploy.md`). Needs Python 3.10+ and `ssh`. Agents never deploy to a real host; test with a throwaway `--config` |
| Screenshots | `uv run python scripts/screenshot.py --pr <n> --base http://127.0.0.1:<port> --password <pw> accounts accounts/4` against a throwaway instance (never :8360). Phone and desktop width, light and dark; fails on sideways scroll. Saved per PR, issue or milestone (`--pr`, `--issue`, `--milestone`) under `<repo>-screenshots/` beside the repo (or `FINANCE360_SCREENSHOTS`); open that folder's `index.html`. One-time: `uv run playwright install chromium` |
| Review a PR (maintainer only) | `review <PR#>` · `review --stop` · `review --reset` (`scripts/review.py`; see "Review slot" below). Needs Python 3.10+, `git`, `gh`, Docker; not `uv` |

**Agents run tier 1 locally (see "Test tiers"); CI is the gate for the rest.** CI is
`.github/workflows/ci.yml`: three parallel jobs on Python 3.14 and Node 25 (the image's versions;
keep them in step with the `Dockerfile`). Within each job the steps run in this order:

1. **backend**: `uv sync --frozen` → `ruff check .` → `ruff format --check .` → `pyright` →
   `pytest -m "not slow and not platform"` (tier 1) → `pytest -m slow` (tier 2, only when its
   paths changed; see below) → `lint-imports` → `alembic upgrade head` →
   `alembic check` (against a temp `FINANCE360_DATA_DIR`; fails if a model change lacks a migration).
2. **frontend** (in `frontend/`): `npm ci` → `npm run typecheck` → `npm run lint` → `npm test` →
   API client drift (`npm run generate:api`, then `git diff --exit-code src/api`).
3. **docker**: `docker build .`
4. **security** (`.github/workflows/security.yml`, a separate workflow, three parallel jobs): gitleaks
   over the PR range (default rules plus the identifier-shape rules in `.gitleaks.toml`);
   `scripts/guard_paths.py` over changed files; `scripts/guard_denylist.py` over the PR's added lines
   and its title and body, using the `FINANCE360_DENYLIST` Actions secret (fails if the secret is
   empty, skips on a fork PR; the weekly run scans every tracked file). Locally,
   `pre-commit run --all-files` covers the first two; the denylist hook scans the staged diff.
5. **audit** (`.github/workflows/audit.yml`, a separate workflow): `pip-audit` (from `uv export`) and
   `npm audit --audit-level=high`, as two parallel jobs.
6. **platform** (`.github/workflows/platform.yml`, tier 3): installs pinned restic and rclone, then
   `pytest -m platform`. Runs on PRs that change backup or restore code, on release tags, and on
   demand. Migration and model changes are covered at the release tag; run it on demand before
   tagging if in doubt.

**What runs when.** A change that touches only `docs/**` or Markdown files skips `ci.yml` (and
`platform.yml`). On a PR, `ci.yml`'s `changes` job then runs only the affected jobs:
- **backend:** any change outside `frontend/` (the backend tests read `scripts/`, `deploy/`,
  `fixtures/` and the compose files).
- **frontend:** a change under `frontend/`. A change to the API it is generated from (`backend/`,
  `scripts/export_openapi.py`, `pyproject.toml`, `uv.lock`) runs only its API client drift step.
- **docker:** a change to anything the image copies in.
- **slow tier** (a step in backend): a change to what it exercises: `backend/finance360/` `core/`,
  `api/`, `auth/`, `jobs/`, `migrations/`, `backup/`, `mcp/`, `main.py` or `config.py`; `fixtures/`;
  dependencies; `conftest.py`, `support.py` or a slow test file itself. A change only to a
  capability or its unit tests skips it. `test_ci_workflow.py` keeps the pattern's file list equal
  to `SLOW_FILES`.

**Drafts run nothing but `security.yml`.** `ci.yml`, `audit.yml` and `platform.yml` skip a draft PR
and run when it is marked ready, then on each push. So open a PR as a draft, and mark it ready to run
CI once the local checks (tier 1, plus the slow tier for the area) pass. While reworking a ready PR
after review, convert it back to draft until all the fixes are pushed, then mark it ready again.

A change to `ci.yml` itself, a release tag (`v*`), the weekly scheduled run and a manual run each run
everything. `security.yml` (gitleaks, the path guard and the denylist) runs on every PR regardless, docs-only
included, plus weekly (a full-history scan). `audit.yml` runs on a PR only when it changes
`pyproject.toml`, `uv.lock`, `frontend/package.json` or `frontend/package-lock.json`, plus weekly
(which catches a newly published advisory against an unchanged lockfile) and on manual dispatch.
**None of them runs on push to `main`:** the PR run is the gate, and a post-merge re-run would
roughly double Actions minutes for no new signal. The weekly run catches a merge of a PR that was
behind `main`. Keep this list and the workflows in sync when any of them changes.

## Testing protocol

**Test tiers.** Test effort scales with risk; not every check belongs in every pass.

| Tier | What | Where it runs |
|---|---|---|
| 1. Fast | lint, format, types, boundaries, unit and integration tests (`uv run pytest`: its default excludes tiers 2 and 3), frontend typecheck, lint and tests | every Developer and Tester pass, every CI run |
| 2. Slow (`@pytest.mark.slow`) | real-server (uvicorn) tests, concurrency tests, the populated-database upgrade test | CI on a PR that touches its paths (see "What runs when"), and weekly. Locally when the change touches that area, including any fixture change: `uv run pytest -m slow -k <area>` |
| 3. Platform (`@pytest.mark.platform`) | backup and restore with real restic and rclone, Linux only | `platform.yml`: when backup, restore, migration or model code changes, on release tags, on demand. Never in a feature pass |
| 4. Human | screenshot sweeps at every width and theme, full browser walk-through | per milestone. Per PR: only the changed screens, at 390px and desktop, plus the maintainer's review slot |

Slow tests are marked by file (`SLOW_FILES` in `backend/tests/conftest.py`); a new real-server or
concurrency test file goes on that list, and in `ci.yml`'s slow pattern. Platform tests carry `@pytest.mark.platform`.

**Rigour scales with risk.**
- **Invariant work** (migrations, money maths, supersession, masking, anything touching live data):
  write the test that would catch the defect, break the code once to prove it fails, restore it.
- **Everything else:** ordinary tests, no mutation proofs, no extra concurrency or real-server tests
  unless the change introduces a new write path or something shared and counted.
- **Thinnest first implementation, enforced.** Briefs name what *not* to build. Optional extras
  (status pages, sample transports, extra reporting) wait until a slice needs them.
- **Size cap.** A Developer pass covers at most one or two sub-issues. If it outgrows that, stop at
  a coherent point and report.

- **Automated pyramid first.**
  - Unit tests for pure logic.
  - Integration tests against a real temporary SQLite database. Don't mock the database to fake
    integration coverage.
  - **Hypothesis property tests** for invariants: money round-trips exactly, Holdings never go
    negative, ownership shares sum to 100%.
  - **Golden tests** against known answers.
  - **Real-export check for Importers.** An Importer isn't done until
    `backend/tests/test_importer_real_samples.py` passes locally against real exports in
    `private/samples/<vendor>/` (gitignored; the test skips where the folder is absent).
- **Agents never use the review slot.** In worktrees, use throwaway temp data directories and random
  ports (`FINANCE360_DATA_DIR`, `FINANCE360_PORT`).
- **Review slot (the maintainer's pre-merge testing, ADR-0006):** a fixed worktree on
  `http://localhost:8360`, driven by `review <PR#>`, using the synthetic household by default. Every
  PR's **"How to test"** section must give concrete steps with expected results against the
  synthetic household. The Developer runs them in a clean instance before reporting.
- **Review slot mechanics.** `review <PR#>` fetches the PR into the slot worktree
  (a sibling worktree `<repo>-review`, created on first use), builds the Docker image, serves it on :8360 with the
  persistent data dir `<repo>-review-data` beside it, and prints the PR's "How to test" (it fails if the
  section is missing or empty). `review --stop` frees the port; `review --reset` wipes the data dir
  and reloads the slot's `fixtures/sample_household/`. Override with `FINANCE360_REVIEW_SLOT`,
  `FINANCE360_REVIEW_DATA`, `FINANCE360_REVIEW_PORT`, `FINANCE360_REVIEW_PROJECT`. Add `scripts/` to
  `PATH` (or run `scripts\review.cmd`) to type `review`. **Agents never run it with the defaults**;
  to test the script itself, point all four variables at throwaway values.
- **Standing rule: the synthetic household grows with the product.** Every slice that introduces
  data extends `fixtures/sample_household/` (invented values only) so its "How to test" has
  something to look at. A slice that adds data without extending the fixture is incomplete.
- **Standing rule: the Guide grows with the product.** The Guide (`frontend/src/guide/*.md`, shown
  at `/guide`) is how someone sees where a screen fits. Every slice that adds or changes a screen,
  an input the household must provide or a calculation updates the Guide page for its area, and the
  schedule in "What to provide, when" if it changes what is provided or how often. Describe only what
  the app does today; label planned work "coming later". A new screen also needs a Guide link: a
  new sidebar group links its heading to a Guide page. A slice that does this without touching the
  Guide is incomplete. The Guide's tests fail on a link to a page or route that does not exist.

## Git workflow (mandatory)

- **`main` is protected by instruction.** Never commit or push directly to `main`. The single
  bootstrap commit is the only exception.
- One feature branch per issue (`issue-<n>-<slug>` or `feature/<slug>`), branched from the latest
  `main`. Every change lands via a PR against `main` that references its issue (`Closes #N`).
- **Agents never merge, ever.** Merging is always the maintainer's own action. This holds even if they say a
  PR is approved or "merge it": treat that as "ready for you to merge," then stop. An agent may help
  *resolve* conflicts if the maintainer explicitly asks; the merge itself stays theirs.
- **A PR is merge-ready only when** CI is green, or skipped for a docs-only change (and, if a Tester
  pass was requested, Tester has marked it ready), and the maintainer has ticked
  *"Maintainer tested in review slot"*.
- No force-push to `main`. No `--no-verify` or other hook bypasses. No bypassing status checks.
- Prefer new commits over amending, and a new branch over reusing one whose PR already merged.
- **Release tags are annotated and carry release notes.** Write them user-focused first (what the
  household can now do or will notice, in plain words), then brief technical detail: the PRs and
  issues, any migrations and whether a rollback needs a restore, and deploy notes. No real figures
  or names.

## Issue tracker conventions (GitHub, `codebureau/Finance360`)

- **Milestones are sequential, demonstrable checkpoints**: they close when their observable outcome
  is real, not when a phase of work ends. They are native GitHub Milestones, kept in sync with
  `docs/roadmap/index.md`. Every scoped issue is assigned to its milestone.
- **Labels.** Native Issue Types need a GitHub org, so labels are used instead. Three orthogonal
  axes plus two flags:
  - `type:` `feature` | `task` | `bug` | `chore` | `docs` | `adr`
  - `category:` `cash` | `investments` | `super` | `overseas-pension` | `property` | `vehicles` |
    `loans` (the Categories in `docs/catalogue.md`), plus `entitlements` (Age Pension, foreign
    State Pensions, DB pensions) and `income-expense`
  - `capability:` `import` | `reference` | `household` | `position` | `lookthrough` | `projection` | `tax` | `goals` |
    `knowledge` | `ai` | `platform` (`platform` covers infra, deploy, auth, tooling)
  - `needs-design`: needs an Architect/ADR pass before Developer starts.
  - `Inbox`: a raw, untriaged thought. Product Manager triages it to one of: duplicate (close and
    link), feature idea (roadmap entry), backlog issue, or still too raw (ask questions).
- **Sub-issues:** a `type:feature` issue is a vertical slice, decomposed into native sub-issues
  (`type:task`), each sized for one Developer pass. **Decompose before Developer starts** whenever a
  feature's Outcome names several pieces. A PR closes the sub-issue it ships, never the parent
  prematurely. One umbrella index issue parents every epic, so the whole backlog is visible as one
  tree.
- **An issue isn't done just because its PR merged.** Check it against the milestone's Definition of
  Done.
- The single-view **Roadmap Project** (grouped by Milestone) is managed by the maintainer in the web UI.

## Working model: role-based agents

Four roles. Spin-up is **always maintainer-gated**: an agent may suggest that a pass looks warranted, but
never launches one unannounced.

| Role | Owns | Writes | Never |
|---|---|---|---|
| **Product Manager** | Backlog, scope, priority, milestone hygiene, `Inbox` triage | GitHub issues only | Code, branches, PRs |
| **Architect** | Design decisions with real weight: short ADRs (`docs/adr/`) | `docs/**` only | Production code |
| **Developer** | Implementation against the issue (and ADR, if any) | Code, tests, docs on its branch | Merging; skipping the hard stop |
| **Tester** | Independent verification against the issue's acceptance criteria | PR review comments | Editing source |

**Checkpoints.** Each role reports at its own meaningful checkpoint, and the pre-action report and
the post-action confirmation are **always two separate messages**:

- **Developer** implements and self-verifies fully, then **hard-stops before any git action**. It
  reports what it built, every command it ran with its real result (naming the test layer), the
  test-count delta, the "How to test" steps, and any judgment calls. It commits, pushes and opens a
  **draft** PR only after an explicit go-ahead. The follow-up "PR opened" message is just the link
  and what it closes.
- **Architect** reports the core decision and its reasoning once the ADR is drafted, before opening
  the PR. It reports immediately if the design pivots materially mid-draft.
- **Tester** reads the acceptance criteria itself rather than trusting the PR's account, runs the
  checks, validates "How to test" in a clean instance, and reports what it actually verified before
  `gh pr ready`. It flags manual-only testing of things that could reasonably be automated.
- **Product Manager** drafts originated content (new requirements text, new issue scope) and waits
  for confirmation before writing it. It summarises after routine mechanical changes, and flags
  before any bulk or hard-to-reverse tracker action.

**Scope discipline.** An ADR stays narrowly scoped to the decision. Whoever hands work to Developer
re-diffs the issue's full Outcome against the ADR and states the routine remainder explicitly.

**Tester passes are the exception, not a stage.** The default path is Developer → CI → the maintainer's
review slot; the automated tests are what catch defects. A recurring Tester finding means a missing
test or an overbuilt slice: fix that, don't add a Tester pass.
- **Request one** only for high-risk changes: a migration on live data, money maths, supersession,
  or masking of sensitive values. The maintainer may also ask for one on any PR.
- **When requested, scope it:** read the acceptance criteria and the diff, run tier 1, confirm CI is
  green, and validate the "How to test" steps that cover the risky part. No full screenshot sweeps
  and no re-running CI's slow or platform tiers locally.
- **Without a Tester pass,** the main session marks the PR ready once the local checks pass, which
  starts CI, and reports when CI is green.
A Tester pass never substitutes for the maintainer's review and merge.

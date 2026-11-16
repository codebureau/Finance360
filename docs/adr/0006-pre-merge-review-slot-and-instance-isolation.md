# 0006. Pre-merge review slot and instance isolation

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §5e)

## Context

In an earlier project there was no consistent place or command for the maintainer to try a branch,
so testing slid to after merge and deploy. Finance360 needs **direct, pre-merge testing on the maintainer's machine**, while role
agents work concurrently in their own worktrees.

## Decision

- **Instance isolation:** every running instance is configured by `FINANCE360_DATA_DIR` (its own
  SQLite file and documents folder) and `FINANCE360_PORT`. No instance shares a database or port.
  Agents' automated tests use temporary data directories and random ports.
- **One fixed review slot:** a dedicated worktree, by default a sibling of the main clone named `<repo>-review`
  (override: `FINANCE360_REVIEW_SLOT`, with `FINANCE360_REVIEW_DATA`, `FINANCE360_REVIEW_PORT` and
  `FINANCE360_REVIEW_PROJECT` for the rest), always served on **http://localhost:8360** by default,
  with its own persistent data directory. It is separate
  from the maintainer's main working copy and from all agent worktrees. Agents never use it.
- **`review <PR#>`**, a repo script with uniform invocation:
  - `gh pr checkout` into the slot;
  - dependency sync only if lockfiles changed;
  - back up the review database, then migrate;
  - build the UI and start on :8360;
  - print the PR's "How to test" section.

  Also `review --stop` and `review --reset` (back to the synthetic household).
- **Data:**
  - synthetic sample household by default;
  - **`--real`** restores the latest encrypted backup into a separate, disposable data directory,
    wiped on stop. This is allowed because the desktop is hardware the owner controls.
- **"How to test" is mandatory** in the PR template: concrete steps with expected results.
  **Tester validates them** in a clean isolated instance before `gh pr ready`.
- **Merge gate:** the PR checkbox *"Maintainer tested in review slot."*
- **Built as one of M1's first tasks**, so even the walking skeleton is reviewed this way.

## Consequences

- Testing is one command, in one place, before merge, and never disturbs agents or the maintainer's working
  copy.
- The instance configuration (data directory and port) must be honoured everywhere: app, tests,
  scripts.
- The synthetic household must be maintained as a first-class fixture, because reviews depend on
  it.

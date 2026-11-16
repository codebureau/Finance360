# Roadmap

The standing coordination surface for milestone selection. Kept in sync with native GitHub
Milestones (every scoped issue is assigned to its milestone). Product Manager and Architect update
it when a milestone starts or completes. Full milestone definitions are in
[`docs/requirements.md` §5b](../requirements.md#5b-roadmap-session-2-agreed-2026-10-16).

**Milestones are sequential, demonstrable checkpoints**: one closes when its observable outcome is
real. Every epic hangs off the backlog index, [#1](https://github.com/codebureau/Finance360/issues/1).

## Current milestone

**M3 First projection**: "Are we on track for retirement?" is roughly answerable per
Scenario, with reproducible saved runs.

- **Definition of Done:** the epic's Outcome is real, and saved projection runs record their
  inputs, Rule pack version and engine version.
- **Decomposed.** The first tranche is split into issues; B3 to B12 and A3/A4 follow, as set out in the epic.
  A1 (ADR-0015: projection engine contract) is in progress.

**In flight alongside:**
- **Orientation** (inserted 2026-11-03): someone can open Finance360 cold and know why
  they are there, what it answers, where each answer lives, what is out of date and what to provide
  next. **Complete** (2026-11-03): the Guide, the sidebar, What's due, the home and back links and the
  Ask panel ADR (ADR-0021) are in; building the panel itself is outside this milestone.
  It was inserted ahead of the rest of Tax planning, which is no longer paused.
  - **Definition of Done:** each section reachable from the sidebar has Guide coverage and the Guide
    opens with "Start here"; the sidebar is grouped by question; What's due lists stale and missing
    data on the synthetic household, for the areas in use; the Ask panel ADR is accepted.
  - **Sub-issues:** O1 the Guide and O2 the sidebar ship together; O3 What's due
    (needs design); O4 the Ask panel ADR.
- **Tax planning** (ADR-0019): the household's records for a financial year produce a Tax estimate
  that matches the lodged return to the cent, and the owner can save, compare and break-even Tax what-ifs.
  Runs beside M3, ahead of its Age Pension slices. It is no longer paused now that Orientation is complete.
  - **Definition of Done:** the local test reproduces a lodged return to the cent (it skips where
    `private/` is absent); Records, estimates, what-ifs, compare and break-even work on the synthetic
    household.
- **Tax in the plan** (ADR-0019): the tax engine supplies take-home pay to the projection,
  and Division 293 is predicted. Follows Tax planning.
  - **Definition of Done:** a projection year's take-home pay comes from the tax engine with saved
    runs still reproducible; Division 293 is predicted with its one-year lag and a stated payer; the
    regular-deduction-from-super mechanism is decided by its ADR (ADR-0028).
- **M1 Household position**: its sub-issues are done. It closes when its Definition of Done is met: a
  real household's data entered and matching its inventory, the restore exercise, and the review-slot
  confirmation.

**Earlier phases:**
- Bootstrap: done 2026-10-16.
- M0 Financial inventory: closed 2026-10-19. The private inventory has had two passes; the
  catalogue is in `docs/catalogue.md`, and the Importer backlog was ranked from it.
- UX foundation (ADR-0014): closed 2026-10-20 (U1–U4).
- Lean dev cycle (2026-10-21): tiered tests, opt-in Tester passes, thin slices.
- M2 Investment platform import (ADR-0013): closed 2026-10-24. G1–G6 were released as
  `v0.2.0`. Fixes from real exports followed in `v0.2.1`–`v0.2.4`.

## Committed sequence

| # | Milestone | Observable outcome |
|---|---|---|
| M0 | Financial inventory | Private inventory of every item in today's position, plus a seeded Category/Account type catalogue and a ranked Importer backlog in the repo |
| M1 | Household position (walking skeleton) ([#2](https://github.com/codebureau/Finance360/issues/2)) | Real Accounts entered in a deployment show the true household net position by Member and Category, in AUD, today and over time. Includes the review slot, backup and a tested restore |
| UX | UX foundation (inserted 2026-10-20; done) | URL routing, sidebar shell, list and detail pages, anchored editing, confirmed destructive actions and semantic theming. New screens follow one pattern (ADR-0014) |
| M2 | Investment platform import | The platform's exports derive an investment Account's value and history automatically, kept current by daily prices |
| M3 | First projection ([#4](https://github.com/codebureau/Finance360/issues/4)) | "Are we on track for retirement?" is roughly answerable per Scenario, with reproducible saved runs |
| O | Orientation (inserted 2026-11-03, ahead of the rest of T1; complete 2026-11-03) | Someone can open Finance360 cold and know why they are here, what it answers, where each answer lives, what is out of date and what to provide next |
| T1 | Tax planning (alongside M3) ([#11](https://github.com/codebureau/Finance360/issues/11)) | Records for a financial year produce a Tax estimate that matches a lodged return to the cent; Tax what-ifs can be saved, compared and break-even searched |
| T2 | Tax in the plan (alongside M3) | The tax engine supplies take-home pay to the projection, and Division 293 is predicted with its lag |
| M4 | Look-through analytics ([#5](https://github.com/codebureau/Finance360/issues/5)) | Exposure of investment-platform and super Accounts by asset class, region, sector and currency, with fund-of-funds resolved |
| M5 | Super import ([#7](https://github.com/codebureau/Finance360/issues/7)) | Super fund statements and exports replace manual super Valuations and option mix |

## Candidate milestones (unordered)

- Read-only MCP server (early, soon after M1)
- Goals (solve for targets)
- Knowledge & agent v1
- Mortgage & offset
- Bank accounts and transfer recognition
- Property valuation
- Overseas pensions (partial transfer, a foreign-jurisdiction Rule pack)
- Age Pension in full
- Monte Carlo engine
- Investment accounting (cost base, CGT, performance)
- Representative budget (AI)
- Point-in-time export/summary for sharing
- History views: splits over time, a CPI trend line, and history joined to projection (requirements §5a, Valuations & history)

## Milestone selection rule

1. When the current milestone closes, pick the next from the committed sequence or, after M5, from
   the candidates.
2. Write its observable outcome and Definition of Done here.
3. Create the native GitHub Milestone and assign every scoped issue to it.

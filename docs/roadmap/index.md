# Roadmap

The standing coordination surface for milestone selection. Kept in sync with native GitHub
Milestones (every scoped issue is assigned to its milestone). Product Manager and Architect update
it when a milestone starts or completes. Full milestone definitions are in
[`docs/requirements.md` §5b](../requirements.md#5b-roadmap-session-2-agreed-2026-10-16).

**Milestones are sequential, demonstrable checkpoints**: one closes when its observable outcome is
real. Every epic hangs off the backlog index, [#1](https://github.com/codebureau/Finance360/issues/1).

## Current milestone

**M4 Look-through analytics** ([#5](https://github.com/codebureau/Finance360/issues/5)): what the
household's invested money is exposed to (asset class, region, sector and exposure currency) is
visible for investment-platform and super Accounts, with fund-of-funds resolved.

- **Definition of Done:** the epic's acceptance criteria hold on the synthetic household: exposure by
  the four dimensions at Account, Category, Member and Household level; a fund-of-funds resolves to
  its underlying funds; an unallocated remainder is shown, not absorbed.
- **Sub-issues of [#5](https://github.com/codebureau/Finance360/issues/5), in order:** #82 ADR
  (Exposure profile storage and sourcing, fund-of-funds, the unallocated remainder, the glide path,
  target allocation and asset-class returns); #83 asset-class exposure for one Account (first
  screen); #84 region, sector and exposure currency; #85 fund-of-funds and #86 super options with the
  unallocated remainder; #87 the Analytics view by Category, Member and Household; #88 concentration
  and target gap; #6 the lifecycle glide path; #89 asset-class returns in the projection (last).
- **Not in M4:** AI-assisted factsheet extraction (#90), a candidate after it. Profiles are entered
  by hand in M4.

**In flight alongside:**
- **Orientation** (inserted 2026-11-03): someone can open Finance360 cold and know why
  they are there, what it answers, where each answer lives, what is out of date and what to provide
  next. **Complete** (2026-11-03): the Guide, the sidebar, What's due, the home and back links and the
  Ask panel ADR (ADR-0021) are in; building the panel itself is outside this milestone.
  - **Definition of Done:** each section reachable from the sidebar has Guide coverage and the Guide
    opens with "Start here"; the sidebar is grouped by question; What's due lists stale and missing
    data on the synthetic household, for the areas in use; the Ask panel ADR is accepted.
  - **Sub-issues:** O1 the Guide and O2 the sidebar ship together; O3 What's due
    (needs design); O4 the Ask panel ADR.

**Earlier phases:**
- Bootstrap: done 2026-10-16.
- M0 Financial inventory: closed 2026-10-19. The private inventory has had two passes; the
  catalogue is in `docs/catalogue.md`, and the Importer backlog was ranked from it.
- UX foundation (ADR-0014): closed 2026-10-20 (U1–U4).
- Lean dev cycle (2026-10-21): tiered tests, opt-in Tester passes, thin slices.
- M2 Investment platform import (ADR-0013): closed 2026-10-24. G1–G6 were released as
  `v0.2.0`. Fixes from real exports followed in `v0.2.1`–`v0.2.4`.
- M1 Household position (epic #2): closed 2026-11-17. `v0.9.0` is deployed, the restore was
  exercised from the off-site copy, and the maintainer confirmed a representative position.
- Tax planning (epic #11, ADR-0019): closed 2026-11-20. Records, Tax estimates, what-ifs, break-even
  and the Return worksheet; released as `v0.10.0`.
- Tax in the plan (ADR-0028): done, delivered under #11. Take-home pay and Division 293 are in the projection.
- M3 First projection (epic #4): closed 2026-11-22. Scenarios, saved reproducible runs, Drawdown,
  Life events, Income streams, spending phases, tax in the plan and, last, the Age Pension (ADR-0029:
  Household facts, the means test on opening balances, its working on screen). Released as `v0.11.0`.
  Its deferred items stay open on their own: survivor amounts (#26), inheritance (#35) and rent as an
  Income stream (#76).

## Committed sequence

| # | Milestone | Observable outcome |
|---|---|---|
| M0 | Financial inventory | Private inventory of every item in today's position, plus a seeded Category/Account type catalogue and a ranked Importer backlog in the repo |
| M1 | Household position (walking skeleton; closed 2026-11-17) ([#2](https://github.com/codebureau/Finance360/issues/2)) | Real Accounts entered in a deployment show the true household net position by Member and Category, in AUD, today and over time. Includes the review slot, backup and a tested restore |
| UX | UX foundation (inserted 2026-10-20; done) | URL routing, sidebar shell, list and detail pages, anchored editing, confirmed destructive actions and semantic theming. New screens follow one pattern (ADR-0014) |
| M2 | Investment platform import | The platform's exports derive an investment Account's value and history automatically, kept current by daily prices |
| M3 | First projection (closed 2026-11-22) ([#4](https://github.com/codebureau/Finance360/issues/4)) | "Are we on track for retirement?" is roughly answerable per Scenario, with reproducible saved runs |
| O | Orientation (inserted 2026-11-03; complete 2026-11-03) | Someone can open Finance360 cold and know why they are here, what it answers, where each answer lives, what is out of date and what to provide next |
| T1 | Tax planning (closed 2026-11-20) ([#11](https://github.com/codebureau/Finance360/issues/11)) | Records for a financial year produce a Tax estimate that matches a lodged return to the cent; Tax what-ifs can be saved, compared and break-even searched |
| T2 | Tax in the plan (done; delivered under #11) | The tax engine supplies take-home pay to the projection, and Division 293 is predicted with its lag |
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
- Question-first navigation: the Guide, Ask and What's due built around the questions (#72, with #24)
- Tax: provisional figures and document parsing (#56)
- Factsheet Importer: Exposure profiles proposed from fund factsheets (#90)
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

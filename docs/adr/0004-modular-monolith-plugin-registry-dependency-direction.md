# 0004. Modular monolith, plug-in registry, enforced dependency direction

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §2, §5e)

## Context

The product is a grid of **Categories × Capabilities**, extended over time by going deeper in one
area. The maintainer prefers patterns over hard-coding, and the NFRs ask for minimal moving parts. Agents,
especially, drift across boundaries unless something checks mechanically.

## Decision

- **One deployable modular monolith:**
  ```
  backend/finance360/
    core/          domain model: Household, Member, Account, Valuation, Money, Ownership, audit
    capabilities/  imports, reference, position, lookthrough, projection, goals, knowledge, ai
    behaviours/    simple_balance, holdings_based, amortising_loan, …
    rulepacks/     au/ (versioned by date), other jurisdictions later
    plugins/       importers/, providers/
    api/           FastAPI routers (thin)
    mcp/           MCP server (thin)
    jobs/          in-app scheduler
  frontend/
  ```
- **An explicit plug-in registry** for Importers, providers, Behaviours and Rule packs. Each plug-in
  declares what it is (e.g. "Importer: an investment platform, trade Stream"). In-repo plug-ins first; Python
  entry points later if open-sourcing warrants third-party plug-ins.
- **Dependency direction:** plugins → capabilities → core, never the reverse. API, MCP and jobs call
  only the service layer. **Enforced in CI by import-linter.**
- **Account types are data referencing a code Behaviour.** A new Account type that fits an existing
  Behaviour is configuration.
- **The projection engine is pure functions over immutable inputs** (Position + Scenario + Rule pack
  version → result).

## Consequences

- Going deeper in one Category adds plug-ins or Behaviours without touching the core.
- Reproducible projections and golden tests come naturally. Monte Carlo is just another engine.
- Boundary violations fail CI instead of accumulating.
- Splitting into services later stays possible, but nothing anticipates it.

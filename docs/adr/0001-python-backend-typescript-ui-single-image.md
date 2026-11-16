# 0001. Python backend, TypeScript UI, single image

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §5e)

## Context

Finance360's core is correctness-critical domain code: exact money, Importers and the projection
engine. The maintainer reviews every agent-produced PR and is most fluent in Python. An earlier project of
the maintainer's is TypeScript end to end, with tooling and agent conventions worth reusing. TypeScript
alone was considered: one language, shared types. Python alone isn't viable, because the UI needs a
JS framework anyway.

## Decision

- **Backend in Python:**
  - **FastAPI** for the API, with an auto-generated OpenAPI schema;
  - **uv** (environments and packaging), **ruff** (lint and format), **pyright** (strict),
    **pytest** + **Hypothesis**.
- **UI in TypeScript:** **React + Vite**, **shadcn/ui on Tailwind**, **TanStack Query, Table and
  Router**, **Apache ECharts**. Light and dark themes from day one, and centralised money
  formatting.
- **Typed end to end:** the TS API client is **generated from FastAPI's OpenAPI schema**. CI fails
  if the regenerated client differs from the committed one.
- **One deployable image:** a multi-stage Docker build compiles the UI to static files that FastAPI
  serves, on the same origin.
- Borrow that earlier project's tooling and conventions, not its code. Its UI library is not imported.

## Consequences

- The maintainer can review the domain core fluently. `Decimal`, Hypothesis and strong PDF/CSV libraries help
  the exact-money and Importer work.
- Two toolchains (uv + npm) in one repo: a one-off setup cost.
- Backend model changes break the UI build rather than failing at runtime.
- Serving the UI from the same origin removes build-time API URL configuration, which was a known
  footgun in that earlier project.

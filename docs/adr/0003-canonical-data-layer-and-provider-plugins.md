# 0003. Canonical data layer and provider plug-ins

**Status:** Accepted
**Date:** 2026-10-16
**Issue:** — (foundational; requirements §5a, §5e)

## Context

Data arrives from user exports (e.g. an investment platform's two CSV Streams), later PDF statements, and
reference-data providers (prices, FX, fund composition, CPI). Free data sources keep diminishing,
so **every dataset's provider is expected to change over time.** Retail exports also lack stable
IDs, and identify Instruments inconsistently (ticker, Yahoo symbol, product name only).

## Decision

- **A thin canonical (anti-corruption) layer.** Every Importer and provider maps into a small set
  of canonical records:
  - canonical transaction types;
  - `Price`, `FxRate`, `ExposureProfile`, `CpiIndex`.

  Nothing beyond this layer knows which provider supplied a value, except through **provenance**
  (source, Import or provider, fetch time).
- **Per-dataset provider precedence with fallback**, in configuration (e.g. prices: `yfinance` →
  paid provider → manual). Manual entry is always available as an override.
- **Instrument identifier crosswalk:** one canonical Instrument ID plus a table of external
  identifiers and aliases (exchange ticker, provider symbol, ISIN, product-name aliases).
- **Import mechanics, shared by user data and reference data:**
  - exact-file hashing makes re-imports no-ops;
  - Coverage (source × Account × Stream × date range) with supersession of the overlap;
  - staged diff, then commit;
  - superseded Imports are retained;
  - cross-Stream reconciliation.
- **Initial providers:** `yfinance` (prices, stored locally after fetch), RBA (FX; Frankfurter
  fallback), ABS (CPI). Fund composition comes from an AI-assisted factsheet Importer with manual
  confirmation, investigated in M4.

## Consequences

- Swapping a provider is a configuration change plus one plug-in, never a domain change.
- Distribution rows that carry only a product name resolve through aliases.
- The canonical record set must be kept small and deliberate. Adding a canonical type is an ADR
  matter.

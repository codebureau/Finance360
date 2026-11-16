# 0014. Frontend UI architecture: routing, shell, page patterns and visual language

**Status:** Accepted
**Date:** 2026-10-20
**Issue:** — (UI architecture, before M2 adds screens)

## Context

The UI is `App.tsx` state-based tabs, each tab one long stacked page. At realistic data volumes this fails:
no URLs (no deep link, back/forward or refresh-in-place); an Account edit opens an inline form at
the top, far from the click; every row carries a Close button; everything is monochrome, with no
cue for primary, destructive or warning; pages grow without bound. M2 adds Instruments, Prices and
Holdings, so this is the last cheap moment to fix the foundations.

Reviewed an earlier project of the maintainer's (read-only): `react-router-dom` v7 with a `<Routes>` tree, `NavLink`, a
`RequireAuth` route wrapper and nested detail routes; hand-written Radix wrappers (Dialog, Select,
Tabs, Toast) styled by custom colour CSS variables; a `data-density` attribute on the
root that swaps density variables (row height, padding, control height); Button
variants primary/secondary/ghost. Finance360 already has Tailwind v4 with shadcn-style tokens
(`--primary`, `--destructive`, `--success`, `cn`, `cva`), so the theming framework is already
present, only under-used.

The backend SPA fallback (`main.py`, `/{path:path}`) already returns `index.html` for any
non-file path except `health*` and `api/*`, so nested client paths work. No backend change.

## Decision

**1. Routing: TanStack Router** (MIT), code-based route tree (no file-based codegen plugin) in
`frontend/src/routes.tsx`. Chosen over react-router (that project's) because TanStack Query is already in
use, search params are typed and validated (needed for the Audit trail filter), and route loaders
can prefetch into the existing QueryClient. Browser history; no hash routing.

| Path | Screen |
|---|---|
| `/` | What's due, the home screen (ADR-0020) |
| `/position` | Position |
| `/accounts`, `/accounts/$accountId` | Account list; Account detail (Valuations, identifiers, audit, later Holdings) |
| `/instruments`, `/instruments/$instrumentId` | M2 (prices, identifiers) |
| `/household` | Household (Members, Entities) |
| `/fx-rates`, `/categories` | as named |
| `/audit` | Audit trail; filter in search params (`?entity=&id=`) |
| `/settings` | Security (TOTP), theme, density |

Unknown paths render a NotFound page inside the shell. The `NavigationContext` / `showAuditTrail`
mechanism is deleted: "view audit for X" becomes a `<Link to="/audit" search=...>`.

**Auth gate** stays outside the router: `AuthGate` wraps `<RouterProvider>`, so setup/login render
at whatever URL was requested and the user is on that URL after sign-in (no redirect plumbing).
`SIGNED_OUT_EVENT` handling is unchanged; every route inherits the gate.

**2. Shell.** Replace top tabs with a **left sidebar** (desktop, fixed) that becomes a **top bar
with a menu button opening a Sheet** below `md`. Groups: Overview (Position), Data (Accounts,
Instruments, Household, FX rates, Categories), System (Audit trail, Settings). Active item via the
router's active-link state (`aria-current="page"`). Footer status (HealthStatus, BackupStatus) moves
to the sidebar bottom (and the bottom of the mobile Sheet); AccountMenu and ThemeToggle sit at the
sidebar foot / top bar. The existing `AppShell` keeps its slot props (`nav`, `actions`, `footer`) and
`data-testid="content"`; only its layout changes, so its contracts are preserved.

**3. Page patterns** (rules in `docs/ui-conventions.md`):
- **List page** = `PageHeader` (title, one primary action) + compact table; clicking a row opens
  its detail page. No per-row buttons beyond one overflow menu where unavoidable.
- **Detail page** = header (name, status badges, primary action, overflow menu) + sections or tabs
  (tab in a search param so refresh and links preserve it). Sub-entities (Valuations, Holdings)
  live here, not on the list.
- **Create/edit** in a **Dialog** (short forms) or **Sheet** (right drawer, longer forms) opened
  from the clicked control. Never an inline form at the top of a page.
  Clicking outside never dismisses one; Cancel, the X or Esc confirm ("Discard your changes?")
  before discarding unsaved input; a Sheet may expand to full width for sheet-like entry.
- **Destructive actions** (Close, Remove, Delete) live in the detail overflow menu or a dialog
  footer, use the `destructive` variant, and always go through a confirm `AlertDialog` naming the
  affected thing. Dismissing is Cancel or the dialog X; "Close account" is a specific, confirmed
  action, never a bare "Close".
- **Position**: top-level Categories open one level, deeper collapsed; sticky totals bar; Account
  and Holding rows link to detail pages instead of expanding further.

**4. Visual language.** Adopt **shadcn/ui's token system properly** on the existing Tailwind v4
setup (no extra theming library; that project's custom CSS variables are the same idea, but Tailwind is
already here). `index.css` defines, for light and `.dark`, each token with a paired `-foreground`:
`background, foreground, card, popover, primary, secondary, muted, accent, destructive, warning,
success, info, border, input, ring`, mapped in `@theme inline`. Components use semantic tokens only,
never raw palette colours or hard-coded black/white. Button variants (cva): `default`, `secondary`,
`outline`, `ghost`, `destructive`, `link`; one `default` per page or dialog. A `<Money>` component
applies a `money-negative` token to negative and liability amounts (never colour alone: sign or
label too). `<Badge>` variants map to states: `flagged`/`stale` = warning, `closed`/`superseded` =
muted, `ok` = success, `info`. **Density**: `data-density="normal|compact"` on `<html>` swaps CSS
variables (`--row-h`, `--cell-px`, `--cell-py`, `--control-h`) read by tables and form controls
(that project's approach, kept). Default normal; the architecture ships now, the Settings toggle may come
later (persisted in `localStorage` like the theme).

**5. Dependencies** (exact-pinned, MIT; the Developer records the licence check in the PR;
`npm audit` already gates CI): `@tanstack/react-router`; `@radix-ui/react-dialog`,
`-alert-dialog`, `-dropdown-menu` (the primitives behind shadcn Dialog, Sheet, AlertDialog,
DropdownMenu), optionally `-tabs`. shadcn components are copied into `components/ui/`, not a
dependency. No TanStack Table (plain styled `<table>` until sorting or virtualisation is needed),
no toast library yet.

**6. Enforcement.** `docs/ui-conventions.md` is the contract, with a Developer self-review line and
a Tester brief line (wording in that doc). An ESLint rule (restricted syntax/class regex) bans raw
palette classes and bare `<button>` outside `components/ui/`.

## Resolved questions

The maintainer accepted all three on 2026-10-20:
1. **Sidebar grouping:** Accounts, Instruments, Household, FX rates and Categories go under one "Data" heading. Position and Audit trail stay separate.
2. **Density:** the variables ship in the foundation slice. The Settings toggle arrives with the Accounts or Position slice, once a table exists to judge it on.
3. **Instruments** are top level at `/instruments`. Prices live on the Instrument detail page, not as their own nav item.

## Consequences

- Every screen has a URL; back, refresh and deep links work; Audit presets become links.
- Pages stay bounded: short tables, depth on detail pages.
- New runtime dependencies (router, Radix dialog/menu); Radix is what that project already uses.
- Migration is presentation, not behaviour; `lib/` data hooks are untouched.
- Forecloses: in-page tab state, inline top-of-page edit forms, per-row destructive buttons, ad hoc
  colours, hash routing, file-based routes.
- Not taken from that project: react-router, its custom CSS (Tailwind is present), `RequirePermission`
  (single user), its domain screens.

## Not decided here / routine follow-on

- Migrating each screen to these patterns (grouped slices, one per Developer pass).
- Exact colour values, sidebar glyphs and badge icons (lucide-react continues; no new iconography
  system), copy.
- Density toggle UI, table sorting/filter library, toasts, keyboard shortcuts.
- Instruments/Prices screen content (ADR-0013); they only adopt these patterns.

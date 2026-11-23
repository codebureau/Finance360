# UI conventions

The contract for `frontend/`. Decisions and rationale: [ADR-0014](adr/0014-frontend-ui-architecture.md).
Every Developer pass follows this; Tester checks it.

## Understanding first, detail on demand
Someone opens Finance360 to understand their financial world, not to be shown every figure. Each
screen and section leads with a **designed view** that answers its question at a glance; the full
figures sit one step behind it.
- **Cards lead.** A section is a card: a plain-language headline (what this shows, and the one thing
  to notice), a chart or hero number, then the detail. Prefer a few focused cards over one long table.
- **A chart wherever the data has a shape.** Shares of a whole: a bar (stacked or one bar per label)
  or a donut with at most six slices. Change over time: a line or area. Comparisons: grouped bars.
  Flows (how a total is built): a waterfall. A single headline figure: a stat tile, not a chart. A
  screen that shows percentages or a time series with no chart is incomplete.
- **Tables support, behind a disclosure.** The exact figures stay one click away ("Show figures",
  a collapsible section or a drill-in to a detail route), never removed: figures stay traceable.
  Open by default only where the table *is* the task (editing, reconciling, a list to pick from).
- **Chart rules.** Colour follows the entity, never its rank or position in a list: a label keeps
  its colour across screens and filters. More labels than chart tokens fold into "Other" for the
  picture (the table keeps every label). A legend for two or more series, direct labels on the few
  that matter, hover tooltips with the exact figure, one y-axis only, and light and dark both checked.
- **Briefs name the presentation.** Every slice that shows data states its designed view (the card,
  the chart type and what it answers) and where the detail lives, before it is built.

## Routing and layout
- Every screen has a URL (TanStack Router, `frontend/src/routes.tsx`). Navigate with `<Link>`, never
  state-based tab switching. Filters, selected tab and linkable dialogs live in typed search params.
- Pages render inside `AppShell` (sidebar on desktop, Sheet menu on mobile). Footer status lives in
  the sidebar. No per-page header controls; use the page header.
- A page is `PageHeader` (title, optional description, **at most one primary action**) then content.

## Lists and detail
- Lists are compact tables. The whole row links to the detail page. No per-row buttons; at most one
  overflow menu (`DropdownMenu`) per row, only if the actions can't live on the detail page.
- Detail pages hold the entity's sub-sections (Valuations, identifiers, audit, Holdings). Prefer
  sections; use tabs (search param) only when sections are heavy. No stacks of unbounded
  expandable panels: a long section gets its own tab or list page.
- A section showing more than ~10 rows paginates or links to a full list.

## Editing
- Create/edit in a `Dialog` (short) or `Sheet` (long) opened from the clicked control. Save and
  Cancel in the footer; focus returns to the trigger. Never an inline form at the top of a page.
- Validation errors show beside the field; the dialog stays open on failure.

## Actions
- Variants: `default` (primary), `secondary`, `outline`, `ghost`, `destructive`, `link`. One
  `default` per page or dialog. Cancel is `ghost` or `outline`.
- Destructive actions (Close, Remove, Delete, Supersede) use `destructive`, sit in the detail
  overflow menu or a dialog footer, and **always** open an `AlertDialog` naming the affected thing
  and the consequence. Labels are specific ("Close account"), never a bare "Close".
- Dismissing UI is Cancel or the dialog X, never a button called Close.

## Colour and status
- Semantic tokens only (`bg-primary`, `text-destructive`, `text-muted-foreground`, `bg-warning/10`).
  No raw palette classes, hex values or hard-coded black/white. Check light and dark.
- Money renders through `<Money>`: negative and liability amounts use `money-negative` and also
  carry a sign or label (colour is never the only cue).
- Status uses `<Badge>` variants: `flagged`/`stale` (warning), `closed`/`superseded` (muted), `ok`
  (success), `info`. Same state, same badge, everywhere.
- Chart slices use the categorical tokens `--chart-1` to `--chart-6` (`chart-1`..`chart-6` in
  Tailwind), assigned to a category in a fixed order so a colour means one thing everywhere. A chart
  always has a table or legend with the exact figures beside it (colour is never the only cue).

## Density
- Row height, cell padding and control height come from `--row-h`, `--cell-px`, `--cell-py`,
  `--control-h`, switched by `data-density="normal|compact"` on `<html>`. Never hard-code them in
  tables or form controls. Check compact when touching a table.

## Position screen
- Top-level Categories open one level, deeper collapsed. Totals bar sticky. Rows link to detail
  pages instead of expanding further.

## Checklists
**Developer self-review (add to `.claude/agents/developer.md`):** "UI follows `docs/ui-conventions.md`:
routed URL, designed view first (card, chart, detail behind a disclosure), list-to-detail, edit in Dialog/Sheet, destructive confirmed and out of row clutter, one
primary action, semantic tokens only, `<Money>`/`<Badge>` used, fine in light/dark and compact."

**Tester brief:** "For UI changes, check against `docs/ui-conventions.md`: deep-link and refresh each
new route; back/forward; edit opens in context; destructive actions confirm; no raw colours;
light/dark."

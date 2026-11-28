# UI conventions

The contract for `frontend/`. Decisions and rationale: [ADR-0014](adr/0014-frontend-ui-architecture.md).
Every Developer pass follows this; Tester checks it.

## Understanding first, detail on demand
Someone opens Finance360 to understand their financial world, not to be shown every figure. Each
screen and section leads with a **designed view** that answers its question at a glance; the full
figures sit one step behind it.
Decisions and values: [ADR-0031](adr/0031-visual-layer-chart-kit-display-preferences-and-palette.md).
- **Cards lead.** A section is a view card (`ViewCard`): a title naming what it answers, a
  plain-language headline computed from the data (the one thing to notice), the chart switch where
  offered, a chart or stat tile, then "Show figures". Prefer a few focused cards over one long table.
- **A chart wherever the data has a shape.** The data shape picks the forms (first is the default):
  `share` (parts of one whole): donut, stacked bar, bars. `share-over-time`: stacked area, lines.
  `series` (independent values over time, such as Scenarios): lines. `comparison`: grouped bars.
  `build-up` (how a total is reached): waterfall. `figure`: a stat tile, not a chart. A screen that
  shows percentages or a time series with no chart is incomplete.
- **Never a form that misreads the data.** No donut for a time series, no stacking of alternatives
  (Scenarios), no donut or stacked bar when a part is negative (bars only). Liabilities are never a
  slice of assets: they go in a stat tile.
- **Tables support, behind a disclosure.** The exact figures stay one click away, never removed:
  figures stay traceable. **"Show figures" is the standard disclosure label** (`FiguresDisclosure`);
  a drill-in names its destination ("Show items"). Open by default only where the table *is* the task
  (editing, reconciling, a list to pick from).
- **Chart rules.** Build charts from the kit (`lib/charts/<shape>.ts` pure option builders on the lazy
  `components/echart.tsx`); add a form only with the first screen that needs it. At most six parts in
  any `share` form, the smallest folded into "Other" for the picture (the table keeps every label);
  switching form never changes what is folded. Explicit lines (No profile, Not classified,
  Unallocated, Unreconciled) always stay. A legend or axis label names every mark, tooltips give the
  exact figure, one y-axis only, no animation, light and dark both checked.
- **The chart switch.** A shape with several forms offers a segmented control (`SegmentedControl`,
  lucide icons, each with a name and tooltip) at the right of the card's title row; a view may narrow
  the forms or offer none, saying why in its brief. The choice is a Display preference: stored in the
  instance per Login for this view (`<view-key>`), via `useDisplayPreference`. The default for the
  shape (`default.<shape>`) changes only through an explicit "Use as my default". Cards versus list
  (`collection`) works the same way.
- **Briefs name the presentation.** Every slice that shows data states its designed view (the card,
  the data shape, the forms offered, its view key and what it answers) and where the detail lives,
  before it is built.

## Cards and stat tiles
- **View card:** neutral surface; colour lives in its chart. Title, headline, switch, body, "Show
  figures".
- **Stat tile** (`StatTile`): a hero value (Money or a count; proportional figures, system sans), a
  label, up to two supporting values, an optional delta (arrow and sign; neutral colour unless the
  direction itself means good or bad). With a severity it adds a status-toned left accent bar and
  icon, a word badge, a one-line explanation and a drill-in link. Severity never comes from a numeric
  score.
- **Question card** (`QuestionCards`, Home, view key `home.questions`): a stat tile with severity.
  The Question in plain words is its label; the hero is the count of inputs needing attention; the
  Readiness word wears its status tone and icon (Ready `success`, Partly ready `warning`, Not ready
  `destructive`, Not started `info`, Could not check muted); one line of reason; "Take the next step"
  (the first reason's fix link) and "Show items". One headline sentence sits over the group, computed
  from the states. No gauge, no score and no percentage. Cards stack to one column at 390px.
- **Question page** (`/questions/$questionKey`, view key `question.page`, shape `figure` with
  severity): leads with the same tile as the Home card (status icon, word and tone; count; one line of
  reason; the next step), plus "See the answer" (the screen that answers it) and "Ask about this";
  then "What this answer needs": the inputs in the order to provide them, each with its Readiness
  badge (icon and word), what needs doing with a "Fix it here" link, and small gaps muted beneath.
  An input the Effort level does not ask for is shown, labelled, and weighs on nothing. No chart: a
  state, a count and a list are the data. "Your questions" in the sidebar lists the Questions from
  the registry, its heading linking the Guide.
- **Return bar** (shell, every screen): a fix link carries `?from=due` or `?from=q.<key>`; the shell
  keeps the marker per browser tab (`sessionStorage`) and shows "Back to What's due", or "Back to
  your next step" with the Question and its next step, until the owner goes back, dismisses it or
  follows another return link. A screen declares nothing.
- **Notices** (ADR-0033): a write that moves the picture answers at once with a toast (a polite live
  region, always in the page): the headline, what it unlocked in words, the change as an arrow and a
  sign (never colour alone) and a "Take a look" link. The same Notice is kept as a row of the
  Recent changes list: a `collection` in list form only (no Cards and List switch), the last ten
  undismissed on Home, every one on `/recent-changes` with dismissed rows muted. Dismiss is an `x` on
  the row; a Notice is deleted after 30 days and nothing nags. A toast is a transient surface, not a
  chart-kit shape.
- **Cards or list** for a collection: both show the same items, wording and actions; cards stack to
  one column at 390px.

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
- **Two colour systems, never mixed up.** *Entity* colour (which thing): the categorical tokens
  `--chart-1` to `--chart-6`, which contain no red, green or amber. *Status* colour (what state):
  `success`, `warning`, `destructive`, `info`, muted, always with an icon and a word. A mark wears a
  status tone only when it means a state (No profile, Unreconciled beyond tolerance); an ordinary
  series never does.
- **Colour follows the entity, never its rank.** Take a mark's tone from `toneOf(family, key)` in
  `lib/entity-colour.ts`: fixed tables for Category and asset class (Cash is teal, never red), stable
  order for Members, Scenarios and larger Dimensions. A label keeps its colour across screens and
  filters. One chart uses one family. Never generate a seventh hue.
- **A colour may repeat only where every mark is named on it.** Bars may reuse a token (each bar is
  named on its axis). In a donut, stacked bar, stacked area or lines, no token appears twice: a
  smaller part that would repeat a larger part's token is drawn muted with its name beside it.
- "Other" and residual lines (Not classified, Unallocated) are muted, never a chart token.
- **Sidebar group colour** is a wayfinding cue only: the group's token tints its item icons and the
  icon chip beside the page title (Reference and system stays muted). Never a fill or a chart colour.
- **Restraint.** Saturated colour is for chart marks and small marks (icons, chips, swatches, a stat
  tile's accent bar). Card surfaces, backgrounds and body text stay neutral. A chart always has a
  legend or labels and "Show figures" beside it (colour is never the only cue).

## Density
- Row height, cell padding and control height come from `--row-h`, `--cell-px`, `--cell-py`,
  `--control-h`, switched by `data-density="normal|compact"` on `<html>`. Never hard-code them in
  tables or form controls. Check compact when touching a table.

## Position screen
- Leads with the net stat tile, then "What it is made of" (a `share` of assets by Category or by
  Member), then "Over time". The tree and table sit behind "Show figures", open when Layout is Table.
  Top-level Categories open one level, deeper collapsed; rows link to detail pages instead of
  expanding further. As at, Tag, View and Layout live in one collapsed "Change view" row.

## Checklists
**Developer self-review (add to `.claude/agents/developer.md`):** "UI follows `docs/ui-conventions.md`:
routed URL, designed view first (view card, a kit chart of the right shape with entity colour, "Show figures"), list-to-detail, edit in Dialog/Sheet, destructive confirmed and out of row clutter, one
primary action, semantic tokens only, `<Money>`/`<Badge>` used, fine in light/dark and compact."

**Tester brief:** "For UI changes, check against `docs/ui-conventions.md`: deep-link and refresh each
new route; back/forward; edit opens in context; destructive actions confirm; no raw colours;
light/dark."

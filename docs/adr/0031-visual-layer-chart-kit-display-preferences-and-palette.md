# 0031. Visual layer: chart kit, switchable chart forms, Display preferences, palette and colour

**Status:** Proposed
**Date:** 2026-11-23
**Issue:** #96 (epic #94; absorbs #46)

## Context

The app is a sea of numbers: one chart (Position history) and tables for every share and time
series. `docs/ui-conventions.md` ("Understanding first, detail on demand", from #84) now requires a
**Designed view** first: a card with a plain-language headline and a chart or hero number, with the
figures one disclosure away. Every Visual layer slice (#97 Position, #98 Projection, #99 Income
reconciliation, #100 Compare, #101 What's due cards, #102 notices) inherits the answers to: which
chart forms exist, whether the owner can switch form, where that choice lives, and which colours.

The maintainer (2026-11-23): for percentages a donut, pie or stacked bar reads better than one bar
per label; the form must be switchable, remembered per view and as a default; more colour, applied
consistently (cards, status, Category), with 1Password Watchtower's stat cards as the reference;
chart themes can come later.

What exists: a lazy ECharts host (`components/echart.tsx`, line and bar registered), pure option
builders (`lib/history-chart.ts`, `lib/exposure-chart.ts`), token resolution for the canvas
(`lib/chart-palette.ts`), `FiguresDisclosure`, and six `--chart-N` tokens added under ADR-0019's
reconciliation slice. Theme and density are per browser (`localStorage`, ADR-0014); the Effort
level is an audited Household column (ADR-0020). One Login per instance (ADR-0010).

The current `--chart-N` set fails the dataviz validator: light chart-5 chroma below the floor, the
amber and green pair under 6 ΔE for colour-vision deficiency in both modes, the dark set above the
dark lightness band, and chart-6 is a red that Cash wears, beside red status and `money-negative`.

This ADR **extends ADR-0014 section 4** (visual language). ADR-0014 stays Accepted; its rule "chart
slices use `--chart-1..6` in a fixed order" is replaced by sections 4 and 5 below, and the token
values added under ADR-0019 are replaced. It does not decide the Question registry, readiness or a
gauge (#103, R0); those will use the stat tile and status tones decided here.

## Decision

### 1. Chart kit: the form follows the data shape

Every chart is named by its **data shape**, and the shape fixes the forms it may take. The first
form listed is the kit default.

| Data shape (`shape`) | What it is | Forms | Switch |
|---|---|---|---|
| `share` | Parts of one whole, every part ≥ 0 (Position by Category, an Exposure Dimension, an income package) | `donut`, `stacked-bar`, `bars` | Yes |
| `share-over-time` | Parts of a total per date or year, every part ≥ 0 (balances by Category over the projection) | `stacked-area`, `lines` | Yes |
| `series` | Independent values over time (Assets, Liabilities and Net; one line per Scenario) | `lines` | No |
| `comparison` | One or two measures across a few items (a result per Scenario) | `grouped-bars` | No |
| `build-up` | Signed steps from a start to a total (income to tax payable) | `waterfall` | No |
| `figure` | One headline value, with at most two beside it | stat tile | No |
| `ratio` | One value against its limit (a cap used) | meter | No |
| `collection` | A set of items (Due items) | `cards`, `list` | Yes |

Rules:
- **A shape never offers a form that misreads it.** A time series never offers a donut; `series`
  never stacks (Scenarios are alternatives, not parts). A `share` with any negative part, or a
  whole of zero, draws `bars` and hides the switch. Liabilities are never a part of assets: they sit
  in a stat tile.
- **Switching form never changes the content.** One fold constant serves every `share` form: at
  most six parts; beyond that the smallest fold into one "Other" for the picture (the table keeps
  every part). Explicit lines (No profile, Not classified, Unallocated, Unreconciled) always stay
  as their own parts.
- **The figures are always one step away.** Every chart has a legend or axis labels naming each
  mark, a tooltip with the exact figure, and "Show figures" (the table). The table is the text
  equivalent; it is not a chart form and is not in the switch.
- **One y-axis, no animation, no texture fills, no 3-D, no pie with exploded slices.**
- **Money rule** (unchanged): amounts shown are the API's exact strings through `formatMoney`;
  shares are summed in integer parts per million; `Number()` only gives ECharts a geometry.

**Module shape** (one shared kit on the existing lazy host; no chart framework):
- `components/echart.tsx` stays the only ECharts host. It registers a chart type (Pie, later
  Custom for the waterfall) in the slice that first needs it.
- `lib/charts/<shape>.ts`: pure option builders, one file per data shape, each
  `(model, palette) => option` and unit-tested without a canvas. `share.ts` takes one row model
  (key, name, ppm, percent text, amount text, tone) and builds all three `share` forms from it; the
  existing `BarRow`, `foldRows` and `buildBarOption` move into it.
- `lib/entity-colour.ts`: the colour tables and `toneOf(family, key)` (section 5). It replaces
  `labelTone` and `ASSET_CLASS_TOKEN`.
- `lib/display-preferences.ts`: `useDisplayPreference(viewKey, shape, allowed?)` (section 3).
- `components/share-chart.tsx` (a `share` in any form, with its switch), `components/view-card.tsx`,
  `components/stat-tile.tsx` (section 6), and `components/ui/segmented-control.tsx` (the switch).
  `ExposureBars` becomes a use of `ShareChart`.

**The kit grows with the first screen that needs each form** (AGENTS.md rule 5). V1 (#97) builds
`share` (all three forms), the stat tile, the view card, the segmented control, entity colour and
the preference hook. V2 (#98) adds `share-over-time`; V4 (#100) `series` with end labels and
`comparison`; V5 (tax, not yet filed) `build-up`; #101 `collection`. `ratio` (meter) and a stat-tile
sparkline wait for a slice that needs them. No form is built ahead of its screen.

### 2. The switch

- A **segmented control** (`components/ui/segmented-control.tsx`): a labelled radio group
  ("Chart type", "Show as") of icon buttons from lucide-react (pie, stacked bar, bar, area, line;
  grid and list for `collection`), each with an accessible name and a tooltip of the same word.
  Native radio semantics: arrow keys move, the choice applies at once, focus stays on the control.
  No new dependency.
- It sits at the right of the view card's title row, and only where a shape offers two or more
  forms. A view may narrow the forms it offers (keeping the kit order) or offer none, when one form
  is the point; its brief says why.
- The chart's accessible label (`role="img"`) carries the headline and the form; the legend and
  "Show figures" are unchanged by a switch.

### 3. Display preferences: stored in the instance, per Login

A **Display preference** remembers a presentation choice. Resolution for a view, first match wins:

1. the stored choice for this view (`<view-key>`, for example `position.composition`), if the view
   offers it;
2. the stored default for the shape (`default.<shape>`, for example `default.share`), if the view
   offers it;
3. the view's first offered form (the kit default unless the view narrowed it).

**Choosing a form on a switch stores the view's choice only.** The shape's default changes only
through an explicit **"Use as my default"** action beside the switch (shown when the view's form
differs from the stored default), which stores `default.<shape>`. So a default never changes as a
side effect of browsing, and a view once chosen keeps its own. Card versus list (#101) is the same
mechanism with shape `collection`. (Maintainer's choice, 2026-11-23: explicit over last-choice-wins,
for predictability.)

**Where: the instance, not the browser.** One Login per instance (ADR-0010) means the Login is the
user; storing per Login makes the choice follow across devices, survive a cleared browser and be
in the Backup. Per browser would need no migration but forgets per device, which defeats "the app
remembers". Theme and density stay per browser (ADR-0014): they must apply before first paint and
legitimately differ by device.

**Storage**, at decision level:
- Table `display_preference`: `login_id` (FK `login.id`), `key` (≤ 64 chars), `value` (≤ 24
  chars), `updated_at`; primary key (`login_id`, `key`). One additive migration; no data is moved;
  downgrade drops the table, so a rollback needs no restore.
- **Not audited and not a Household fact.** It is presentation: it changes no figure, and nothing
  in the service layer reads it to compute. It is not exposed over MCP.
- **Nothing financial can be stored.** The service validates the key shape
  (`^[a-z0-9-]+(\.[a-z0-9-]+)+$`) and the value (`^[a-z0-9-]{1,24}$`) and caps rows per Login
  (200); free text, amounts and names cannot fit. The frontend owns the meaning of keys and values,
  and ignores an unknown or no-longer-offered value (falling to the next layer).
- API: `GET /api/preferences` (the Login's map) and `PUT /api/preferences` (a partial map; a null
  value deletes a key), through a small service package (`capabilities/preferences/`) like any other
  write, with the usual CSRF check. The frontend reads the map once with the shell (TanStack Query)
  and updates it optimistically. A chart waits for the map as it waits for its data; if the read
  fails, the view uses layer 3 and a switch still works for that page view.

### 4. Palette: six categorical tokens, separate from status

**Categorical** (identity: which Category, asset class, Member, Scenario). No red, no green and no
amber, so a data colour never impersonates a status. Values replace `--chart-1..6` in `index.css`;
light is validated on `--card` `#ffffff`, dark on `--card` `#171717`.

| Token | Hue | Light | Dark |
|---|---|---|---|
| `--chart-1` | blue | `oklch(0.6 0.19 255)` `#127eee` | `oklch(0.51 0.16 255)` `#0f64bf` |
| `--chart-2` | terracotta | `oklch(0.55 0.15 45)` `#b64e10` | `oklch(0.54 0.14 45)` `#ae4e1a` |
| `--chart-3` | teal | `oklch(0.64 0.11 190)` `#04a19b` | `oklch(0.67 0.11 190)` `#1faaa4` |
| `--chart-4` | violet | `oklch(0.48 0.22 300)` `#7429c3` | `oklch(0.65 0.18 300)` `#a170eb` |
| `--chart-5` | magenta | `oklch(0.54 0.21 350)` `#bf1c7f` | `oklch(0.56 0.2 350)` `#c32e85` |
| `--chart-6` | gold | `oklch(0.66 0.13 90)` `#b18e15` | `oklch(0.66 0.13 90)` `#b18e15` |

Validator (`dataviz/scripts/validate_palette.js`, both pairlists, against the card surfaces): every
check passes in both modes, **including all pairs**, which matters because colour follows the
entity, so any two tokens can end up side by side.

| Check | Light, adjacent | Light, all pairs | Dark, adjacent | Dark, all pairs |
|---|---|---|---|---|
| Lightness band | PASS (0.43–0.77) | PASS | PASS (0.48–0.67) | PASS |
| Chroma floor (≥ 0.10) | PASS | PASS | PASS | PASS |
| CVD separation (≥ 8) | PASS 11.7 magenta↔violet (protan) | PASS 8.8 magenta↔teal (deutan) | PASS 13.2 violet↔teal (deutan) | PASS 9.7 magenta↔blue (protan) |
| Normal-vision floor (≥ 15) | PASS 19.2 | PASS 15.5 gold↔terracotta | PASS 18.5 | PASS 16.1 gold↔terracotta |
| Contrast vs surface (≥ 3:1) | PASS (lowest 3.11 gold) | PASS | PASS (lowest 3.07 blue) | PASS |

**Status** (state: good, warning, critical, plus info and neutral) keeps the existing tokens:
`success`, `warning`, `destructive` (critical), `info`, and muted. No new status token; there is no
"serious" step. A status colour always comes with an icon and a word. The nearest categorical to a
status is terracotta (about 6 to 10 ΔE from `warning` and `destructive` in light), so the
collision rule holds: a mark wears a status tone only when it *means* a state; a chart never mixes
the two except for an explicit line that needs attention (No profile; Unreconciled beyond
tolerance), which takes the warning tone *and* its name with a warning icon in the legend.

**Other and residuals** ("Other", Not classified, Unallocated) take the muted tone
(`muted-foreground` at reduced opacity), never a chart token. `money-negative` stays a text colour
for amounts. The history chart's Assets, Liabilities and Net keep their sign-bearing treatment.

**More labels than tokens.** Never generate a seventh hue, never colour by rank.
- A family with at most six keys has a **fixed table** by key (section 5), so nothing repeats.
- A family with more keys (region, sector, exposure currency, user-added Categories) takes its
  token by the key's stable order (vocabulary seed order or creation order) modulo six.
- In **bars**, every bar is named on its axis, so a token may repeat.
- In a **legend-identified form** (donut, stacked bar, stacked area, lines), no token appears twice:
  parts are drawn largest first, and a part whose token a larger drawn part already wears is drawn
  muted with its name printed beside it (it is not folded away, and it never takes another
  entity's colour). Beyond six parts the rest fold into "Other" as in section 1.

### 5. Colour applied consistently: entity, status, and restraint

Three uses of colour, one rule each. Semantic tokens only (`text-chart-3`, `bg-warning/10`), never
raw palette classes.

- **Entity colour** (`lib/entity-colour.ts`): a label keeps one colour on every screen and under
  every filter. Tables by key:
  - Category: `investments` 1, `property` 2, `cash` 3, `superannuation` 4, `overseas_pensions` 5,
    `vehicles` 6. A liability Category is never a chart part.
  - Asset class: `australian_shares` 1, `property` 2, `cash` 3, `international_shares` 4,
    `fixed_income` 5, `infrastructure` 6 (Property and Cash match their Categories).
  - Members by Member order; Scenarios by creation order; other Dimensions by seed order (all modulo
    six, section 4). A slice with a fixed set of parts (#99's package parts) adds its table to this
    module.
  A chart uses one family. Families share the six tokens, so the legend always names the colour.
- **Status colour**: badges, Due item importance (important: warning; optional: muted), readiness
  (#103), stat tile severity. Always with an icon and a word.
- **Sidebar group colour** (absorbs #46's "colour that identifies an area"): each sidebar group has
  a fixed token, used only for its items' icons and a small icon chip beside the page title: Where
  we stand 1, Where we're heading 4, This tax year 6, Keep it current 3, Your documents 2;
  Reference and system stays muted. It is a wayfinding cue, never a fill or a chart colour.
- **Restraint**: saturated colour is for chart marks and small marks (icons, chips, swatches, a
  stat tile's accent bar). Card surfaces, page backgrounds and body text stay neutral; no coloured
  large blocks. Light and dark are both checked; colour is never the only cue.

No new iconography system: lucide-react icons, as ADR-0014. The design-language additions are the
view card, stat tile, segmented control and group chip, documented in `docs/ui-conventions.md`.

### 6. Cards and stat tiles

- **View card** (the standard Designed view): a title naming what it answers; a headline sentence
  computed from the data ("Property is the largest share; Cash is a tenth"); the switch, where
  offered; the chart or a stat tile; then "Show figures" (the standard disclosure label, closed by
  default unless the table is the task). Neutral surface; colour lives in the chart.
- **Stat tile** (Watchtower-style, one component for two uses): a hero value (a Money or a count,
  proportional figures, system sans), a short label, up to two supporting values, an optional delta
  (arrow and sign, neutral colour unless the direction itself means good or bad), and optionally a
  severity: a status tone on a left accent bar and icon, a word badge, a one-line explanation, and a
  drill-in link that names its destination ("Show items", "Show figures"). Without severity it is
  neutral (Net position). Severity never comes from a numeric score.
- **Cards or list** for a collection (#101): the segmented control with Cards and List, stored as a
  Display preference (`whats-due.items`, `default.collection`); both views show the same items,
  wording and actions. Cards stack to one column at 390px.

## Consequences

- Every slice inherits forms, colours and persistence; briefs name a view key, a shape and its
  offered forms instead of re-deciding them.
- One additive table and two endpoints. Display preferences are in the Backup and follow the Login
  across devices; they are invisible to the Audit trail by design.
- Recolouring: Cash moves from red to teal, asset classes are re-tabled, and the Exposure screens
  change colour with V1. A colour can mean different things in different families (blue is
  Investments in Position and Australian shares in Exposure); the legend always says which.
- The donut is the `share` default because the owner reads it best; the switch covers the cases
  where a stacked bar or ranked bars compare close values better.
- Forecloses: colour by rank, generated hues past six, red, green or amber as data colours, status
  tones on ordinary series, dual axes, a chart form a shape does not offer, per-browser chart
  preferences, and preferences that hold text or figures.

**Not built (now or by this design):** chart themes or alternative palettes; a chart designer;
per-chart or per-label colour editing; a settings matrix of chart choices; chart export; texture
fills; animation; moving theme or density into the instance; preferences per Member.

## Not decided here / routine follow-on

- Building the kit: V1 (#97) builds what section 1 lists for V1, with its screen; each later form
  arrives with its slice.
- Updating `index.css` tokens and the hex fallbacks in `lib/chart-palette.ts` to section 4 (V1).
- Moving the Exposure screens onto `ShareChart` with the switch, and the asset-class re-table (V1,
  or the next slice touching Exposure if V1 outgrows one pass).
- Sidebar group icon tint and the page-header chip (a named passenger on V1, #97).
- A Settings control to reset chart choices (the `PUT` with nulls supports it), when asked for.
- The Question registry, readiness rules and any gauge (#103); the notices store (#102).
- Guide updates per screen, as each slice ships.

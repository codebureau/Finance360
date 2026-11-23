import type { CategoricalPalette } from "@/lib/chart-palette";
import { TOKEN_COUNT, type Tone } from "@/lib/entity-colour";
import { ppmToPercent } from "@/lib/exposure";
import { escapeHtml } from "@/lib/history-chart";

/**
 * The `share` chart shape (ADR-0031 section 1): parts of one whole in three forms, built from one
 * row model so a switch never changes the content. Pure, so it is unit-tested without a canvas.
 * Money rule: amounts shown are the API's exact strings through `formatMoney` (done by the caller
 * into `amountText`); parts are integer parts per million. `Number()` only gives ECharts geometry.
 */

export type ShareForm = "donut" | "stacked-bar" | "bars";
/** Kit order: the first is the default. */
export const SHARE_FORMS: ShareForm[] = ["donut", "stacked-bar", "bars"];

/** Most parts before the smallest fold into one "Other" (for the picture only). */
export const MAX_PARTS = 6;

export type ShareRow = {
  key: string;
  name: string;
  /** Parts per million of the whole (integer). */
  ppm: number;
  /** Shown beside the mark and in the tooltip. */
  percentText: string;
  /** Exact amount text, where the source has one (look-through lines do; profile weights do not). */
  amountText: string | null;
  tone: Tone;
  /** On a folded "Other": the parts it holds, so the legend still names every one. */
  parts?: ShareRow[];
};

/**
 * The forms a set of parts may take. A part below zero, or a whole of zero, cannot be a slice or a
 * stacked segment: only `bars` reads it, and the switch is hidden (one form).
 */
export function formsFor(rows: ShareRow[]): ShareForm[] {
  const whole = rows.reduce((sum, r) => sum + r.ppm, 0);
  return rows.some((r) => r.ppm < 0) || whole <= 0 ? ["bars"] : SHARE_FORMS;
}

/**
 * Largest first, at most `max` parts including the explicit lines (No profile, Not classified,
 * Unallocated, which always stay). Labels without a colour of their own ("other", alternatives)
 * and the smallest coloured labels beyond the room fold into one muted "Other".
 */
export function foldRows(labelRows: ShareRow[], explicitRows: ShareRow[], max = MAX_PARTS): ShareRow[] {
  const sorted = [...labelRows].sort((a, b) => b.ppm - a.ppm);
  const coloured = sorted.filter((r) => typeof r.tone === "object");
  const plain = sorted.filter((r) => typeof r.tone !== "object");
  const room = Math.max(max - explicitRows.length, 1);
  let kept = coloured;
  let folded = plain;
  if (coloured.length + (plain.length > 0 ? 1 : 0) > room) {
    const keep = Math.max(room - 1, 0);
    kept = coloured.slice(0, keep);
    folded = [...coloured.slice(keep), ...plain];
  }
  const out = [...kept].sort((a, b) => b.ppm - a.ppm);
  if (folded.length === 1) out.push(folded[0]);
  else if (folded.length > 1) {
    const ppm = folded.reduce((sum, r) => sum + r.ppm, 0);
    out.push({
      key: "other",
      name: "Other",
      ppm,
      percentText: `${ppmToPercent(ppm)}%`,
      amountText: null,
      tone: "muted",
      parts: folded,
    });
  }
  return [...out, ...explicitRows];
}

/**
 * The parts as a legend-identified form draws them (donut, stacked bar): no token appears twice.
 * Largest first, a part whose token a larger drawn part already wears is drawn muted (its name is
 * in the legend); it is never folded away and never takes another entity's colour.
 */
export function legendRows(rows: ShareRow[]): ShareRow[] {
  const sorted = [...rows].sort((a, b) => b.ppm - a.ppm);
  const used = new Set<number>();
  const muted = new Set<ShareRow>();
  for (const r of sorted) {
    if (typeof r.tone !== "object") continue;
    const token = r.tone.token % TOKEN_COUNT;
    if (used.has(token)) muted.add(r);
    else used.add(token);
  }
  // Keep the caller's order (explicit lines last); only the tone changes.
  return rows.map((r) => (muted.has(r) ? { ...r, tone: "muted" as const } : r));
}

/** The rows a form draws: legend forms de-duplicate tokens, bars name every bar on its axis. */
export function drawnRows(form: ShareForm, rows: ShareRow[]): ShareRow[] {
  return form === "bars" ? rows : legendRows(rows);
}

export function colourOf(tone: Tone, pal: CategoricalPalette): { color: string; opacity: number } {
  if (tone === "warning") return { color: pal.warning, opacity: 0.85 };
  if (tone === "muted") return { color: pal.muted, opacity: 0.55 };
  return { color: pal.lines[tone.token % pal.lines.length], opacity: 1 };
}

const tooltipHtml = (row: ShareRow) =>
  [
    `<strong>${escapeHtml(row.name)}</strong>`,
    escapeHtml(row.percentText),
    ...(row.amountText ? [escapeHtml(row.amountText)] : []),
  ].join("<br/>");

function tooltip(rows: ShareRow[], pal: CategoricalPalette, pick: (p: unknown) => number) {
  return {
    trigger: "item",
    confine: true,
    backgroundColor: pal.card,
    borderColor: pal.grid,
    textStyle: { color: pal.text },
    formatter: (params: unknown) => tooltipHtml(rows[pick(params)]),
  };
}

const base = (pal: CategoricalPalette) => ({ animation: false, textStyle: { color: pal.text } });

/** Pixel height of a bar chart of `rows` bars. */
export const barChartHeight = (rows: number) => Math.max(rows, 1) * 30 + 8;

/** One horizontal bar per row, in the order given (first at the top), directly labelled. */
export function buildBarOption(rows: ShareRow[], pal: CategoricalPalette) {
  const percents = rows.map((r) => Number(ppmToPercent(r.ppm)));
  return {
    ...base(pal),
    grid: { left: 4, right: 52, top: 2, bottom: 2, containLabel: true },
    tooltip: tooltip(rows, pal, (p) => (p as { dataIndex: number }).dataIndex),
    // 0 to 100 percent; a part below zero (an overdrawn Account) extends the axis to the left.
    xAxis: { type: "value", min: Math.min(0, ...percents), max: 100, show: false },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map((r) => r.name),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: pal.text, width: 120, overflow: "truncate", interval: 0 },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        data: rows.map((r, i) => ({ value: percents[i], itemStyle: colourOf(r.tone, pal) })),
        label: {
          show: true,
          position: "right",
          color: pal.text,
          formatter: (p: { dataIndex: number }) => rows[p.dataIndex].percentText,
        },
      },
    ],
  };
}

/** A ring, one slice per part, no labels on the ring (the legend beside it names each part). */
export function buildDonutOption(rows: ShareRow[], pal: CategoricalPalette) {
  return {
    ...base(pal),
    tooltip: tooltip(rows, pal, (p) => (p as { dataIndex: number }).dataIndex),
    series: [
      {
        type: "pie",
        radius: ["58%", "86%"],
        center: ["50%", "50%"],
        startAngle: 90,
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: false },
        itemStyle: { borderColor: pal.card, borderWidth: 2 },
        data: rows.map((r) => ({ name: r.name, value: r.ppm, itemStyle: colourOf(r.tone, pal) })),
      },
    ],
  };
}

/** One horizontal bar split into one segment per part, in the order given. */
export function buildStackedBarOption(rows: ShareRow[], pal: CategoricalPalette) {
  return {
    ...base(pal),
    grid: { left: 0, right: 0, top: 0, bottom: 0 },
    tooltip: tooltip(rows, pal, (p) => (p as { seriesIndex: number }).seriesIndex),
    xAxis: { type: "value", min: 0, max: rows.reduce((sum, r) => sum + r.ppm, 0), show: false },
    yAxis: { type: "category", data: [""], show: false },
    series: rows.map((r) => ({
      type: "bar",
      name: r.name,
      stack: "share",
      barWidth: 32,
      data: [r.ppm],
      itemStyle: { ...colourOf(r.tone, pal), borderColor: pal.card, borderWidth: 1 },
    })),
  };
}

export function buildShareOption(form: ShareForm, rows: ShareRow[], pal: CategoricalPalette) {
  const drawn = drawnRows(form, rows);
  if (form === "donut") return buildDonutOption(drawn, pal);
  if (form === "stacked-bar") return buildStackedBarOption(drawn, pal);
  return buildBarOption(drawn, pal);
}

/** Pixel height of the chart in a form. */
export function shareChartHeight(form: ShareForm, rowCount: number): number {
  if (form === "donut") return 220;
  if (form === "stacked-bar") return 48;
  return barChartHeight(rowCount);
}

/** Accessible name of the chart: the headline and the form, pointing at the legend and figures. */
export function shareChartLabel(form: ShareForm, headline: string): string {
  const name = { donut: "Donut chart", "stacked-bar": "Stacked bar chart", bars: "Bar chart" }[form];
  return `${name}. ${headline} The legend beside it and the figures under Show figures have the same values.`;
}

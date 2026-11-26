import type { CategoricalPalette } from "@/lib/chart-palette";
import { colourOf } from "@/lib/charts/share";
import type { Tone } from "@/lib/entity-colour";
import { axisLabel, escapeHtml } from "@/lib/history-chart";
import { formatMoney } from "@/lib/money";

/**
 * The `series` chart shape (ADR-0031 section 1): independent values over time, drawn as overlaid
 * lines. One form, no switch, and never stacked (the lines are alternatives or separate measures,
 * not parts of a whole). Pure, so it is unit-tested without a canvas. Money rule: every figure
 * shown is the API's exact string through `formatMoney`; `Number()` only gives ECharts a
 * coordinate to plot.
 */

export type SeriesLine = {
  key: string;
  name: string;
  /** Colour follows the entity (`lib/entity-colour.ts`), never the line's rank. */
  tone: Tone;
  /** One exact amount per label; null where the line has no value (it simply stops or has a gap). */
  values: (string | null)[];
  /** The answer line (Net): drawn heavier than the rest. */
  bold?: boolean;
  /** A secondary encoding beside colour, where the data gives one (Liabilities). */
  dash?: "solid" | "dashed" | "dotted";
  /** Per label: the point is incomplete (some of what it adds up is missing). Drawn hollow. */
  incomplete?: boolean[];
};

export type SeriesModel = {
  labels: string[];
  lines: SeriesLine[];
  /** Per label: extra tooltip lines, such as why a point is incomplete. */
  notes?: string[][];
};

/** Direct labels need room to their right; the grid keeps it whatever the width. */
export const END_LABEL_SPACE = 104;
const LINE_WIDTH = 2;
const BOLD_WIDTH = 3.5;
/** Past this many points the small solid markers are dropped (only incomplete points stay). */
const MARKER_LIMIT = 36;

/** The index of the last value a line has, or -1. */
export function lastIndex(line: SeriesLine): number {
  for (let i = line.values.length - 1; i >= 0; i--) if (line.values[i] !== null) return i;
  return -1;
}

/**
 * Longer names are cut for the label alone, so every label stays two lines and labels can stack
 * without overprinting; the legend and the tooltip carry the whole name.
 */
const NAME_LIMIT = 14;
const shortName = (name: string) =>
  name.length > NAME_LIMIT ? `${name.slice(0, NAME_LIMIT - 1).trimEnd()}…` : name;

/** The direct label at a line's end: its name, then its last value, exact. */
export function endLabelText(line: SeriesLine): string {
  const i = lastIndex(line);
  return i < 0 ? shortName(line.name) : `${shortName(line.name)}\n${formatMoney(line.values[i])}`;
}

function tooltipFormatter(model: SeriesModel) {
  return (params: unknown) => {
    const list = params as { dataIndex: number; seriesIndex?: number; marker?: string }[];
    const i = list[0].dataIndex;
    const lines = [`<strong>${escapeHtml(model.labels[i])}</strong>`];
    model.lines.forEach((l, k) => {
      const v = l.values[i];
      if (v === null || v === undefined) return;
      const marker = list.find((p) => p.seriesIndex === k)?.marker ?? "";
      lines.push(`${marker}${escapeHtml(l.name)}: ${escapeHtml(formatMoney(v))}`);
    });
    for (const n of model.notes?.[i] ?? []) lines.push(escapeHtml(n));
    return lines.join("<br/>");
  };
}

/**
 * Space above the plot for the legend, which wraps to more rows at phone width: estimated from the
 * names (about 7px a character plus the swatch) against a narrow card, so the plot never starts
 * under it. A wide screen simply keeps a little spare room.
 */
export function legendSpace(model: SeriesModel): number {
  if (model.lines.length < 2) return 12;
  const px = model.lines.reduce((sum, l) => sum + l.name.length * 7 + 40, 0);
  return 16 + Math.max(1, Math.ceil(px / 300)) * 22;
}

export function buildSeriesOption(model: SeriesModel, pal: CategoricalPalette) {
  const dense = model.labels.length > MARKER_LIMIT;
  const series = model.lines.map((l) => {
    const { color } = colourOf(l.tone, pal);
    const last = lastIndex(l);
    const width = l.bold ? BOLD_WIDTH : LINE_WIDTH;
    return {
      name: l.name,
      type: "line" as const,
      // Draw every symbol, however dense the axis, or flagged points vanish on a phone.
      showAllSymbol: true,
      lineStyle: { width, color, type: l.dash ?? "solid" },
      itemStyle: { color },
      // Trailing gaps are trimmed so the end label sits on the line's last real point.
      data: l.values.slice(0, last + 1).map((v, i) => {
        const hollow = l.incomplete?.[i] === true;
        return {
          // Geometry only; the figure shown is the exact string.
          value: v === null ? null : Number(v),
          symbol: hollow || !dense ? "circle" : "none",
          symbolSize: hollow ? 11 : l.bold ? 6 : 4,
          itemStyle: hollow ? { color: pal.card, borderColor: color, borderWidth: 2 } : { color },
          incomplete: hollow,
        };
      }),
      endLabel: {
        show: true,
        formatter: endLabelText(l),
        color: pal.text,
        fontSize: 11,
        fontWeight: l.bold ? ("bold" as const) : ("normal" as const),
        width: END_LABEL_SPACE - 12,
        overflow: "truncate" as const,
        distance: 6,
      },
      // Labels that would overlap shift apart vertically instead of printing over one another.
      labelLayout: { moveOverlap: "shiftY" as const },
      emphasis: { focus: "series" as const },
    };
  });
  return {
    animation: false,
    textStyle: { color: pal.text },
    grid: { left: 8, right: END_LABEL_SPACE, top: legendSpace(model), bottom: 8, containLabel: true },
    legend: {
      show: model.lines.length > 1,
      top: 0,
      textStyle: { color: pal.text },
      itemWidth: 18,
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      // The crosshair: a vertical guide at the year or date, a horizontal one at the pointer.
      axisPointer: {
        type: "cross",
        lineStyle: { color: pal.muted, type: "dashed" },
        crossStyle: { color: pal.muted, type: "dashed" },
        label: { show: false },
      },
      formatter: tooltipFormatter(model),
    },
    xAxis: {
      type: "category",
      data: model.labels,
      boundaryGap: false,
      // The last tick would sit under the end labels.
      axisLabel: { color: pal.muted, hideOverlap: true, showMaxLabel: false },
      axisLine: { lineStyle: { color: pal.grid } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: pal.muted, formatter: (v: number) => axisLabel(v) },
      splitLine: { lineStyle: { color: pal.grid } },
    },
    series,
  };
}

/** The accessible name of the chart: what it draws, and where the figures are. */
export function seriesChartLabel(model: SeriesModel, what: string, headline?: string | null): string {
  return `Line chart of ${what}: ${model.lines.map((l) => l.name).join(", ")}. ${headline ? `${headline} ` : ""}The figures are under Show figures.`;
}

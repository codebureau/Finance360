import type { CategoricalPalette } from "@/lib/chart-palette";
import { colourOf } from "@/lib/charts/share";
import { toneOf, type Tone } from "@/lib/entity-colour";
import { axisLabel, escapeHtml } from "@/lib/history-chart";
import { formatMoney } from "@/lib/money";
import { LIFE_EVENT_LABEL } from "@/lib/projection";
import type { ScenarioProjection } from "@/lib/scenarios";

/**
 * The `share-over-time` chart shape (ADR-0031 section 1): parts of a total per year, in two forms
 * built from one model so a switch never changes the content. Pure, so it is unit-tested without a
 * canvas. Money rule: every figure shown is the API's exact string through `formatMoney`;
 * `Number()` only gives ECharts a coordinate to plot.
 */

export type OverTimeForm = "stacked-area" | "lines";
/** Kit order: the first is the default. */
export const OVER_TIME_FORMS: OverTimeForm[] = ["stacked-area", "lines"];

export type OverTimeSeries = {
  key: string;
  name: string;
  tone: Tone;
  /** The exact closing balance at the end of each year, as the API gave it. */
  values: string[];
};

/** A Life event the chart marks: where it takes effect, and the date it falls on. */
export type OverTimeMarker = {
  kind: string;
  /** "Retirement (Alex Sample)". */
  label: string;
  on: string;
  /** The projection year it takes effect from; the index of the year's point. */
  index: number;
};

/** The years spending is not met in full, as runs of consecutive years (inclusive indexes). */
export type ShortSpan = { from: number; to: number };

export type OverTimeModel = {
  /** One label per year: the calendar year it ends in. */
  labels: string[];
  series: OverTimeSeries[];
  /** Each year's total closing balance, exact. */
  totals: string[];
  /** Each year's spending (need) and shortfall, exact, for the tooltip. */
  needs: string[];
  shortfalls: string[];
  markers: OverTimeMarker[];
  shortSpans: ShortSpan[];
};

/** The Life events that read as a milestone on a balance chart; the rest stay in the list. */
export const MARKED_EVENTS = ["retirement", "preservation_age", "age_pension_age"] as const;

/** A stacked area cannot draw a part below zero, so such a run offers lines only. */
export function overTimeForms(model: OverTimeModel): OverTimeForm[] {
  const negative = model.series.some((s) => s.values.some((v) => v.startsWith("-") && Number(v) !== 0));
  return negative ? ["lines"] : OVER_TIME_FORMS;
}

const isShort = (shortfall: string) => shortfall !== "0.00";

/** Shape the existing year result; nothing is computed here that the table does not show. */
export function shapeProjection(p: ScenarioProjection): OverTimeModel {
  const rows = p.rows;
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  const series = p.categories.map((c, i) => ({
    key: c.key,
    name: c.name,
    tone: toneOf("category", c.key, i),
    values: rows.map((r) => r.categories.find((x) => x.category_key === c.key)?.closing ?? "0.00"),
  }));
  const markers = (p.life_events ?? [])
    .filter((e) => (MARKED_EVENTS as readonly string[]).includes(e.kind) && e.year >= 0 && e.year < rows.length)
    .map((e) => ({
      kind: e.kind,
      label: `${LIFE_EVENT_LABEL[e.kind] ?? e.kind} (${memberName(e.member_id)})`,
      on: e.on,
      index: e.year,
    }))
    .sort((a, b) => a.index - b.index);
  const shortSpans: ShortSpan[] = [];
  rows.forEach((r, i) => {
    if (!isShort(r.shortfall)) return;
    const last = shortSpans[shortSpans.length - 1];
    if (last && last.to === i - 1) last.to = i;
    else shortSpans.push({ from: i, to: i });
  });
  return {
    labels: rows.map((r) => String(Number(r.starts_on.slice(0, 4)) + 1)),
    series,
    totals: rows.map((r) => r.closing),
    needs: rows.map((r) => r.need),
    shortfalls: rows.map((r) => r.shortfall),
    markers,
    shortSpans,
  };
}

/** What to notice: where the total goes, and when spending stops being met in full. */
export function headlineOf(p: ScenarioProjection): string {
  const first = p.rows[0];
  const last = p.rows[p.rows.length - 1];
  const horizonAge = p.reference_member.age_now + p.years;
  const start = `Balances in these Categories go from ${formatMoney(first.opening)} today to ${formatMoney(last.closing)} at age ${horizonAge}.`;
  return p.lasts_beyond_horizon
    ? `${start} Spending is met in full every year.`
    : `${start} Spending stops being met in full after age ${p.funds_last_to_age}.`;
}

/** Line style per kind, so a marker is never told apart by colour alone. */
const MARKER_DASH: Record<string, "solid" | "dashed" | "dotted"> = {
  retirement: "solid",
  preservation_age: "dashed",
  age_pension_age: "dotted",
};

function tooltipFormatter(model: OverTimeModel) {
  return (params: unknown) => {
    const first = (params as { dataIndex: number }[])[0];
    const i = first.dataIndex;
    const lines = [
      `<strong>End of ${escapeHtml(model.labels[i])}</strong>`,
      ...model.series.map((s) => `${escapeHtml(s.name)}: ${escapeHtml(formatMoney(s.values[i]))}`),
      `Total: ${escapeHtml(formatMoney(model.totals[i]))}`,
      `Spending: ${escapeHtml(formatMoney(model.needs[i]))}`,
    ];
    if (isShort(model.shortfalls[i])) lines.push(`Short by ${escapeHtml(formatMoney(model.shortfalls[i]))}`);
    for (const m of model.markers.filter((x) => x.index === i)) {
      lines.push(`${escapeHtml(m.label)} from ${escapeHtml(m.on)}`);
    }
    return lines.join("<br/>");
  };
}

export function buildOverTimeOption(model: OverTimeModel, form: OverTimeForm, pal: CategoricalPalette) {
  const stacked = form === "stacked-area";
  const series = model.series.map((s, k) => {
    const { color, opacity } = colourOf(s.tone, pal);
    return {
      name: s.name,
      type: "line",
      ...(stacked ? { stack: "total" } : {}),
      symbol: "none",
      lineStyle: { width: stacked ? 1 : 2, color, opacity },
      itemStyle: { color, opacity },
      ...(stacked ? { areaStyle: { color, opacity: opacity * 0.75 } } : {}),
      data: s.values.map(Number),
      // Marks live on the first series; they draw across the whole plot.
      ...(k === 0
        ? {
            markLine: {
              symbol: "none",
              silent: true,
              label: { show: false },
              data: model.markers.map((m) => ({
                name: m.label,
                xAxis: m.index,
                lineStyle: { color: pal.text, width: 1.5, type: MARKER_DASH[m.kind] ?? "solid" },
              })),
            },
            markArea: {
              silent: true,
              itemStyle: { color: pal.warning, opacity: 0.18 },
              label: { show: true, position: "insideTop", color: pal.text, formatter: "Short" },
              data: model.shortSpans.map((s) => [{ xAxis: s.from }, { xAxis: s.to }]),
            },
          }
        : {}),
    };
  });
  return {
    animation: false,
    textStyle: { color: pal.text },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { color: pal.text }, itemWidth: 18 },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      formatter: tooltipFormatter(model),
    },
    xAxis: {
      type: "category",
      data: model.labels,
      boundaryGap: false,
      axisLabel: { color: pal.muted, hideOverlap: true },
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

export function overTimeChartLabel(form: OverTimeForm, headline: string): string {
  const name = form === "stacked-area" ? "Stacked area chart" : "Line chart";
  return `${name} of balances by Category at the end of each year. ${headline} The Year by year table under Show working has the same values.`;
}

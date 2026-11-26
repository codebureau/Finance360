import type { CategoricalPalette } from "@/lib/chart-palette";
import { colourOf } from "@/lib/charts/share";
import type { Tone } from "@/lib/entity-colour";
import { escapeHtml } from "@/lib/history-chart";
import { formatMoney } from "@/lib/money";

/**
 * The `comparison` chart shape (ADR-0031 section 1): one or two measures across a few items,
 * drawn as grouped bars. One form, no switch. Bars are horizontal so item names stay readable at
 * phone width, coloured by the item (the entity), and every bar is labelled with its exact value,
 * so no legend is needed for the items. A second measure is told apart by a lighter bar, never by
 * another colour. Pure, so it is unit-tested without a canvas. Money rule: every figure shown is
 * the API's exact string through `formatMoney`; `Number()` only gives ECharts a length.
 */

export type ComparisonItem = { key: string; name: string; tone: Tone };

export type ComparisonMeasure = {
  key: string;
  name: string;
  /** One exact amount per item; null where the item has none (no bar is drawn). */
  values: (string | null)[];
  /** Per item: words before the value in its label ("At least"). */
  prefixes?: string[];
};

export type ComparisonModel = {
  items: ComparisonItem[];
  /** One or two. */
  measures: ComparisonMeasure[];
};

/** Room for the value labels beyond the longest bar. */
const LABEL_SPACE = 120;
/** The second measure's bars, lighter than the first so the pair reads without a second hue. */
const SECOND_OPACITY = 0.5;

export function barLabelText(m: ComparisonMeasure, i: number): string {
  const v = m.values[i];
  if (v === null || v === undefined) return "";
  const prefix = m.prefixes?.[i];
  return `${prefix ? `${prefix} ` : ""}${formatMoney(v)}`;
}

export function buildComparisonOption(model: ComparisonModel, pal: CategoricalPalette) {
  const series = model.measures.slice(0, 2).map((m, k) => ({
    name: m.name,
    type: "bar" as const,
    barMaxWidth: 28,
    data: model.items.map((item, i) => {
      const { color, opacity } = colourOf(item.tone, pal);
      const v = m.values[i];
      return {
        value: v === null || v === undefined ? null : Number(v),
        itemStyle: { color, opacity: opacity * (k === 0 ? 1 : SECOND_OPACITY) },
      };
    }),
    label: {
      show: true,
      position: "right" as const,
      color: pal.text,
      fontSize: 11,
      formatter: (p: { dataIndex: number }) => barLabelText(m, p.dataIndex),
    },
  }));
  return {
    animation: false,
    textStyle: { color: pal.text },
    grid: { left: 8, right: LABEL_SPACE, top: model.measures.length > 1 ? 32 : 8, bottom: 8, containLabel: true },
    legend: { show: model.measures.length > 1, top: 0, textStyle: { color: pal.text }, itemWidth: 18 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      formatter: (params: unknown) => {
        const i = (params as { dataIndex: number }[])[0].dataIndex;
        return [
          `<strong>${escapeHtml(model.items[i].name)}</strong>`,
          ...model.measures.map((m) => `${escapeHtml(m.name)}: ${escapeHtml(barLabelText(m, i) || "none")}`),
        ].join("<br/>");
      },
    },
    // First item at the top.
    yAxis: {
      type: "category" as const,
      inverse: true,
      data: model.items.map((i) => i.name),
      axisLabel: { color: pal.text, width: 96, overflow: "break" as const },
      axisLine: { lineStyle: { color: pal.grid } },
      axisTick: { show: false },
    },
    // No scale: every bar carries its value.
    xAxis: { type: "value" as const, show: false },
    series,
  };
}

export function comparisonChartLabel(model: ComparisonModel, headline?: string | null): string {
  return `Bar chart of ${model.measures.map((m) => m.name).join(" and ")} for ${model.items.map((i) => i.name).join(", ")}. ${headline ? `${headline} ` : ""}The figures are in the comparison table under Show figures.`;
}

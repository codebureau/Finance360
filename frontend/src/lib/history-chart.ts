import { formatMoney } from "@/lib/money";
import type { HistoryPoint, HistorySeries } from "@/lib/position";

/**
 * Builds the ECharts option for the Position history. Pure, so it is unit-tested without a canvas.
 *
 * Money rule: every amount shown (tooltip) is the API's exact decimal string passed through
 * `formatMoney`. `Number()` is used only to give ECharts a coordinate to plot, and for the
 * compact y-axis tick text, which is display-only and never stored, summed or compared.
 */

export type ChartTheme = "light" | "dark";

export type Palette = { text: string; muted: string; grid: string; card: string; lines: string[] };

export const PALETTES: Record<ChartTheme, Palette> = {
  light: {
    text: "#0a0a0a",
    muted: "#737373",
    grid: "#e5e5e5",
    card: "#ffffff",
    lines: ["#2f855a", "#c53030", "#2b4acb", "#b7791f"],
  },
  dark: {
    text: "#fafafa",
    muted: "#a3a3a3",
    grid: "#3a3a3a",
    card: "#171717",
    lines: ["#68d391", "#fc8181", "#90a4f4", "#f6d365"],
  },
};

const DASH: ("solid" | "dashed" | "dotted")[] = ["solid", "dashed", "solid", "dotted"];

/** Tick text only (e.g. "1.2M"). Display-only; the exact figures are in the tooltip and table. */
export function axisLabel(value: number): string {
  return new Intl.NumberFormat("en-AU", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

/**
 * One line per series: a data item per point. A flagged (incomplete) point is a larger circle
 * filled with the card colour and outlined in the line colour, so it is genuinely hollow in both
 * themes. Normal points are small and solid.
 */
export function buildSeriesData(
  points: HistoryPoint[],
  s: HistorySeries,
  lineColour: string,
  cardColour: string,
) {
  return points.map((p) => {
    const flagged = p.flagged > 0;
    return {
      // [category, plotted y]: the number is geometry only.
      value: [p.on, Number(s.value(p))] as [string, number],
      symbol: "circle",
      symbolSize: flagged ? 11 : 4,
      itemStyle: flagged
        ? { color: cardColour, borderColor: lineColour, borderWidth: 2 }
        : { color: lineColour },
      flagged,
    };
  });
}

/** Tooltip HTML-free text for the point at ``index``: exact strings, incomplete marked. */
export function tooltipText(points: HistoryPoint[], series: HistorySeries[], index: number) {
  const p = points[index];
  const lines = series.map((s) => `${s.label}: ${formatMoney(s.value(p))}`);
  if (p.flagged > 0) {
    lines.push(`(incomplete: ${p.flagged} ${p.flagged === 1 ? "Account" : "Accounts"} not counted)`);
  }
  return [p.on, ...lines];
}

/** Labels can carry Member names, and ECharts renders the tooltip formatter's result as HTML. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** `palette` is resolved from the theme tokens by the caller; the literal palette is the fallback. */
export function buildOption(
  points: HistoryPoint[],
  series: HistorySeries[],
  theme: ChartTheme,
  palette?: Palette,
) {
  const pal = palette ?? PALETTES[theme];
  return {
    animation: false,
    color: pal.lines,
    textStyle: { color: pal.text },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { color: pal.text }, itemWidth: 18 },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      formatter: (params: unknown) => {
        const first = (params as { dataIndex: number }[])[0];
        const [date, ...rest] = tooltipText(points, series, first.dataIndex);
        return [`<strong>${escapeHtml(date)}</strong>`, ...rest.map(escapeHtml)].join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: points.map((p) => p.on),
      boundaryGap: false,
      axisLabel: { color: pal.muted, hideOverlap: true },
      axisLine: { lineStyle: { color: pal.grid } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: pal.muted, formatter: (v: number) => axisLabel(v) },
      splitLine: { lineStyle: { color: pal.grid } },
    },
    series: series.map((s, k) => ({
      name: s.label,
      type: "line",
      lineStyle: { width: 2, type: DASH[k % DASH.length] },
      // Draw every symbol, however dense the axis, or flagged points vanish on a phone.
      showAllSymbol: true,
      itemStyle: { color: pal.lines[k % pal.lines.length] },
      data: buildSeriesData(points, s, pal.lines[k % pal.lines.length], pal.card),
    })),
  };
}

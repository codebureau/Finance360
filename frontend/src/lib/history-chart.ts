/**
 * What every chart builder shares: the literal palettes (the fallback when the theme tokens cannot
 * be read), the compact y-axis tick text and HTML escaping for tooltips. The chart shapes
 * themselves live in `lib/charts/`.
 *
 * Money rule: `axisLabel` is display-only tick text, never stored, summed or compared; every
 * amount shown elsewhere is the API's exact decimal string through `formatMoney`.
 */

export type ChartTheme = "light" | "dark";

export type Palette = { text: string; muted: string; grid: string; card: string; lines: string[] };

export const PALETTES: Record<ChartTheme, Palette> = {
  light: {
    text: "#0a0a0a",
    muted: "#737373",
    grid: "#e5e5e5",
    card: "#ffffff",
    lines: ["#127eee", "#b64e10", "#04a19b", "#7429c3", "#bf1c7f", "#b18e15"],
  },
  dark: {
    text: "#fafafa",
    muted: "#a3a3a3",
    grid: "#3a3a3a",
    card: "#171717",
    lines: ["#0f64bf", "#ae4e1a", "#1faaa4", "#a170eb", "#c32e85", "#b18e15"],
  },
};

/** Tick text only (e.g. "1.2M"). Display-only; the exact figures are in the tooltip and table. */
export function axisLabel(value: number): string {
  return new Intl.NumberFormat("en-AU", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

/** Labels can carry Member names, and ECharts renders the tooltip formatter's result as HTML. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

import { PALETTES, type ChartTheme, type Palette } from "@/lib/history-chart";

/**
 * Resolves a semantic token (`--success`, `--border`, ...) to an sRGB hex the ECharts canvas can
 * use. Tokens are `oklch()`, which zrender cannot parse, so the colour is painted on a 1px canvas
 * and read back. Returns null where there is no canvas (jsdom) or the token is missing.
 */
function resolve(token: string): string | null {
  const css = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  if (!css) return null;
  const ctx = document.createElement("canvas").getContext?.("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#000000";
  ctx.fillStyle = css;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  if (a === 0) return null;
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** The chart palette from the live theme tokens, falling back per colour to the literal palette. */
export function paletteFromTokens(theme: ChartTheme): Palette {
  const fallback = PALETTES[theme];
  const lines = ["--success", "--money-negative", "--info", "--warning"].map(
    (t, i) => resolve(t) ?? fallback.lines[i],
  );
  return {
    text: resolve("--foreground") ?? fallback.text,
    muted: resolve("--muted-foreground") ?? fallback.muted,
    grid: resolve("--border") ?? fallback.grid,
    card: resolve("--card") ?? fallback.card,
    lines,
  };
}

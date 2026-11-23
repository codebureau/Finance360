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

/** The palette for categorical (per-label) charts: `lines` holds the six `--chart-N` tokens, and
 * `warning` the warning tone. Literal fallbacks are the tokens converted to sRGB. */
export type CategoricalPalette = Palette & { warning: string };

const CATEGORICAL_FALLBACK: Record<ChartTheme, { tokens: string[]; warning: string }> = {
  light: { tokens: ["#127eee", "#b64e10", "#04a19b", "#7429c3", "#bf1c7f", "#b18e15"], warning: "#b7791f" },
  dark: { tokens: ["#0f64bf", "#ae4e1a", "#1faaa4", "#a170eb", "#c32e85", "#b18e15"], warning: "#f6d365" },
};

export function categoricalPaletteFromTokens(theme: ChartTheme): CategoricalPalette {
  const base = paletteFromTokens(theme);
  const fb = CATEGORICAL_FALLBACK[theme];
  return {
    ...base,
    warning: resolve("--warning") ?? fb.warning,
  };
}

/** The chart palette from the live theme tokens, falling back per colour to the literal palette. */
export function paletteFromTokens(theme: ChartTheme): Palette {
  const fallback = PALETTES[theme];
  // The six chart tokens, never the status colours (ADR-0031 section 5).
  const lines = [1, 2, 3, 4, 5, 6].map((n, i) => resolve(`--chart-${n}`) ?? fallback.lines[i]);
  return {
    text: resolve("--foreground") ?? fallback.text,
    muted: resolve("--muted-foreground") ?? fallback.muted,
    grid: resolve("--border") ?? fallback.grid,
    card: resolve("--card") ?? fallback.card,
    lines,
  };
}

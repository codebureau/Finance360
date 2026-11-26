import type { CategoricalPalette } from "@/lib/chart-palette";
import { colourOf } from "@/lib/charts/share";
import type { Tone } from "@/lib/entity-colour";
import { escapeHtml } from "@/lib/history-chart";
import { formatMoney, fromMinor, toMinor } from "@/lib/money";

/**
 * The `build-up` chart shape (ADR-0031 section 1): signed steps from a start to a total, drawn as
 * a waterfall. One form, no switch. Pure, so it is unit-tested without a canvas. Money rule: the
 * running total is worked out in integer cents from the API's exact strings, every label is the
 * exact string through `formatMoney`, and `Number()` only gives ECharts a coordinate.
 *
 * A step is one of: `start` (a total bar from zero), `delta` (a bar from the running total, a rise
 * or a fall by the sign of its amount), `subtotal` (a total bar that must equal the running total)
 * or `end` (a total bar, the answer). Colour is never the only cue: each delta's label carries a
 * `+` or `-`. A rise and a fall take two categorical tokens; totals take the neutral foreground.
 */

export type BuildUpKind = "start" | "delta" | "subtotal" | "end";

export type BuildUpStep = {
  key: string;
  /** On the axis and in the tooltip. */
  name: string;
  kind: BuildUpKind;
  /**
   * delta: the signed exact amount (a fall is negative). start, subtotal, end: the stated total
   * the bar stands for, which must equal the running total (see `reconciles`).
   */
  amount: string;
  /** end only: the words before the amount in its label ("Refund", "Amount payable"). */
  endWord?: string;
};

export type BuildUpModel = { steps: BuildUpStep[] };

/** Rises and falls each take one categorical token, never a status colour (ADR-0031 section 5). */
export const RISE_TONE: Tone = { token: 0 };
export const FALL_TONE: Tone = { token: 2 };
const TOTAL_TONE: Tone = "ink";

/** A step's signed amount in cents; 0n for an amount that is not a plain decimal. */
const cents = (amount: string): bigint => toMinor(amount) ?? 0n;

export type Placed = { step: BuildUpStep; from: string; to: string; running: string };

/**
 * Each step placed on the axis: the bar runs `from` to `to`, and `running` is the total after it.
 * A total bar starts at zero and stands at its stated amount; a delta starts at the running total.
 */
export function place(model: BuildUpModel): Placed[] {
  let running = 0n;
  return model.steps.map((step) => {
    if (step.kind === "delta") {
      const from = running;
      running += cents(step.amount);
      return { step, from: fromMinor(from), to: fromMinor(running), running: fromMinor(running) };
    }
    running = cents(step.amount);
    return { step, from: "0.00", to: fromMinor(running), running: fromMinor(running) };
  });
}

/**
 * True where every subtotal and the end stand exactly at the running total of the steps before
 * it, to the cent. The tax engine has no tolerance (ADR-0030).
 */
export function reconciles(model: BuildUpModel): boolean {
  let running = 0n;
  for (const step of model.steps) {
    if (step.kind === "delta") running += cents(step.amount);
    else if (step.kind === "start") running = cents(step.amount);
    else if (cents(step.amount) !== running) return false;
  }
  return true;
}

/** The label on a bar: a delta says its sign, the end says which outcome, totals are plain. */
export function stepLabel(step: BuildUpStep): string {
  if (step.kind === "delta") {
    const m = cents(step.amount);
    return `${m > 0n ? "+" : ""}${formatMoney(step.amount)}`;
  }
  if (step.kind === "end") {
    const abs = cents(step.amount);
    return `${step.endWord ? `${step.endWord} ` : ""}${formatMoney(fromMinor(abs < 0n ? -abs : abs))}`;
  }
  return formatMoney(step.amount);
}

/** On the bar: the end's word sits on its own line on a wide chart, so the label stays inside the band. */
export function barText(step: BuildUpStep, compact: boolean): string {
  const text = stepLabel(step);
  return !compact && step.kind === "end" && step.endWord ? text.replace(`${step.endWord} `, `${step.endWord}
`) : text;
}

const toneOfStep = (step: BuildUpStep): Tone =>
  step.kind === "delta" ? (cents(step.amount) < 0n ? FALL_TONE : RISE_TONE) : TOTAL_TONE;

/** What a step does, in words, for the tooltip and the accessible name. */
export function directionWord(step: BuildUpStep): string {
  if (step.kind === "delta") return cents(step.amount) < 0n ? "fall" : "rise";
  return step.kind === "start" ? "start" : step.kind === "subtotal" ? "subtotal" : "end";
}

/** `wide` draws labels level and names unrotated; `compact` (a phone) turns both so none overprint. */
export type BuildUpLayout = "wide" | "compact";

type ItemParams = { dataIndex: number };
type Api = {
  value: (dim: number) => number;
  coord: (point: number[]) => number[];
  size: (point: number[]) => number[];
};

/** Room above the highest bar for its label (a compact label is turned, so it needs more). */
const TOP_SPACE: Record<BuildUpLayout, number> = { wide: 40, compact: 84 };

export function buildBuildUpOption(model: BuildUpModel, pal: CategoricalPalette, layout: BuildUpLayout = "wide") {
  const placed = place(model);
  const compact = layout === "compact";
  const fontSize = compact ? 10 : 11;
  const data = placed.map((p, i) => [i, Number(p.from), Number(p.to)]);
  const colours = placed.map((p) => colourOf(toneOfStep(p.step), pal));
  return {
    animation: false,
    textStyle: { color: pal.text },
    grid: { left: 8, right: 24, top: TOP_SPACE[layout], bottom: 8, containLabel: true },
    tooltip: {
      trigger: "item" as const,
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      formatter: (p: ItemParams) => {
        const row = placed[p.dataIndex];
        return [
          `<strong>${escapeHtml(row.step.name)}</strong>`,
          escapeHtml(stepLabel(row.step)),
          `Running total: ${escapeHtml(formatMoney(row.running))}`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "category" as const,
      data: placed.map((p) => p.step.name),
      axisLabel: {
        color: pal.text,
        interval: 0,
        fontSize,
        rotate: compact ? 40 : 0,
        width: compact ? undefined : 66,
        overflow: "break" as const,
      },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    // No scale: every bar carries its value. The zero line is the baseline a refund falls below.
    yAxis: {
      type: "value" as const,
      show: false,
      splitLine: { show: false },
    },
    series: [
      {
        type: "custom" as const,
        // Each datum is [index, from, to]; a bar runs between the two.
        data,
        encode: { x: 0, y: [1, 2] },
        renderItem: (params: ItemParams, api: Api) => {
          const i = params.dataIndex;
          const a = api.coord([i, api.value(1)]);
          const b = api.coord([i, api.value(2)]);
          const width = Math.min(api.size([1, 0])[0] * 0.62, 56);
          const top = Math.min(a[1], b[1]);
          const { color, opacity } = colours[i];
          const label = barText(placed[i].step, compact);
          return {
            type: "group" as const,
            children: [
              {
                type: "rect" as const,
                shape: { x: a[0] - width / 2, y: top, width, height: Math.max(Math.abs(a[1] - b[1]), 1) },
                style: { fill: color, opacity },
              },
              {
                type: "text" as const,
                x: a[0],
                y: top - 4,
                // zrender turns anticlockwise for a positive angle: the text reads upward.
                rotation: compact ? Math.PI / 2 : 0,
                style: {
                  text: label,
                  fill: pal.text,
                  fontSize,
                  fontWeight: placed[i].step.kind === "delta" ? ("normal" as const) : ("bold" as const),
                  textAlign: compact ? ("left" as const) : ("center" as const),
                  textVerticalAlign: compact ? ("middle" as const) : ("bottom" as const),
                },
              },
            ],
          };
        },
      },
    ],
  };
}

export function buildUpChartLabel(model: BuildUpModel, what: string, headline?: string | null): string {
  const names = model.steps.map((s) => `${s.name} ${stepLabel(s)}`).join("; ");
  return `Waterfall chart of ${what}. ${headline ? `${headline} ` : ""}${names}. The figures are in the table under Show figures.`;
}

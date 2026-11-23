import type { CategoricalPalette } from "@/lib/chart-palette";
import { percentToPpm, ppmToPercent, type ExposureDimension, type LookThrough } from "@/lib/exposure";
import { escapeHtml } from "@/lib/history-chart";
import { formatMoney } from "@/lib/money";

/**
 * Presentation of Exposure shares as charts (the tables hold the exact figures). Pure, so it is
 * unit-tested without a canvas. Money rule: amounts shown are the API's exact strings through
 * `formatMoney`; percentages are summed as integer parts per million. `Number()` only gives
 * ECharts a bar length.
 */

export const CHART_TOKENS = 6;
/** Most bars before the smallest fold into one "Other" (for the picture only). */
export const MAX_BARS = 6;

/** Asset class keeps one token per class, in a fixed order, so a colour means one thing everywhere. */
const ASSET_CLASS_TOKEN: Record<string, number> = {
  australian_shares: 0,
  international_shares: 1,
  property: 2,
  infrastructure: 3,
  fixed_income: 4,
  cash: 5,
};

export type Tone = { token: number } | "muted" | "warning";

/**
 * The colour of a label: by the label's identity, never its rank or position in a list. Asset
 * class has a fixed token per class; other Dimensions use the label's seed order in the
 * vocabulary (so USD is the same colour in every view). "other", alternatives and anything
 * unknown are muted. A Dimension seeded with more than six labels reuses tokens; each bar is
 * directly labelled, so colour is never the only cue.
 */
export function labelTone(dimensionKey: string, labelKey: string, vocab: ExposureDimension[]): Tone {
  if (labelKey === "other") return "muted";
  if (dimensionKey === "asset_class") {
    const token = ASSET_CLASS_TOKEN[labelKey];
    return token === undefined ? "muted" : { token };
  }
  const labels = vocab.find((d) => d.key === dimensionKey)?.labels.filter((l) => l.key !== "other") ?? [];
  const index = labels.findIndex((l) => l.key === labelKey);
  return index < 0 ? "muted" : { token: index % CHART_TOKENS };
}

export type BarRow = {
  name: string;
  ppm: number;
  /** Shown beside the bar and in the tooltip. */
  percentText: string;
  /** Exact amount text, where the source has one (look-through lines do; profile weights do not). */
  amountText: string | null;
  tone: Tone;
};

/**
 * Largest first, at most `max` bars including the explicit lines (No profile, Not classified,
 * Unallocated, which always stay). Labels without a colour of their own ("other", alternatives)
 * and the smallest coloured labels beyond the room fold into one muted "Other".
 */
export function foldRows(labelRows: BarRow[], explicitRows: BarRow[], max = MAX_BARS): BarRow[] {
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
    out.push({ name: "Other", ppm, percentText: `${ppmToPercent(ppm)}%`, amountText: null, tone: "muted" });
  }
  return [...out, ...explicitRows];
}

/** Rows for a look-through: positive lines only; the table keeps every line, zeros included. */
export function lookThroughRows(data: LookThrough, vocab: ExposureDimension[]): BarRow[] {
  const labels: BarRow[] = [];
  const explicit: BarRow[] = [];
  for (const line of data.lines) {
    const ppm = line.percent === null ? null : percentToPpm(line.percent);
    if (ppm === null || ppm === 0) continue;
    const row: BarRow = {
      name: line.name,
      ppm,
      percentText: `${line.percent}%`,
      amountText: formatMoney(line.amount.amount, line.amount.currency),
      tone:
        line.kind === "label"
          ? labelTone(data.dimension_key, line.key ?? "", vocab)
          : line.kind === "no_profile"
            ? "warning"
            : "muted",
    };
    (line.kind === "label" ? labels : explicit).push(row);
  }
  return foldRows(labels, explicit);
}

/** Rows for one Dimension of an Exposure profile (weights are percent strings). */
export function profileRows(
  weights: { dimension_key: string; label_key: string; percent: string }[],
  dimension: ExposureDimension,
  vocab: ExposureDimension[],
): BarRow[] {
  const rows: BarRow[] = [];
  for (const w of weights) {
    if (w.dimension_key !== dimension.key) continue;
    const ppm = percentToPpm(w.percent);
    if (ppm === null || ppm === 0) continue;
    rows.push({
      name: dimension.labels.find((l) => l.key === w.label_key)?.name ?? w.label_key,
      ppm,
      percentText: `${w.percent}%`,
      amountText: null,
      tone: labelTone(dimension.key, w.label_key, vocab),
    });
  }
  return foldRows(rows, []);
}

const NOTICE_PPM = 10_000; // one point: the named weight tolerance

/** The plain-language headline of a look-through: what to notice, computed from the data. */
export function headline(data: LookThrough): string {
  const dimension = data.dimension_name.toLowerCase();
  const ppm = (percent: string | null) => (percent === null ? 0 : (percentToPpm(percent) ?? 0));
  const sumOf = (kind: string) => data.lines.filter((l) => l.kind === kind).reduce((s, l) => s + ppm(l.percent), 0);
  const top = data.lines
    .filter((l) => l.kind === "label" && ppm(l.percent) > 0)
    .sort((a, b) => ppm(b.percent) - ppm(a.percent))
    .slice(0, 2);
  const topPpm = top.reduce((s, l) => s + ppm(l.percent), 0);
  const parts: string[] = [];
  if (top.length === 0) parts.push(`Nothing is classified by ${dimension} yet`);
  else parts.push(`${topPpm * 2 >= 1_000_000 ? "Mostly" : "Largest:"} ${top.map((l) => l.name).join(" and ")}`);
  const noProfile = sumOf("no_profile");
  if (noProfile > 0) parts.push(`${ppmToPercent(noProfile)}% has no profile yet`);
  const notClassified = sumOf("not_classified");
  if (notClassified >= NOTICE_PPM && top.length > 0) parts.push(`${ppmToPercent(notClassified)}% is not classified`);
  const unallocated = sumOf("unallocated");
  if (data.flagged && unallocated > 0) parts.push(`${ppmToPercent(unallocated)}% is unallocated, beyond tolerance`);
  return parts.join("; ");
}

function colourOf(tone: Tone, pal: CategoricalPalette): { color: string; opacity: number } {
  if (tone === "warning") return { color: pal.warning, opacity: 0.85 };
  if (tone === "muted") return { color: pal.muted, opacity: 0.55 };
  return { color: pal.lines[tone.token % pal.lines.length], opacity: 1 };
}

/** Pixel height of a bar chart of `rows` bars. */
export const barChartHeight = (rows: number) => Math.max(rows, 1) * 30 + 8;

/** One horizontal bar per row, in the order given (first at the top), directly labelled. */
export function buildBarOption(rows: BarRow[], pal: CategoricalPalette) {
  return {
    animation: false,
    textStyle: { color: pal.text },
    grid: { left: 4, right: 52, top: 2, bottom: 2, containLabel: true },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: pal.card,
      borderColor: pal.grid,
      textStyle: { color: pal.text },
      formatter: (params: unknown) => {
        const row = rows[(params as { dataIndex: number }).dataIndex];
        return [`<strong>${escapeHtml(row.name)}</strong>`, escapeHtml(row.percentText), ...(row.amountText ? [escapeHtml(row.amountText)] : [])].join("<br/>");
      },
    },
    xAxis: { type: "value", min: 0, max: 100, show: false },
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
        data: rows.map((r) => ({ value: Number(ppmToPercent(r.ppm)), itemStyle: colourOf(r.tone, pal) })),
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

/** Geometry (percent of the track) of the form's running-total bar: the track runs 0 to 110%,
 * and the band marks the accepted 99 to 101 range. Display only. */
export function totalBarGeometry(totalPpm: number) {
  const SCALE = 1_100_000;
  return {
    fill: (Math.min(totalPpm, SCALE) / SCALE) * 100,
    bandStart: ((1_000_000 - NOTICE_PPM) / SCALE) * 100,
    bandEnd: ((1_000_000 + NOTICE_PPM) / SCALE) * 100,
  };
}

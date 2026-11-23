import { toneOf, type Tone } from "@/lib/entity-colour";
import { percentToPpm, ppmToPercent, type ExposureDimension, type LookThrough } from "@/lib/exposure";
import { foldRows, type ShareRow } from "@/lib/charts/share";
import { formatMoney } from "@/lib/money";

/**
 * Presentation of Exposure shares as charts (the tables hold the exact figures). Pure, so it is
 * unit-tested without a canvas. Money rule: amounts shown are the API's exact strings through
 * `formatMoney`; percentages are summed as integer parts per million. `Number()` only gives
 * ECharts a bar length.
 */

/**
 * The colour of a label: by the label's identity, never its rank or position in a list (ADR-0031
 * section 5). Asset class has a fixed token per class; other Dimensions take the label's seed
 * order in the vocabulary (so USD is the same colour in every view). "other", alternatives and
 * anything unknown are muted.
 */
export function labelTone(dimensionKey: string, labelKey: string, vocab: ExposureDimension[]): Tone {
  if (dimensionKey === "asset_class") return toneOf("asset-class", labelKey);
  const labels = vocab.find((d) => d.key === dimensionKey)?.labels.filter((l) => l.key !== "other") ?? [];
  return toneOf("dimension", labelKey, labels.findIndex((l) => l.key === labelKey));
}

/** Rows for a look-through: positive lines only; the table keeps every line, zeros included. */
export function lookThroughRows(data: LookThrough, vocab: ExposureDimension[]): ShareRow[] {
  const labels: ShareRow[] = [];
  const explicit: ShareRow[] = [];
  for (const line of data.lines) {
    const ppm = line.percent === null ? null : percentToPpm(line.percent);
    if (ppm === null || ppm === 0) continue;
    const row: ShareRow = {
      key: line.key ?? line.kind,
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
): ShareRow[] {
  const rows: ShareRow[] = [];
  for (const w of weights) {
    if (w.dimension_key !== dimension.key) continue;
    const ppm = percentToPpm(w.percent);
    if (ppm === null || ppm === 0) continue;
    rows.push({
      key: w.label_key,
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

import { toneOf, type Tone } from "@/lib/entity-colour";
import {
  percentToPpm,
  ppmToPercent,
  type ExposureDimension,
  type ExposureProfile,
  type LookThrough,
} from "@/lib/exposure";
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

/**
 * Rows for one Dimension of an Exposure profile (weights are percent strings). For a fund-of-funds,
 * `throughPpm` is the share held in underlying funds: it is shown as an explicit muted part, so a
 * chart of the fund's own 10% reads as 10% of a ring, not a whole one.
 */
export function profileRows(
  weights: { dimension_key: string; label_key: string; percent: string }[],
  dimension: ExposureDimension,
  vocab: ExposureDimension[],
  throughPpm = 0,
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
  const through: ShareRow[] =
    throughPpm > 0 && rows.length > 0
      ? [
          {
            key: "through-funds",
            name: "Through underlying funds",
            ppm: throughPpm,
            percentText: `${ppmToPercent(throughPpm)}%`,
            amountText: null,
            tone: "muted",
          },
        ]
      : [];
  return foldRows(rows, through);
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
  if (data.flagged && unallocated > 0)
    parts.push(
      data.change_since
        ? `${ppmToPercent(unallocated)}% is unallocated: the option mix may have changed`
        : `${ppmToPercent(unallocated)}% is unallocated, beyond tolerance`,
    );
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

const WHOLE_PPM = 1_000_000;

/**
 * The plain-language headline of an Exposure profile card: for a fund-of-funds how much is held in
 * other funds, otherwise what the asset-class weights mostly are.
 */
export function profileHeadline(profile: ExposureProfile, vocab: ExposureDimension[]): string {
  const ppm = (percent: string) => percentToPpm(percent) ?? 0;
  const references = [...profile.references].sort((a, b) => ppm(b.percent) - ppm(a.percent));
  if (references.length > 0) {
    const total = references.reduce((sum, r) => sum + ppm(r.percent), 0);
    const named = references
      .slice(0, 2)
      .map((r) => `${r.instrument_name} (${r.percent}%)`)
      .join(" and ");
    const more = references.length > 2 ? ` and ${references.length - 2} more` : "";
    return `A fund of funds: ${ppmToPercent(total)}% is held in ${named}${more}`;
  }
  const labels = vocab.find((d) => d.key === "asset_class")?.labels ?? [];
  const top = profile.weights
    .filter((w) => w.dimension_key === "asset_class")
    .sort((a, b) => ppm(b.percent) - ppm(a.percent))
    .slice(0, 2)
    .map((w) => labels.find((l) => l.key === w.label_key)?.name ?? w.label_key);
  return top.length === 0 ? "No asset class weights are published" : `Mostly ${top.join(" and ")}`;
}

/**
 * The parts of a fund-of-funds as shares of the whole profile: each underlying fund, and whatever
 * the fund holds directly. A fund keeps one colour within the profile, by its order in it.
 */
export function referenceRows(profile: ExposureProfile): ShareRow[] {
  const rows: ShareRow[] = [];
  let held = 0;
  profile.references.forEach((r, i) => {
    const ppm = percentToPpm(r.percent) ?? 0;
    held += ppm;
    rows.push({
      key: `fund-${r.instrument_id}`,
      name: r.instrument_name,
      ppm,
      percentText: `${r.percent}%`,
      amountText: null,
      tone: toneOf("dimension", `fund-${r.instrument_id}`, i),
    });
  });
  if (held < WHOLE_PPM && rows.length > 0) {
    const ppm = WHOLE_PPM - held;
    rows.push({
      key: "held-directly",
      name: "Held directly",
      ppm,
      percentText: `${ppmToPercent(ppm)}%`,
      amountText: null,
      tone: toneOf("dimension", "held-directly", rows.length),
    });
  }
  return foldRows(rows, []);
}

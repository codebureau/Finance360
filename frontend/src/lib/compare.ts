import type { ComparisonModel } from "@/lib/charts/comparison";
import type { SeriesModel } from "@/lib/charts/series";
import { formatMoney, fromMinor, toMinor } from "@/lib/money";
import { scenarioTone, type ComparedScenario, type Comparison } from "@/lib/scenarios";

/** At most this many Scenarios are drawn; the rest stay in the table only (six tokens, ADR-0031). */
export const MAX_DRAWN = 6;

export type DrawnSet = {
  /** The Scenarios the charts draw, in creation order. */
  drawn: ComparedScenario[];
  /** Resolved Scenarios beyond the sixth: in the table only. */
  folded: ComparedScenario[];
  /** Scenarios that could not be projected: nothing to draw. */
  unresolved: ComparedScenario[];
  /** Every Scenario's id, so a colour is the same here as on Projection. */
  ids: number[];
};

export function drawnSet(data: Comparison): DrawnSet {
  const resolved = data.scenarios.filter((s) => s.resolved);
  return {
    drawn: resolved.slice(0, MAX_DRAWN),
    folded: resolved.slice(MAX_DRAWN),
    unresolved: data.scenarios.filter((s) => !s.resolved),
    ids: data.scenarios.map((s) => s.scenario.id),
  };
}

/** One line per Scenario: its total closing balance at the end of each projection year. */
export function balancesModel(set: DrawnSet): SeriesModel {
  const years = [...new Set(set.drawn.flatMap((s) => s.balances.map((b) => b.year)))].sort((a, b) => a - b);
  return {
    labels: years.map(String),
    lines: set.drawn.map((s) => {
      const byYear = new Map(s.balances.map((b) => [b.year, b.closing]));
      return {
        key: String(s.scenario.id),
        name: s.scenario.name,
        tone: scenarioTone(set.ids, s.scenario.id),
        values: years.map((y) => byYear.get(y) ?? null),
      };
    }),
  };
}

/** One bar per Scenario: the Sustainable retirement income, a year. */
export function incomeModel(set: DrawnSet): ComparisonModel {
  return {
    items: set.drawn.map((s) => ({
      key: String(s.scenario.id),
      name: s.scenario.name,
      tone: scenarioTone(set.ids, s.scenario.id),
    })),
    measures: [
      {
        key: "sustainable",
        name: "Sustainable retirement income, a year",
        values: set.drawn.map((s) => s.sustainable?.annual_spending ?? null),
        prefixes: set.drawn.map((s) => (s.sustainable?.capped ? "At least" : "")),
      },
    ],
  };
}

/** The Scenarios with the largest and smallest of an exact amount, and their gap, exact. */
function spread(rows: { name: string; amount: string }[]) {
  const minors = rows.map((r) => ({ ...r, minor: toMinor(r.amount) })).filter((r): r is typeof r & { minor: bigint } => r.minor !== null);
  if (minors.length < 2) return null;
  let hi = minors[0];
  let lo = minors[0];
  for (const m of minors) {
    if (m.minor > hi.minor) hi = m;
    if (m.minor < lo.minor) lo = m;
  }
  return { hi, lo, gap: fromMinor(hi.minor - lo.minor) };
}

/** What to notice about the balances: where the Scenarios end up, and how far apart. */
export function balancesHeadline(set: DrawnSet): string | null {
  const ends = set.drawn
    .map((s) => ({ name: s.scenario.name, amount: s.balances[s.balances.length - 1]?.closing }))
    .filter((r): r is { name: string; amount: string } => r.amount !== undefined);
  if (ends.length === 0) return null;
  const short = set.drawn.filter((s) => s.lasts_beyond_horizon === false).length;
  const shortText =
    short === 0
      ? "Spending is met in full to the horizon in every Scenario."
      : `${short === 1 ? "One Scenario runs" : `${short} Scenarios run`} short before the horizon.`;
  const sp = spread(ends);
  if (!sp) return `${ends[0].name} ends the projection at ${formatMoney(ends[0].amount)}. ${shortText}`;
  if (sp.gap === "0.00") return `Every Scenario ends the projection at ${formatMoney(sp.hi.amount)}. ${shortText}`;
  return `By the end of the projection the balances run from ${formatMoney(sp.lo.amount)} (${sp.lo.name}) to ${formatMoney(sp.hi.amount)} (${sp.hi.name}), ${formatMoney(sp.gap)} apart. ${shortText}`;
}

/** What to notice about the sustainable income: the range across the Scenarios. */
export function incomeHeadline(set: DrawnSet): string | null {
  const rows = set.drawn.flatMap((s) =>
    s.sustainable ? [{ name: s.scenario.name, amount: s.sustainable.annual_spending }] : [],
  );
  if (rows.length === 0) return null;
  const sp = spread(rows);
  if (!sp) return `Sustainable retirement income is ${formatMoney(rows[0].amount)} a year in ${rows[0].name}.`;
  if (sp.gap === "0.00") return `Sustainable retirement income is ${formatMoney(sp.hi.amount)} a year in every Scenario.`;
  return `Sustainable retirement income runs from ${formatMoney(sp.lo.amount)} a year (${sp.lo.name}) to ${formatMoney(sp.hi.amount)} a year (${sp.hi.name}): ${formatMoney(sp.gap)} a year apart.`;
}

/** The stat for a Scenario: how long the funds last, in words. */
export function lastsText(s: ComparedScenario): string {
  return s.lasts_beyond_horizon ? `Beyond age ${s.horizon_age}` : `Age ${s.funds_last_to_age}`;
}

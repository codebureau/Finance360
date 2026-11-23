import { expect, test } from "vitest";

import { buildOverTimeOption, overTimeForms, shapeProjection, headlineOf } from "@/lib/charts/share-over-time";
import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { toneOf } from "@/lib/entity-colour";
import type { ScenarioProjection } from "@/lib/scenarios";

// Invented: four years, the last two short; Categories out of the usual order.
const year = (t: number, cash: string, shares: string, shortfall = "0.00") => ({
  t,
  starts_on: `${2026 + t}-10-25`,
  age: 40 + t,
  opening: "0.00",
  closing: (Number(cash) + Number(shares)).toFixed(2),
  need: "100.00",
  shortfall,
  categories: [
    { category_key: "superannuation", closing: "10.00" },
    { category_key: "cash", closing: cash },
    { category_key: "investments", closing: shares },
  ],
});
const p = {
  years: 4,
  reference_member: { id: 1, name: "Alex Sample", age_now: 40, retirement_year: 2 },
  funds_last_to_age: 42,
  lasts_beyond_horizon: false,
  categories: [
    { key: "cash", name: "Cash" },
    { key: "investments", name: "Investments" },
    { key: "superannuation", name: "Superannuation" },
  ],
  members: [{ id: 1, name: "Alex Sample", age_now: 40, retirement_year: 2 }],
  life_events: [
    { member_id: 1, kind: "unrestricted_super_age", on: "2028-01-01", year: 1, age_years: 65, age_months: 0, source: "rule_pack" },
    { member_id: 1, kind: "retirement", on: "2028-02-02", year: 2, age_years: 42, age_months: 0, source: "scenario" },
  ],
  rows: [year(0, "50.00", "5.00"), year(1, "40.00", "5.00"), year(2, "0.00", "0.00", "20.00"), year(3, "0.00", "0.00", "100.00")],
} as unknown as ScenarioProjection;

test("series follow the Category, each year's values are the row's closings, and only milestones are marked", () => {
  const m = shapeProjection(p);
  expect(m.series.map((s) => s.name)).toEqual(["Cash", "Investments", "Superannuation"]);
  expect(m.series[0].values).toEqual(["50.00", "40.00", "0.00", "0.00"]);
  expect(m.series[2].values).toEqual(["10.00", "10.00", "10.00", "10.00"]);
  for (const s of m.series) expect(s.tone).toEqual(toneOf("category", s.key));
  expect(m.markers).toEqual([{ kind: "retirement", label: "Retirement (Alex Sample)", on: "2028-02-02", index: 2 }]);
  expect(m.labels).toEqual(["2027", "2028", "2029", "2030"]);
});

test("short years are shaded as one span per run, and the headline names the age", () => {
  const m = shapeProjection(p);
  expect(m.shortSpans).toEqual([{ from: 2, to: 3 }]);
  expect(headlineOf(p)).toContain("after age 42");
  const option = buildOverTimeOption(m, "stacked-area", categoricalPaletteFromTokens("light"));
  expect(option.series[0].markArea!.data).toEqual([[{ xAxis: 2 }, { xAxis: 3 }]]);
  expect(option.series.every((s) => s.stack === "total")).toBe(true);
});

test("a negative balance offers lines only", () => {
  const m = shapeProjection(p);
  expect(overTimeForms(m)).toEqual(["stacked-area", "lines"]);
  m.series[0].values[1] = "-5.00";
  expect(overTimeForms(m)).toEqual(["lines"]);
});

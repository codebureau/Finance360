import { expect, test } from "vitest";

import {
  balancesHeadline,
  balancesModel,
  drawnSet,
  incomeHeadline,
  incomeModel,
  lastsText,
  MAX_DRAWN,
} from "@/lib/compare";
import { toneOf } from "@/lib/entity-colour";
import { fromMinor } from "@/lib/money";
import { scenarioTone, type ComparedScenario, type Comparison } from "@/lib/scenarios";

// Invented figures.
const sc = (id: number, name: string, over: Partial<ComparedScenario> = {}): ComparedScenario =>
  ({
    scenario: { id, key: name.toLowerCase(), name },
    resolved: true,
    errors: [],
    horizon_age: 95,
    drawdown_order: [],
    funds_last_to_age: null,
    lasts_beyond_horizon: true,
    sustainable: { annual_spending: "90000.00", capped: false },
    spending: null,
    age_pension: null,
    provenance: [],
    balances: [
      { year: 2027, closing: `${id}00.00` },
      { year: 2028, closing: `${id}50.25` },
    ],
    ...over,
  }) as ComparedScenario;
const cmp = (scenarios: ComparedScenario[]): Comparison => ({
  scenarios,
  differing: [],
  drawdown_differs: false,
  spending_differs: false,
});

test("a Scenario keeps one colour: by creation order, whatever else is drawn", () => {
  expect(scenarioTone([5, 2, 9], 2)).toEqual(toneOf("scenario", "2", 0));
  expect(scenarioTone([5, 2, 9], 5)).toEqual({ token: 1 });
  expect(scenarioTone([5, 2, 9], 9)).toEqual({ token: 2 });
  // The order is of all Scenarios, so a seventh wraps modulo six.
  expect(scenarioTone([1, 2, 3, 4, 5, 6, 7], 7)).toEqual({ token: 0 });
});

test("balance lines are each Scenario's own year-end totals, in its colour; years are joined", () => {
  const set = drawnSet(
    cmp([sc(1, "Best"), sc(2, "Likely", { balances: [{ year: 2028, closing: "5.00" }, { year: 2029, closing: "6.00" }] })]),
  );
  const m = balancesModel(set);
  expect(m.labels).toEqual(["2027", "2028", "2029"]);
  expect(m.lines.map((l) => l.name)).toEqual(["Best", "Likely"]);
  expect(m.lines[0].values).toEqual(["100.00", "150.25", null]);
  expect(m.lines[1].values).toEqual([null, "5.00", "6.00"]);
  expect(m.lines.map((l) => l.tone)).toEqual([{ token: 0 }, { token: 1 }]);
});

test("up to six are drawn and the rest fold into the table only; unresolved ones are not drawn", () => {
  const many = [1, 2, 3, 4, 5, 6, 7].map((i) => sc(i, `S${i}`));
  const set = drawnSet(cmp([...many, sc(8, "Broken", { resolved: false, balances: [], sustainable: null })]));
  expect(MAX_DRAWN).toBe(6);
  expect(set.drawn.map((s) => s.scenario.name)).toEqual(["S1", "S2", "S3", "S4", "S5", "S6"]);
  expect(set.folded.map((s) => s.scenario.name)).toEqual(["S7"]);
  expect(set.unresolved.map((s) => s.scenario.name)).toEqual(["Broken"]);
  expect(balancesModel(set).lines).toHaveLength(6);
  expect(incomeModel(set).items).toHaveLength(6);
});

test("the income bars are the table's Sustainable figures, a floor marked 'At least'", () => {
  const set = drawnSet(
    cmp([sc(1, "Best"), sc(2, "Likely", { sustainable: { annual_spending: "55000.00", capped: true } })]),
  );
  const m = incomeModel(set);
  expect(m.measures[0].values).toEqual(["90000.00", "55000.00"]);
  expect(m.measures[0].prefixes).toEqual(["", "At least"]);
});

test("the headlines name the spread exactly", () => {
  const set = drawnSet(
    cmp([
      sc(1, "Best"),
      sc(2, "Worst", {
        sustainable: { annual_spending: "55000.50", capped: false },
        lasts_beyond_horizon: false,
        funds_last_to_age: 81,
      }),
    ]),
  );
  expect(incomeHeadline(set)).toBe(
    "Sustainable retirement income runs from 55,000.50 a year (Worst) to 90,000.00 a year (Best): 34,999.50 a year apart.",
  );
  expect(balancesHeadline(set)).toBe(
    "By the end of the projection the balances run from 150.25 (Best) to 250.25 (Worst), 100.00 apart. One Scenario runs short before the horizon.",
  );
  expect(incomeHeadline(drawnSet(cmp([sc(1, "Best"), sc(2, "Likely")])))).toBe(
    "Sustainable retirement income is 90,000.00 a year in every Scenario.",
  );
});

test("the funds-last stat reads in words", () => {
  expect(lastsText(sc(1, "Best"))).toBe("Beyond age 95");
  expect(lastsText(sc(1, "Best", { lasts_beyond_horizon: false, funds_last_to_age: 81 }))).toBe("Age 81");
});

test("minor units round-trip to exact two-place text", () => {
  expect(fromMinor(3499950n)).toBe("34999.50");
  expect(fromMinor(-5n)).toBe("-0.05");
  expect(fromMinor(0n)).toBe("0.00");
});

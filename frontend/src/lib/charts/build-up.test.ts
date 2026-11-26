import { expect, test } from "vitest";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import {
  FALL_TONE,
  RISE_TONE,
  buildBuildUpOption,
  buildUpChartLabel,
  place,
  reconciles,
  stepLabel,
  type BuildUpModel,
} from "@/lib/charts/build-up";
import { colourOf } from "@/lib/charts/share";

const pal = categoricalPaletteFromTokens("light");

// Invented: 1,000.00 start, an offset (a fall) of 100.50, a rise of 50.25, total 949.75, credits
// of 1,200.00 in two falls, ending 250.25 below the line (a refund).
const refund: BuildUpModel = {
  steps: [
    { key: "a", name: "Start", kind: "start", amount: "1000.00" },
    { key: "b", name: "Offsets", kind: "delta", amount: "-100.50" },
    { key: "c", name: "Levy", kind: "delta", amount: "50.25" },
    { key: "d", name: "Total", kind: "subtotal", amount: "949.75" },
    { key: "e", name: "Withheld", kind: "delta", amount: "-1000.00" },
    { key: "f", name: "Franking", kind: "delta", amount: "-200.00" },
    { key: "g", name: "Refund", kind: "end", amount: "-250.25", endWord: "Refund" },
  ],
};
const payable: BuildUpModel = {
  steps: [
    { key: "a", name: "Start", kind: "start", amount: "1000.00" },
    { key: "d", name: "Total", kind: "subtotal", amount: "1000.00" },
    { key: "e", name: "Withheld", kind: "delta", amount: "-400.00" },
    { key: "g", name: "Amount payable", kind: "end", amount: "600.00", endWord: "Amount payable" },
  ],
};

test("each bar runs from the running total: a fall goes down, a rise goes up, a total starts at zero", () => {
  const o = buildBuildUpOption(refund, pal);
  expect(o.series[0].data).toEqual([
    [0, 0, 1000],
    [1, 1000, 899.5],
    [2, 899.5, 949.75],
    [3, 0, 949.75],
    [4, 949.75, -50.25],
    [5, -50.25, -250.25],
    [6, 0, -250.25],
  ]);
});

test("a refund ends below the line and says so in words; an amount payable ends above it", () => {
  const r = place(refund);
  expect(r.at(-1)).toMatchObject({ from: "0.00", to: "-250.25" });
  expect(stepLabel(refund.steps.at(-1)!)).toBe("Refund 250.25");
  const p = place(payable);
  expect(p.at(-1)).toMatchObject({ from: "0.00", to: "600.00" });
  expect(stepLabel(payable.steps.at(-1)!)).toBe("Amount payable 600.00");
});

test("start plus the signed steps equals each total, to the cent", () => {
  expect(reconciles(refund)).toBe(true);
  expect(reconciles(payable)).toBe(true);
  // A credit added instead of subtracted, or a subtotal a cent out, no longer reconciles.
  const wrong: BuildUpModel = { steps: refund.steps.map((s) => (s.key === "e" ? { ...s, amount: "1000.00" } : s)) };
  expect(reconciles(wrong)).toBe(false);
  const cent: BuildUpModel = { steps: refund.steps.map((s) => (s.key === "d" ? { ...s, amount: "949.76" } : s)) };
  expect(reconciles(cent)).toBe(false);
});

test("labels carry the sign, and rises and falls take different tokens, so colour is not the only cue", () => {
  expect(refund.steps.map(stepLabel)).toEqual([
    "1,000.00",
    "-100.50",
    "+50.25",
    "949.75",
    "-1,000.00",
    "-200.00",
    "Refund 250.25",
  ]);
  const o = buildBuildUpOption(refund, pal);
  const drawn = (i: number) =>
    o.series[0].renderItem(
      { dataIndex: i },
      {
        value: (d: number) => o.series[0].data[i][d],
        coord: ([x, y]: number[]) => [x * 10, -y],
        size: () => [100, 0],
      },
    );
  const fill = (i: number) => drawn(i).children[0].style.fill;
  expect(fill(1)).toBe(colourOf(FALL_TONE, pal).color);
  expect(fill(2)).toBe(colourOf(RISE_TONE, pal).color);
  expect(fill(1)).not.toBe(fill(2));
  // Totals and the end are neutral, whichever the outcome.
  expect(fill(0)).toBe(pal.text);
  expect(fill(6)).toBe(pal.text);
  expect(drawn(6).children[1].style.text).toBe("Refund\n250.25");
});

test("the chart's accessible label carries the headline, the form and every step", () => {
  const label = buildUpChartLabel(refund, "tax", "A headline.");
  expect(label).toContain("Waterfall chart");
  expect(label).toContain("A headline.");
  expect(label).toContain("Offsets -100.50");
  expect(label).toContain("Show figures");
});

test("a phone layout turns labels and names so none overprint", () => {
  expect(buildBuildUpOption(refund, pal, "compact").xAxis.axisLabel.rotate).toBeGreaterThan(0);
  expect(buildBuildUpOption(refund, pal, "wide").xAxis.axisLabel.rotate).toBe(0);
});

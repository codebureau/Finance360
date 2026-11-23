import { expect, test } from "vitest";

import {
  buildBarOption,
  buildDonutOption,
  buildShareOption,
  buildStackedBarOption,
  foldRows,
  formsFor,
  legendRows,
  type ShareRow,
} from "@/lib/charts/share";
import type { Tone } from "@/lib/entity-colour";

const pal = { text: "#000", muted: "#777", grid: "#ddd", card: "#fff", lines: ["#1", "#2", "#3", "#4", "#5", "#6"], warning: "#w" };
const row = (key: string, ppm: number, tone: Tone, over: Partial<ShareRow> = {}): ShareRow => ({
  key,
  name: key.toUpperCase(),
  ppm,
  percentText: `${ppm / 10_000}%`,
  amountText: `${ppm / 10_000}.00`,
  tone,
  ...over,
});

test("beyond six parts the smallest fold into one muted Other that still lists what it holds", () => {
  const rows = Array.from({ length: 8 }, (_, i) => row(`p${i}`, (80 - i * 10) * 10_000 + 1, { token: i % 6 }));
  const folded = foldRows(rows, []);
  expect(folded).toHaveLength(6);
  const other = folded.at(-1)!;
  expect(other.name).toBe("Other");
  expect(other.tone).toBe("muted");
  expect(other.parts?.map((p) => p.key)).toEqual(["p5", "p6", "p7"]); // the legend keeps every part
  expect(other.ppm).toBe(rows[5].ppm + rows[6].ppm + rows[7].ppm);
  // folding never loses a part: the picture's total equals the parts' total
  expect(folded.reduce((s, r) => s + r.ppm, 0)).toBe(rows.reduce((s, r) => s + r.ppm, 0));
});

test("six parts or fewer are not folded", () => {
  const rows = Array.from({ length: 6 }, (_, i) => row(`p${i}`, 100_000 + i, { token: i }));
  expect(foldRows(rows, []).map((r) => r.key)).toEqual(["p5", "p4", "p3", "p2", "p1", "p0"]);
});

test("a part below zero, or a whole of zero, offers bars only (no switch)", () => {
  expect(formsFor([row("a", 600_000, { token: 0 }), row("b", 400_000, { token: 1 })])).toEqual(["donut", "stacked-bar", "bars"]);
  expect(formsFor([row("a", 1_200_000, { token: 0 }), row("b", -200_000, { token: 1 })])).toEqual(["bars"]);
  expect(formsFor([row("a", 0, { token: 0 })])).toEqual(["bars"]);
  expect(formsFor([])).toEqual(["bars"]);
});

test("in a legend form no token is worn twice: a smaller part repeating one is drawn muted", () => {
  const rows = [row("big", 500_000, { token: 2 }), row("small", 200_000, { token: 2 }), row("other", 300_000, { token: 4 })];
  const drawn = legendRows(rows);
  expect(drawn.map((r) => r.key)).toEqual(["big", "small", "other"]); // order kept, nothing folded away
  expect(drawn.map((r) => r.tone)).toEqual([{ token: 2 }, "muted", { token: 4 }]);
  // bars name every bar on its axis, so the token may repeat there
  const bars = buildBarOption(rows, pal);
  expect(bars.series[0].data.map((d) => d.itemStyle.color)).toEqual(["#3", "#3", "#5"]);
});

test("the donut and the stacked bar draw the same parts with the same exact text in the tooltip", () => {
  const rows = [row("cash", 600_000, { token: 2 }, { name: "Cash <b>" }), row("res", 400_000, "muted")];
  const donut = buildDonutOption(rows, pal);
  expect(donut.series[0].type).toBe("pie");
  expect(donut.series[0].data.map((d) => d.value)).toEqual([600_000, 400_000]);
  expect(donut.series[0].data[0].itemStyle.color).toBe("#3");
  expect(donut.series[0].data[1].itemStyle.color).toBe("#777");
  const stacked = buildStackedBarOption(rows, pal);
  expect(stacked.series.map((s) => s.name)).toEqual(["Cash <b>", "RES"]);
  expect(stacked.xAxis.max).toBe(1_000_000);
  for (const html of [
    donut.tooltip.formatter({ dataIndex: 0 }),
    stacked.tooltip.formatter({ seriesIndex: 0 }),
  ]) {
    expect(html).toContain("60%");
    expect(html).toContain("60.00");
    expect(html).not.toContain("<b>"); // names are escaped
  }
});

test("no form animates or draws a second y-axis", () => {
  const rows = [row("a", 1_000_000, { token: 0 })];
  for (const form of ["donut", "stacked-bar", "bars"] as const) {
    const opt = buildShareOption(form, rows, pal) as { animation: boolean; yAxis?: unknown };
    expect(opt.animation).toBe(false);
    expect(Array.isArray(opt.yAxis)).toBe(false);
  }
});

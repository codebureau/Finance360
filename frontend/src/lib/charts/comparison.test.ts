import { expect, test } from "vitest";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { barLabelText, buildComparisonOption, type ComparisonModel } from "@/lib/charts/comparison";

const pal = categoricalPaletteFromTokens("light");

// Invented: three items, the last without a value, the second a floor ("At least").
const model: ComparisonModel = {
  items: [
    { key: "1", name: "Best", tone: { token: 0 } },
    { key: "2", name: "Likely <i>", tone: { token: 1 } },
    { key: "3", name: "Worst", tone: { token: 2 } },
  ],
  measures: [
    { key: "m", name: "Income, a year", values: ["90000.00", "55000.50", null], prefixes: ["", "At least", ""] },
  ],
};

test("one bar per item in the item's own colour, labelled with the exact value", () => {
  const o = buildComparisonOption(model, pal);
  expect(o.series).toHaveLength(1);
  expect(o.series[0].data.map((d) => d.value)).toEqual([90000, 55000.5, null]);
  expect(o.series[0].data.map((d) => d.itemStyle.color)).toEqual([pal.lines[0], pal.lines[1], pal.lines[2]]);
  expect(barLabelText(model.measures[0], 0)).toBe("90,000.00");
  expect(barLabelText(model.measures[0], 1)).toBe("At least 55,000.50");
  expect(barLabelText(model.measures[0], 2)).toBe("");
  expect(o.series[0].label.formatter({ dataIndex: 1 })).toBe("At least 55,000.50");
  expect(o.yAxis.data).toEqual(["Best", "Likely <i>", "Worst"]);
});

test("no legend for one measure; a second measure is lighter, never another colour", () => {
  expect(buildComparisonOption(model, pal).legend.show).toBe(false);
  const two: ComparisonModel = {
    ...model,
    measures: [model.measures[0], { key: "n", name: "Other", values: ["1.00", "2.00", "3.00"] }],
  };
  const o = buildComparisonOption(two, pal);
  expect(o.legend.show).toBe(true);
  expect(o.series[1].data[0].itemStyle.color).toBe(o.series[0].data[0].itemStyle.color);
  expect(o.series[1].data[0].itemStyle.opacity).toBeLessThan(o.series[0].data[0].itemStyle.opacity);
});

test("the tooltip escapes names", () => {
  const html = buildComparisonOption(model, pal).tooltip.formatter([{ dataIndex: 1 }]);
  expect(html).toContain("&#60;i&#62;");
  expect(html).not.toContain("<i>");
  expect(html).toContain("At least 55,000.50");
});

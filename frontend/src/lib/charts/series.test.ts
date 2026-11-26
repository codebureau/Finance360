import { expect, test } from "vitest";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { buildSeriesOption, endLabelText, lastIndex, type SeriesModel } from "@/lib/charts/series";
import { toneOf } from "@/lib/entity-colour";
import { axisLabel, escapeHtml } from "@/lib/history-chart";

const pal = categoricalPaletteFromTokens("light");

// Invented: two lines over four labels; the second line stops early and has one incomplete point.
const model: SeriesModel = {
  labels: ["2027", "2028", "2029", "2030"],
  lines: [
    { key: "a", name: "Best <b>", tone: { token: 0 }, values: ["1000.50", "2000.00", "3000.00", "1234567.89"] },
    {
      key: "b",
      name: "Worst",
      tone: { token: 1 },
      dash: "dashed",
      bold: true,
      values: ["900.00", "800.00", null, null],
      incomplete: [false, true, false, false],
    },
  ],
  notes: [[], ["(incomplete: 1 Account not counted)"], [], []],
};

test("each line's data is its exact values as geometry, trailing gaps trimmed", () => {
  const o = buildSeriesOption(model, pal);
  expect(o.series.map((s) => s.name)).toEqual(["Best <b>", "Worst"]);
  expect(o.series[0].data.map((d) => d.value)).toEqual([1000.5, 2000, 3000, 1234567.89]);
  expect(o.series[1].data.map((d) => d.value)).toEqual([900, 800]);
  expect(o.xAxis.data).toEqual(model.labels);
  expect(lastIndex(model.lines[1])).toBe(1);
});

test("end labels are the name and the last exact value, on the line's own last point", () => {
  expect(endLabelText(model.lines[0])).toBe("Best <b>\n1,234,567.89");
  expect(endLabelText(model.lines[1])).toBe("Worst\n800.00");
  const o = buildSeriesOption(model, pal);
  expect(o.series[1].endLabel.formatter).toBe("Worst\n800.00");
  // Labels that would collide shift apart rather than overprint.
  expect(o.series[0].labelLayout.moveOverlap).toBe("shiftY");
  // Room for them, whatever the width.
  expect(o.grid.right).toBeGreaterThanOrEqual(96);
});

test("a long name is cut in the end label only, so each label stays two lines", () => {
  const long = { key: "l", name: "Retire early, spend more", tone: { token: 2 }, values: ["5.00"] };
  expect(endLabelText(long)).toBe("Retire early,…\n5.00");
  expect(buildSeriesOption({ labels: ["x"], lines: [long] }, pal).series[0].name).toBe("Retire early, spend more");
});

test("an incomplete point is a larger hollow marker in the line's colour; bold and dash are secondary encodings", () => {
  const o = buildSeriesOption(model, pal);
  const [ok, hollow] = o.series[1].data;
  expect(ok.incomplete).toBe(false);
  expect(hollow.incomplete).toBe(true);
  expect(hollow.symbolSize).toBeGreaterThan(ok.symbolSize);
  expect(hollow.itemStyle).toMatchObject({ color: pal.card, borderColor: pal.lines[1] });
  expect(o.series[1].showAllSymbol).toBe(true);
  expect(o.series[1].lineStyle).toMatchObject({ type: "dashed", color: pal.lines[1] });
  expect(o.series[1].lineStyle.width).toBeGreaterThan(o.series[0].lineStyle.width);
  expect(o.series[0].lineStyle.width).toBe(2);
});

test("colour follows the tone (token, ink) and is re-themed with the palette", () => {
  const ink: SeriesModel = {
    labels: ["x"],
    lines: [{ key: "n", name: "Net", tone: toneOf("position-part", "net"), values: ["1.00"] }],
  };
  const light = buildSeriesOption(ink, categoricalPaletteFromTokens("light"));
  const dark = buildSeriesOption(ink, categoricalPaletteFromTokens("dark"));
  expect(light.series[0].lineStyle.color).toBe(categoricalPaletteFromTokens("light").text);
  expect(dark.series[0].lineStyle.color).toBe(categoricalPaletteFromTokens("dark").text);
  expect(light.series[0].lineStyle.color).not.toBe(dark.series[0].lineStyle.color);
});

test("the legend is on for two or more lines and off for one", () => {
  expect(buildSeriesOption(model, pal).legend.show).toBe(true);
  expect(buildSeriesOption({ ...model, lines: [model.lines[0]] }, pal).legend.show).toBe(false);
});

test("the crosshair tooltip lists exact figures, notes the incomplete point and escapes names", () => {
  const o = buildSeriesOption(model, pal);
  expect(o.tooltip.axisPointer.type).toBe("cross");
  const html = o.tooltip.formatter([{ dataIndex: 1 }]);
  expect(html).toContain("2,000.00");
  expect(html).toContain("800.00");
  expect(html).toContain("(incomplete: 1 Account not counted)");
  expect(html).toContain("&#60;b&#62;");
  expect(html).not.toContain("<b>");
  // A line with no value at that label is left out, not shown as zero.
  expect(o.tooltip.formatter([{ dataIndex: 2 }])).not.toContain("Worst");
});

test("y-axis ticks are compact text and the escape helper is exact", () => {
  expect(buildSeriesOption(model, pal).yAxis.axisLabel.formatter(1_200_000)).toBe("1.2M");
  expect(axisLabel(-500)).toBe("-500");
  expect(escapeHtml("a&b")).toBe("a&#38;b");
});

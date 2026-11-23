import { expect, test } from "vitest";

import { axisLabel, buildOption, escapeHtml, PALETTES, tooltipText } from "@/lib/history-chart";
import type { HistoryPoint, HistorySeries } from "@/lib/position";

const figs = (household: string) => ({ household, joint: "0.00", members: [] });
const point = (on: string, net: string, flagged = 0): HistoryPoint => ({
  on,
  assets: figs(net),
  liabilities: figs("0.00"),
  net: figs(net),
  flagged,
});
const points = [point("2025-01-31", "1200.50", 1), point("2025-02-28", "1234567.89")];
const series: HistorySeries[] = [{ key: "net", label: "Net <b>", tone: { token: 0 }, value: (p) => p.net.household }];

test("the tooltip shows the exact API strings and marks an incomplete point", () => {
  expect(tooltipText(points, series, 1)).toEqual(["2025-02-28", "Net <b>: 1,234,567.89"]);
  expect(tooltipText(points, series, 0)).toEqual([
    "2025-01-31",
    "Net <b>: 1,200.50",
    "(incomplete: 1 Account not counted)",
  ]);
});

test("flagged points are always drawn, larger, and hollow (card-filled) in both themes", () => {
  for (const theme of ["light", "dark"] as const) {
    const [line] = buildOption(points, series, theme).series;
    expect(line.showAllSymbol).toBe(true); // not hidden when the axis is dense (a phone)
    const [flagged, normal] = line.data;
    expect(flagged.symbolSize).toBeGreaterThan(normal.symbolSize);
    expect(flagged.flagged).toBe(true);
    expect(normal.flagged).toBe(false);
    expect(flagged.itemStyle.color).toBe(PALETTES[theme].card); // hollow: the background shows
    expect(flagged.itemStyle.borderColor).toBe(PALETTES[theme].lines[0]);
    expect(flagged.itemStyle.borderWidth).toBeGreaterThan(0);
    expect(normal.itemStyle.color).toBe(PALETTES[theme].lines[0]);
  }
});

test("the x axis is the month-end dates and the tooltip formatter escapes labels", () => {
  const option = buildOption(points, series, "light");
  expect(option.xAxis.data).toEqual(["2025-01-31", "2025-02-28"]);
  const html = option.tooltip.formatter([{ dataIndex: 1 }]);
  expect(html).toContain("1,234,567.89");
  expect(html).toContain("&#60;b&#62;");
  expect(html).not.toContain("<b>");
  expect(escapeHtml("a&b")).toBe("a&#38;b");
});

test("the option is re-themed by theme and y-axis ticks are compact money text", () => {
  const light = buildOption(points, series, "light");
  const dark = buildOption(points, series, "dark");
  expect(light.color).toEqual([PALETTES.light.lines[0]]);
  expect(dark.color).toEqual([PALETTES.dark.lines[0]]);
  expect(light.color).not.toEqual(dark.color);
  expect(axisLabel(1_200_000)).toBe("1.2M");
  expect(axisLabel(-500)).toBe("-500");
  expect(light.yAxis.axisLabel.formatter(1_200_000)).toBe("1.2M");
});

test("series wear chart tokens or neutral ink, never a status colour (ADR-0031)", () => {
  const three: HistorySeries[] = [
    { key: "assets", label: "Assets", tone: { token: 0 }, value: (p) => p.assets.household },
    { key: "liabilities", label: "Liabilities", tone: { token: 1 }, value: (p) => p.liabilities.household },
    { key: "net", label: "Net", tone: "ink", value: (p) => p.net.household },
  ];
  for (const theme of ["light", "dark"] as const) {
    const pal = PALETTES[theme];
    const option = buildOption(points, three, theme);
    expect(option.color).toEqual([pal.lines[0], pal.lines[1], pal.text]);
    // The old status greens and reds are gone from the palette.
    expect(pal.lines).not.toContain("#2f855a");
    expect(pal.lines).not.toContain("#c53030");
    expect(option.series.map((s) => s.itemStyle.color)).toEqual(option.color);
  }
});

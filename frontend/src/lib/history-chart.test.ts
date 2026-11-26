import { expect, test } from "vitest";

import { axisLabel, escapeHtml, PALETTES } from "@/lib/history-chart";

test("y-axis ticks are compact text and tooltip labels are escaped", () => {
  expect(axisLabel(1_200_000)).toBe("1.2M");
  expect(axisLabel(-500)).toBe("-500");
  expect(escapeHtml("a&b")).toBe("a&#38;b");
  expect(escapeHtml("<b>")).toBe("&#60;b&#62;");
});

test("the literal palettes are the six chart tokens, never the old status colours (ADR-0031)", () => {
  for (const theme of ["light", "dark"] as const) {
    expect(PALETTES[theme].lines).toHaveLength(6);
    expect(PALETTES[theme].lines).not.toContain("#2f855a");
    expect(PALETTES[theme].lines).not.toContain("#c53030");
  }
});

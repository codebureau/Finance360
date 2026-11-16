import { expect, test } from "vitest";

import { bpToPercent, evenShares, formatShare, percentToBp } from "@/lib/shares";

test("percentages convert to basis points exactly, with at most two decimals", () => {
  expect(percentToBp("60")).toBe(6000);
  expect(percentToBp(" 33.33 ")).toBe(3333);
  expect(percentToBp("60.5")).toBe(6050);
  expect(percentToBp("100")).toBe(10000);
  for (const bad of ["", "abc", "-5", "50.123", "1e2", "50%", "1000"]) {
    expect(percentToBp(bad)).toBeNull();
  }
});

test("basis points format back to a percentage without trailing zeros", () => {
  expect([6000, 3333, 5050, 1, 10000].map(formatShare)).toEqual([
    "60%",
    "33.33%",
    "50.5%",
    "0.01%",
    "100%",
  ]);
  expect(bpToPercent(5000)).toBe("50");
});

test("even shares always sums to exactly 10000", () => {
  expect(evenShares(2)).toEqual([5000, 5000]);
  expect(evenShares(3)).toEqual([3334, 3333, 3333]);
  for (let n = 1; n <= 12; n++) {
    expect(evenShares(n).reduce((a, b) => a + b, 0)).toBe(10000);
  }
});

import { expect, test } from "vitest";

import { toMinor } from "@/lib/money";
import type { Position } from "@/lib/position";
import { categoryShareRows, compositionHeadline, memberShareRows, percentText } from "@/lib/position-share";

const figs = (household: string, alex: string, jordan: string, joint = "0.00") => ({
  household,
  joint,
  members: [
    { member_id: 1, sole: alex, joint_share: "0.00", total: alex },
    { member_id: 2, sole: jordan, joint_share: "0.00", total: jordan },
  ],
});
const cat = (key: string, name: string, f: ReturnType<typeof figs>) => ({ key, name, figures: f, flagged: 0, accounts: [] });
const position = (categories: ReturnType<typeof cat>[]): Position =>
  ({
    as_at: "2026-10-17",
    tag: null,
    members: [
      { id: 1, name: "Alex Sample" },
      { id: 2, name: "Jordan Sample" },
    ],
    tags: [],
    classes: [
      { class: "asset", figures: figs("100.00", "70.00", "30.00"), flagged: 0, categories },
      { class: "liability", figures: figs("-900.00", "-900.00", "0.00"), flagged: 0, categories: [] },
    ],
    net: figs("-800.00", "-830.00", "30.00"),
    flagged: 0,
  }) as Position;

test("money text becomes exact integer minor units, never a float", () => {
  expect(toMinor("12345678901234567.89")).toBe(1234567890123456789n);
  expect(toMinor("-0.05")).toBe(-5n);
  expect(toMinor("7")).toBe(700n);
  expect(toMinor("1.5")).toBe(150n);
  expect(toMinor("abc")).toBeNull();
});

test("shares are integer parts per million of the sum of the parts, largest first", () => {
  const rows = categoryShareRows(
    position([cat("cash", "Cash", figs("10.00", "10.00", "0.00")), cat("property", "Property", figs("90.00", "90.00", "0.00"))]),
    "household",
  );
  expect(rows.map((r) => [r.key, r.ppm, r.percentText, r.amountText])).toEqual([
    ["property", 900_000, "90%", "90.00"],
    ["cash", 100_000, "10%", "10.00"],
  ]);
});

test("the parts never exceed the whole and lose at most one part per million each", () => {
  const rows = categoryShareRows(
    position(["1.00", "1.00", "1.00"].map((v, i) => cat(`k${i}`, `K${i}`, figs(v, v, "0.00")))),
    "household",
  );
  const total = rows.reduce((s, r) => s + r.ppm, 0);
  expect(total).toBeLessThanOrEqual(1_000_000);
  expect(total).toBeGreaterThanOrEqual(1_000_000 - rows.length);
});

test("liabilities are never a part, and a Category with nothing in it is left out of the picture", () => {
  const rows = categoryShareRows(
    position([cat("cash", "Cash", figs("100.00", "100.00", "0.00")), cat("overseas_pensions", "Overseas pensions", figs("0.00", "0.00", "0.00"))]),
    "household",
  );
  expect(rows.map((r) => r.key)).toEqual(["cash"]);
});

test("the Joint view shares the joint figures, and a Member's part counts the joint share", () => {
  const p = position([cat("cash", "Cash", figs("100.00", "70.00", "30.00", "40.00")), cat("property", "Property", figs("60.00", "60.00", "0.00", "10.00"))]);
  expect(categoryShareRows(p, "joint").map((r) => [r.key, r.amountText])).toEqual([
    ["cash", "40.00"],
    ["property", "10.00"],
  ]);
  expect(memberShareRows(p, "household").map((r) => [r.name, r.percentText])).toEqual([
    ["Alex Sample", "70%"],
    ["Jordan Sample", "30%"],
  ]);
});

test("a negative part keeps its sign so the form falls back to bars", () => {
  const rows = categoryShareRows(
    position([cat("cash", "Cash", figs("120.00", "120.00", "0.00")), cat("vehicles", "Vehicles", figs("-20.00", "-20.00", "0.00"))]),
    "household",
  );
  expect(rows.find((r) => r.key === "vehicles")!.ppm).toBeLessThan(0);
});

test("percent text is whole or one decimal, from integers", () => {
  expect(percentText(524_000)).toBe("52.4%");
  expect(percentText(500_000)).toBe("50%");
  expect(percentText(1_234)).toBe("0.1%");
  expect(percentText(-200_000)).toBe("-20%");
});

test("the headline names the largest share and the next, or says nothing is valued", () => {
  const rows = categoryShareRows(
    position([cat("cash", "Cash", figs("30.00", "30.00", "0.00")), cat("property", "Property", figs("70.00", "70.00", "0.00"))]),
    "household",
  );
  expect(compositionHeadline(rows, "category")).toBe("Property is the largest share, 70%; Cash is 30%.");
  expect(compositionHeadline(rows, "member")).toBe("Property holds the largest share of assets, 70%; Cash is 30%.");
  expect(compositionHeadline([], "category")).toBe("Nothing is valued yet.");
});

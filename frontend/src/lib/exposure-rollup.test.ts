import { expect, test } from "vitest";

import type { Rollup } from "@/lib/exposure";
import { notAnalysedGroups, parseExposureSearch, wholePercent } from "@/lib/exposure-rollup";

test("the search keeps only a named level, a Dimension key and a date", () => {
  expect(parseExposureSearch({ level: "member", dimension: "region", asat: "2026-09-30" })).toEqual({
    level: "member",
    dimension: "region",
    asat: "2026-09-30",
  });
  // Household is the default level and is not kept; junk is dropped
  expect(parseExposureSearch({ level: "household", dimension: "Bad Key", asat: "yesterday" })).toEqual({});
  expect(parseExposureSearch({ level: "galaxy" })).toEqual({});
});

test("a whole percent is worked in integers, half up, and is absent for a whole of nothing", () => {
  expect(wholePercent(14001, 74001)).toBe(19);
  expect(wholePercent(1, 8)).toBe(13); // 12.5 rounds up
  expect(wholePercent(1, 3)).toBe(33);
  expect(wholePercent(5, 0)).toBeNull();
  expect(wholePercent(5, -10)).toBeNull();
  // beyond 2^53 the arithmetic is still exact
  expect(wholePercent(9_000_000_000_000_001, 9_000_000_000_000_001)).toBe(100);
});

test("Not analysed Accounts group by Category, with one plain reason per group and specific reasons kept", () => {
  const money = (amount: string) => ({ amount, currency: "AUD" });
  const rollup = {
    categories: [
      { key: "investments", name: "Investments" },
      { key: "cash", name: "Cash" },
      { key: "loans_credit", name: "Loans & credit" },
    ],
    not_analysed: [
      { account_id: 3, name: "Loan", category_key: "loans_credit", ownership: "sole", value: money("-100.00"), reason: null },
      { account_id: 1, name: "Savings", category_key: "cash", ownership: "sole", value: money("50.00"), reason: null },
      { account_id: 2, name: "Everyday", category_key: "cash", ownership: "sole", value: money("20.00"), reason: null },
      { account_id: 4, name: "Broker", category_key: "investments", ownership: "joint", value: money("0.00"), reason: "It has no committed Import." },
    ],
  } as unknown as Rollup;
  const groups = notAnalysedGroups(rollup);
  expect(groups.map((g) => [g.name, g.accounts.map((a) => a.name)])).toEqual([
    ["Investments", ["Broker"]],
    ["Cash", ["Savings", "Everyday"]],
    ["Loans & credit", ["Loan"]],
  ]);
  expect(groups[0].reason).toBeNull(); // its Account carries its own reason instead
  expect(groups[1].reason).toBe("Held as money, so there is nothing inside to look through.");
  expect(groups[2].reason).toBe("Debts: they reduce the Position but hold no investments.");
});

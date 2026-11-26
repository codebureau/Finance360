import { expect, test } from "vitest";

import { place, reconciles } from "@/lib/charts/build-up";
import type { TaxHouseholdResult, TaxMemberResult } from "@/lib/tax";
import { householdLines, memberLines, netAgrees, waterfallHeadline, waterfallModel } from "@/lib/tax-waterfall";

// Invented figures, each Member's lines adding up.
const base = {
  surcharge_rate: "0",
  low_income_offset: "0.00",
  small_business_offset: "0.00",
  notes: [],
  medicare_levy_surcharge: "0.00",
  help_repayment: "0.00",
  payg_instalments: "0.00",
  franking_credits: "0.00",
};
const payer: TaxMemberResult = {
  ...base,
  member_id: 1,
  name: "Alex Sample",
  total_income: "90000.00",
  total_deductions: "1000.00",
  taxable_income: "89000.00",
  tax_on_taxable_income: "20000.00",
  offsets: "0.00",
  medicare_levy: "1780.00",
  help_repayment: "2100.00",
  total_tax: "23880.00",
  tax_withheld: "23000.00",
  credits: "23000.00",
  net_outcome: "-880.00",
};
const refunded: TaxMemberResult = {
  ...base,
  member_id: 2,
  name: "Jordan Sample",
  total_income: "40000.00",
  total_deductions: "0.00",
  taxable_income: "40000.00",
  tax_on_taxable_income: "4000.00",
  offsets: "300.00",
  medicare_levy: "800.00",
  total_tax: "4500.00",
  tax_withheld: "4400.00",
  franking_credits: "250.50",
  credits: "4650.50",
  net_outcome: "150.50",
};
const household: TaxHouseholdResult = {
  tax_on_taxable_income: "24000.00",
  offsets: "300.00",
  medicare_levy: "2580.00",
  medicare_levy_surcharge: "0.00",
  help_repayment: "2100.00",
  total_tax: "28380.00",
  credits: "27650.50",
  net_outcome: "-729.50",
  taxable_income: "129000.00",
  surcharge_income: "129000.00",
  surcharge_thresholds: "family",
  surcharge_tier: 0,
};

test("an amount payable: start plus steps is total tax, total tax less credits is the end, nil steps left out", () => {
  const model = waterfallModel(memberLines(payer));
  expect(model.steps.map((s) => s.key)).toEqual([
    "tax_on_taxable_income",
    "medicare_levy",
    "help_repayment",
    "total_tax",
    "tax_withheld",
    "net_outcome",
  ]);
  expect(reconciles(model)).toBe(true);
  expect(netAgrees(memberLines(payer))).toBe(true);
  const end = place(model).at(-1)!;
  expect(end.step.name).toBe("Amount payable");
  expect(end.to).toBe("880.00");
});

test("a refund: an offset falls, a credit falls, the end is below the line", () => {
  const model = waterfallModel(memberLines(refunded));
  const byKey = Object.fromEntries(model.steps.map((s) => [s.key, s.amount]));
  expect(byKey.offsets).toBe("-300.00");
  expect(byKey.tax_withheld).toBe("-4400.00");
  expect(byKey.franking_credits).toBe("-250.50");
  expect(reconciles(model)).toBe(true);
  expect(netAgrees(memberLines(refunded))).toBe(true);
  expect(place(model).at(-1)).toMatchObject({ to: "-150.50" });
  expect(model.steps.at(-1)?.name).toBe("Refund");
});

test("the Household's steps are the sum of the Members' steps, and it reconciles", () => {
  const lines = householdLines([payer, refunded], household);
  const sums = waterfallModel(lines).steps;
  const a = Object.fromEntries(waterfallModel(memberLines(payer)).steps.map((s) => [s.key, s.amount]));
  const b = Object.fromEntries(waterfallModel(memberLines(refunded)).steps.map((s) => [s.key, s.amount]));
  const cents = (v: string | undefined) => Math.round(Number(v ?? "0") * 100);
  for (const s of sums) expect(cents(s.amount)).toBe(cents(a[s.key]) + cents(b[s.key]));
  expect(reconciles(waterfallModel(lines))).toBe(true);
  expect(netAgrees(lines)).toBe(true);
  expect(lines.total_income).toBe("130000.00");
  expect(lines.total_deductions).toBe("1000.00");
});

test("an estimate with only tax and credits still draws", () => {
  const model = waterfallModel({
    ...memberLines(payer),
    offsets: "0.00",
    medicare_levy: "0.00",
    help_repayment: "0.00",
    total_tax: "20000.00",
    credits: "20000.00",
    tax_withheld: "20000.00",
    net_outcome: "0.00",
  });
  expect(model.steps.map((s) => s.key)).toEqual(["tax_on_taxable_income", "total_tax", "tax_withheld", "net_outcome"]);
  expect(model.steps.at(-1)).toMatchObject({ amount: "0.00", endWord: "Nothing payable" });
});

test("the headline names who pays or is refunded and what adds the most, with Incomplete when set", () => {
  expect(waterfallHeadline("Alex", memberLines(payer), false)).toBe(
    "Alex pays 880.00 more than the credits cover; the HELP repayment and the Medicare levy add the most after the tax on taxable income.",
  );
  expect(waterfallHeadline("Jordan", memberLines(refunded), true)).toBe(
    "Incomplete: Jordan gets a refund of 150.50: the credits are more than the total tax; the Medicare levy adds the most after the tax on taxable income.",
  );
});

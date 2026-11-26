import type { BuildUpModel, BuildUpStep } from "@/lib/charts/build-up";
import { formatMoney, fromMinor, toMinor } from "@/lib/money";
import type { TaxHouseholdResult, TaxMemberResult } from "@/lib/tax";

/**
 * The Tax estimate as a `build-up` (ADR-0031, view `tax.estimate`): from the tax on taxable
 * income, through offsets, Medicare levy, surcharge and HELP, to total tax, then the three
 * credits, to the amount payable or the refund. Pure, and exact: every figure is the engine's
 * string and every sum is in integer cents (ADR-0030: no tolerance).
 */

/** The lines one waterfall draws, for one Member or the Household. */
export type WaterfallLines = {
  tax_on_taxable_income: string;
  offsets: string;
  medicare_levy: string;
  medicare_levy_surcharge: string;
  help_repayment: string;
  total_tax: string;
  tax_withheld: string;
  franking_credits: string;
  payg_instalments: string;
  credits: string;
  net_outcome: string;
  taxable_income: string;
  total_income: string;
  total_deductions: string;
};

const minor = (v: string): bigint => toMinor(v) ?? 0n;
const sum = (values: string[]): string => fromMinor(values.reduce((a, v) => a + minor(v), 0n));
const negate = (v: string): string => fromMinor(-minor(v));
const isNil = (v: string): boolean => minor(v) === 0n;

export function memberLines(m: TaxMemberResult): WaterfallLines {
  return m;
}

/**
 * The Household's lines: the engine's own for the lines it states, and the Members' sums for the
 * three credits and for income and deductions (the Household has no line of its own for them; it
 * is the exact sum of the Members').
 */
export function householdLines(members: TaxMemberResult[], household: TaxHouseholdResult): WaterfallLines {
  const of = (key: "tax_withheld" | "franking_credits" | "payg_instalments" | "total_income" | "total_deductions") =>
    sum(members.map((m) => m[key]));
  return {
    ...household,
    tax_withheld: of("tax_withheld"),
    franking_credits: of("franking_credits"),
    payg_instalments: of("payg_instalments"),
    total_income: of("total_income"),
    total_deductions: of("total_deductions"),
  };
}

/** Nil steps are left out of the picture (the table keeps every line). */
export function waterfallModel(l: WaterfallLines): BuildUpModel {
  const delta = (key: string, name: string, amount: string): BuildUpStep[] =>
    isNil(amount) ? [] : [{ key, name, kind: "delta", amount }];
  // Total tax less credits, the other way round from the engine's net outcome (positive refund).
  const payable = fromMinor(minor(l.total_tax) - minor(l.credits));
  const owing = minor(payable);
  return {
    steps: [
      { key: "tax_on_taxable_income", name: "Tax on taxable income", kind: "start", amount: l.tax_on_taxable_income },
      ...delta("offsets", "Offsets", negate(l.offsets)),
      ...delta("medicare_levy", "Medicare levy", l.medicare_levy),
      ...delta("medicare_levy_surcharge", "Medicare levy surcharge", l.medicare_levy_surcharge),
      ...delta("help_repayment", "HELP repayment", l.help_repayment),
      { key: "total_tax", name: "Total tax", kind: "subtotal", amount: l.total_tax },
      ...delta("tax_withheld", "Tax withheld", negate(l.tax_withheld)),
      ...delta("franking_credits", "Franking credits", negate(l.franking_credits)),
      ...delta("payg_instalments", "PAYG instalments", negate(l.payg_instalments)),
      {
        key: "net_outcome",
        name: owing < 0n ? "Refund" : "Amount payable",
        kind: "end",
        amount: payable,
        endWord: owing < 0n ? "Refund" : owing > 0n ? "Amount payable" : "Nothing payable",
      },
    ],
  };
}

/** The engine's net outcome (positive refund) agrees with the waterfall's end, to the cent. */
export function netAgrees(l: WaterfallLines): boolean {
  return minor(l.net_outcome) === -(minor(l.total_tax) - minor(l.credits));
}

const RISE_NAMES: Record<string, string> = {
  medicare_levy: "the Medicare levy",
  medicare_levy_surcharge: "the Medicare levy surcharge",
  help_repayment: "the HELP repayment",
};

/** The headline, computed from the figures: who pays or is refunded, and what adds the most. */
export function waterfallHeadline(who: string, l: WaterfallLines, incomplete: boolean): string {
  const net = minor(l.net_outcome);
  const subject = who === "Household" ? "The Household" : who;
  const result =
    net < 0n
      ? `${subject} pays ${formatMoney(negate(l.net_outcome))} more than the credits cover`
      : net > 0n
        ? `${subject} gets a refund of ${formatMoney(l.net_outcome)}: the credits are more than the total tax`
        : `${subject} owes nothing and is owed nothing: the credits equal the total tax`;
  const rises = Object.keys(RISE_NAMES)
    .map((key) => ({ key, amount: minor(l[key as keyof WaterfallLines]) }))
    .filter((r) => r.amount > 0n)
    .sort((a, b) => (a.amount === b.amount ? 0 : a.amount > b.amount ? -1 : 1))
    .slice(0, 2);
  const names = rises.map((r) => RISE_NAMES[r.key]);
  const adds =
    names.length === 0
      ? ""
      : names.length === 1
        ? `; ${names[0]} adds the most after the tax on taxable income`
        : `; ${names[0]} and ${names[1]} add the most after the tax on taxable income`;
  return `${incomplete ? "Incomplete: " : ""}${result}${adds}.`;
}

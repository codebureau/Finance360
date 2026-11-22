import type { components } from "@/api/schema";

export type Projection = components["schemas"]["ProjectionOut"];
export type ProjectionRow = components["schemas"]["ProjectionYearOut"];

/** The Categories the first projection covers, in the order spending is drawn from them. */
export const CATEGORY_KEYS = ["cash", "investments", "superannuation", "overseas_pensions"] as const;

export const CATEGORY_LABEL: Record<string, string> = {
  cash: "Cash",
  investments: "Investments",
  superannuation: "Superannuation",
  overseas_pensions: "Overseas pensions",
};

/** What a line debit is called (ADR-0028). The reason is a tag; nothing branches on it. */
export const DEBIT_REASON_LABEL: Record<string, string> = {
  div293: "Division 293",
  insurance_premium: "Insurance premium",
  fund_fee: "Fund fee",
};

/** What the first projection leaves out or assumes. Shown on the screen beside every result. */
export const SIMPLIFICATIONS = [
  "Property, vehicles and loans are not projected: only Cash, Investments, Superannuation and Overseas pensions are. Spending is taken to cover mortgage repayments.",
  "Superannuation can be drawn from the later of its owner's retirement and preservation age, and at the latest at 65. Overseas pensions can be drawn from the owner's retirement, whatever their age.",
  "Before the last retirement, spending is only what a spending phase starting at the projection start states. With none, nothing is spent then, and a salary can't be projected.",
  "Annual saving is split equally across Members and goes into Investments, except for a Member with a salary stream: they save what their salary leaves after spending.",
  "Salary: take-home pay is the amount entered, unless Tax in the projection is on: then the tax engine estimates it from gross pay and Division 293 is paid a year after the year it is for, from the Member's own super by default. Employer super is the Superannuation Guarantee rate less the flat contributions tax, from the Rule pack. Not modelled: contribution caps, the maximum contribution base, salary sacrifice and fund fees.",
  "The headline age is the youngest Member's.",
  "Annual steps in today's dollars: returns are nominal and inflation is taken off. No fees. The Age Pension is calculated (see its card), and tax only where Tax in the projection is on.",
] as const;

/** What a Life event is called on the timeline. */
export const LIFE_EVENT_LABEL: Record<string, string> = {
  retirement: "Retirement",
  preservation_age: "Preservation age",
  unrestricted_super_age: "Unrestricted super age",
  age_pension_age: "Age Pension age",
};

/** Where a spending phase starts, in words: `From the last retirement`, `10 years after the last retirement`. */
export function phaseStart(anchor: string, offsetYears: number): string {
  const base = anchor === "start" ? "the projection start" : "the last retirement";
  if (offsetYears === 0) return `From ${base}`;
  return `${offsetYears} ${offsetYears === 1 ? "year" : "years"} after ${base}`;
}

/** The anchors a spending phase can start from, in the order the editor offers them. */
export const ANCHOR_OPTIONS = [
  { value: "last_retirement", label: "The last retirement" },
  { value: "start", label: "The projection start" },
] as const;

/** An age held as exact years and months (Age Pension age has half-year steps): `67 years`, `66 years 6 months`. */
export function formatAge(years: number, months: number): string {
  return months === 0 ? `${years} years` : `${years} years ${months} months`;
}

import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type TaxSetup = components["schemas"]["TaxSetupOut"];
export type TaxLabel = components["schemas"]["TaxLabelOut"];
export type TaxEstimate = components["schemas"]["TaxEstimateOut"];
export type TaxMemberResult = components["schemas"]["TaxMemberOut"];
export type TaxHouseholdResult = components["schemas"]["TaxHouseholdOut"];
export type TaxRecord = components["schemas"]["TaxRecordOut"];
export type TaxPreviousYear = components["schemas"]["TaxPreviousYearOut"];
export type TaxRecordInput = components["schemas"]["TaxRecordIn"];
export type TaxLabelTotal = components["schemas"]["TaxLabelTotalOut"];
export type TaxUncounted = components["schemas"]["TaxUncountedOut"];
export type TaxAsset = components["schemas"]["TaxAssetOut"];
export type TaxAssetYear = components["schemas"]["TaxAssetYearOut"];
export type TaxHomeOffice = components["schemas"]["TaxHomeOfficeOut"];
export type TaxFigureUsed = components["schemas"]["TaxFigureUsedOut"];
export type TaxOperand = components["schemas"]["TaxOperandOut"];
export type TaxStep = components["schemas"]["TaxStepOut"];
export type TaxMonthOut = components["schemas"]["TaxMonthOut"];
export type TaxCar = components["schemas"]["TaxCarOut"];
export type TaxTrip = components["schemas"]["TaxTripOut"];
export type TaxDiv293 = components["schemas"]["TaxDiv293Out"];
export type TaxDiv293Member = components["schemas"]["TaxDiv293MemberOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchTaxSetup(): Promise<TaxSetup> {
  const { data, error, response } = await client.GET("/api/tax/setup");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export type TaxRequest = {
  financialYear: number;
  dependants: string;
  memberIds: number[];
};

/** The request body. Totals are not sent: the server reads them from the Tax records. */
export function estimateBody(request: TaxRequest) {
  const dependants = request.dependants.trim();
  return {
    financial_year: request.financialYear,
    dependants: /^\d+$/.test(dependants) ? Number(dependants) : -1,
    member_ids: request.memberIds,
  };
}

export async function calculateTaxEstimate(
  request: TaxRequest,
): Promise<TaxEstimate> {
  const { data, error, response } = await client.POST("/api/tax/estimate", {
    body: estimateBody(request),
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

// --- the Tax record register ---------------------------------------------------------------

/** What the register is narrowed to. Each field is optional; they narrow together. */
export type TaxRecordFilters = {
  member?: number;
  fy?: number;
  label?: string;
  q?: string;
};

export async function fetchTaxRecords(
  filters: TaxRecordFilters,
): Promise<TaxRecord[]> {
  const { data, error, response } = await client.GET("/api/tax/records", {
    params: {
      query: {
        member_id: filters.member,
        financial_year: filters.fy,
        label: filters.label,
        q: filters.q,
      },
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchTaxRecord(id: number): Promise<TaxRecord> {
  const { data, error, response } = await client.GET(
    "/api/tax/records/{record_id}",
    {
      params: { path: { record_id: id } },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createTaxRecord(
  input: TaxRecordInput,
): Promise<TaxRecord> {
  const { data, error, response } = await client.POST("/api/tax/records", {
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateTaxRecord(
  id: number,
  input: TaxRecordInput,
): Promise<TaxRecord> {
  const { data, error, response } = await client.PUT(
    "/api/tax/records/{record_id}",
    {
      params: { path: { record_id: id } },
      body: input,
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function deleteTaxRecord(id: number): Promise<void> {
  const { error, response } = await client.DELETE(
    "/api/tax/records/{record_id}",
    {
      params: { path: { record_id: id } },
    },
  );
  if (!response.ok) throw toApiError(error, response.status);
}

/** The Library's key for a Tax record, when a document is linked to one. */
export const TAX_RECORD_KIND = "tax_record";

/** Each Member's Label totals for a financial year, computed now, and the records they leave out. */
export async function fetchLabelTotals(
  financialYear: number,
  memberId?: number,
): Promise<{ totals: TaxLabelTotal[]; uncounted: TaxUncounted[] }> {
  const { data, error, response } = await client.GET("/api/tax/label-totals", {
    params: { query: { financial_year: financialYear, member_id: memberId } },
  });
  if (!data) throw toApiError(error, response.status);
  return { totals: data.totals, uncounted: data.uncounted };
}

/** A flag or a number of days in words. (An amount is shown with `<Money>`.) */
export function factText(item: {
  kind: string;
  flag?: boolean | null;
  days?: number | null;
  value?: boolean | number | null;
}): string {
  if (item.kind === "flag")
    return (item.flag ?? item.value) === true ? "Yes" : "No";
  const days = Number(item.days ?? item.value ?? 0);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

/** The dates of a record in words: one day, or a span. */
export function recordDates(
  record: Pick<TaxRecord, "from_on" | "to_on">,
): string {
  return record.to_on && record.to_on !== record.from_on
    ? `${record.from_on} to ${record.to_on}`
    : record.from_on;
}

/**
 * A day or hour count typed as decimal text (at most two decimal places) as whole hundredths, or
 * `null` when it is not one. Integer arithmetic only: the server does the real working.
 */
export function hundredths(text: string): number | null {
  const m = /^([0-9]+)(?:\.([0-9]{1,2}))?$/.exec(text.trim());
  return m ? Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0")) : null;
}

/** Ten-thousandths of an hour as hours: two decimal places, more only when needed. */
export function hoursFromTenThousandths(tenThousandths: number): string {
  const whole = Math.floor(tenThousandths / 10000);
  const digits = String(tenThousandths % 10000).padStart(4, "0");
  return `${whole}.${digits.endsWith("00") ? digits.slice(0, 2) : digits.replace(/0+$/, "")}`;
}

/**
 * A regular worksheet row's hours as the server will work them out, in ten-thousandths of an
 * hour: (working - leave - office days) x hours per day, exact integers. `null` while an entry is
 * incomplete or the home days would be negative.
 */
export function rowTenThousandths(
  perDay: string,
  working: string,
  leave: string,
  office: string,
): number | null {
  const rate = hundredths(perDay);
  const w = hundredths(working);
  const l = hundredths(leave === "" ? "0" : leave);
  const o = hundredths(office === "" ? "0" : office);
  if (rate === null || w === null || l === null || o === null || w - l - o < 0)
    return null;
  return (w - l - o) * rate;
}

/** A regular row's hours as text (see `rowTenThousandths`), or `null` while incomplete. */
export function homeHoursText(
  perDay: string,
  working: string,
  leave: string,
  office: string,
): string | null {
  const tt = rowTenThousandths(perDay, working, leave, office);
  return tt === null ? null : hoursFromTenThousandths(tt);
}

/** A casual row's hours in ten-thousandths (hundredths of an hour x 100), or `null` if not a number. */
export function casualTenThousandths(hours: string): number | null {
  const h = hundredths(hours);
  return h === null ? null : h * 100;
}

/**
 * Total hours x rate, rounded once to the cent (half to even), as dollars text: the server's
 * working, in integers. `rate` is dollars per hour as text (such as "0.70").
 */
export function deductionText(
  rate: string,
  tenThousandths: number,
): string | null {
  const cents = hundredths(rate);
  if (cents === null) return null;
  const numerator = cents * tenThousandths;
  let whole = Math.floor(numerator / 10000);
  const rest = numerator % 10000;
  if (rest > 5000 || (rest === 5000 && whole % 2 === 1)) whole += 1;
  return `${Math.floor(whole / 100)}.${String(whole % 100).padStart(2, "0")}`;
}

/** The most trips one car kilometres record holds (the server refuses more). */
export const MAX_TRIPS = 500;

/**
 * Kilometres of one trip typed as decimal text (at most one decimal place, more than 0) as whole
 * tenths, or `null` when it is not one. Integer arithmetic only: the server does the real working.
 */
export function kmTenths(text: string): number | null {
  const m = /^([0-9]+)(?:\.([0-9]))?$/.exec(text.trim());
  if (!m) return null;
  const value = Number(m[1]) * 10 + Number(m[2] ?? "0");
  return value > 0 ? value : null;
}

/** How many times a trip was made, typed as text: blank is once; otherwise a whole number from 1. */
export function timesCount(text: string): number | null {
  const t = text.trim();
  if (t === "") return 1;
  if (!/^[0-9]+$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= 10000 ? n : null;
}

/** A row's kilometres x times in tenths, or `null` while either is not a valid entry. */
export function tripTenths(km: string, times: string): number | null {
  const k = kmTenths(km);
  const n = timesCount(times);
  return k === null || n === null ? null : k * n;
}

/** Tenths of a kilometre as text with one decimal place: 4719 as "471.9". */
export function kmFromTenths(tenths: number): string {
  return `${Math.floor(tenths / 10)}.${tenths % 10}`;
}

/** Days or hours in hundredths as text: no trailing zeros ("2", "2.5", "2.25"). */
export function hundredthsText(value: number): string {
  const whole = Math.floor(value / 100);
  const part = String(value % 100)
    .padStart(2, "0")
    .replace(/0+$/, "");
  return part === "" ? String(whole) : `${whole}.${part}`;
}

export const WEEKDAYS = [
  { code: "mon", label: "Mon", name: "Monday" },
  { code: "tue", label: "Tue", name: "Tuesday" },
  { code: "wed", label: "Wed", name: "Wednesday" },
  { code: "thu", label: "Thu", name: "Thursday" },
  { code: "fri", label: "Fri", name: "Friday" },
] as const;
export type WeekdayCode = (typeof WEEKDAYS)[number]["code"];

/** How many Mondays to Fridays (each) a `YYYY-MM` month has. Calendar only: public holidays are not known. */
export function weekdayCounts(month: string): Record<WeekdayCode, number> {
  const [y, m] = month.split("-").map(Number);
  const counts: Record<WeekdayCode, number> = {
    mon: 0,
    tue: 0,
    wed: 0,
    thu: 0,
    fri: 0,
  };
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let d = 1; d <= days; d++) {
    const at = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 is Sunday
    if (at >= 1 && at <= 5) counts[WEEKDAYS[at - 1].code] += 1;
  }
  return counts;
}

/** The Monday to Friday days in a `YYYY-MM` month. */
export function weekdaysIn(month: string): number {
  return Object.values(weekdayCounts(month)).reduce((a, b) => a + b, 0);
}

/** The financial year (the year it ends in) an ISO date falls in, or `null` for text that is not a date. */
export function financialYearOfDate(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m || Number.isNaN(Date.parse(iso))) return null;
  return Number(m[2]) >= 7 ? Number(m[1]) + 1 : Number(m[1]);
}

/**
 * The months a home office record covers: one per month from `fromOn` to `toOn`, in order. Empty
 * until both are dates in one financial year with `fromOn` not after `toOn`.
 */
export function monthsInRange(fromOn: string, toOn: string): string[] {
  const fy = financialYearOfDate(fromOn);
  if (fy === null || fy !== financialYearOfDate(toOn) || fromOn > toOn)
    return [];
  const months: string[] = [];
  for (let m = fromOn.slice(0, 7); m <= toOn.slice(0, 7); m = nextMonth(m))
    months.push(m);
  return months;
}

/** How a regular arrangement is described: the weekdays at home, or the office days a month. */
export type HomePattern =
  | { kind: "weekly"; weekdays: readonly string[] }
  | { kind: "monthly"; perMonth: string };

/**
 * The calendar prefill of a regular month: working days are the Mondays to Fridays, office days
 * are the weekdays not worked at home (weekly) or the office days a month capped at the working
 * days (monthly). Leave is left to the household: public holidays are not known.
 */
export function prefillRegular(
  month: string,
  pattern: HomePattern,
): { working: string; office: string } {
  const counts = weekdayCounts(month);
  const working = weekdaysIn(month);
  if (pattern.kind === "weekly") {
    const office = WEEKDAYS.filter(
      (d) => !pattern.weekdays.includes(d.code),
    ).reduce((sum, d) => sum + counts[d.code], 0);
    return { working: String(working), office: String(office) };
  }
  const per = hundredths(pattern.perMonth);
  return {
    working: String(working),
    office: per === null ? "" : hundredthsText(Math.min(per, working * 100)),
  };
}

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** `2025-07` as "July 2025". */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTH_NAMES[m - 1]} ${y}`;
}

/** The month after `YYYY-MM`, as `YYYY-MM`. */
export function nextMonth(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

/** A work-use share in words, or `null` when the whole amount counts. */
export function shareText(percent: string | null | undefined): string | null {
  return percent ? `${percent}% work use` : null;
}

/** The financial year containing today: it ends in the next 30 June (1 July starts a new one). */
export function currentFinancialYear(today = new Date()): number {
  return today.getMonth() >= 6 ? today.getFullYear() + 1 : today.getFullYear();
}

/** `2027` -> `2026-27`. */
export function financialYearLabel(year: number): string {
  return `${year - 1}-${String(year % 100).padStart(2, "0")}`;
}

/** The result lines shown per Member and for the Household, in order. */
export const RESULT_LINES = [
  { key: "taxable_income", label: "Taxable income" },
  { key: "tax_on_taxable_income", label: "Tax on taxable income" },
  { key: "offsets", label: "Offsets" },
  { key: "medicare_levy", label: "Medicare levy" },
  { key: "medicare_levy_surcharge", label: "Medicare levy surcharge" },
  { key: "help_repayment", label: "HELP repayment" },
  { key: "total_tax", label: "Total tax" },
  { key: "credits", label: "Credits" },
  { key: "net_outcome", label: "Net outcome" },
] as const;

/** What this estimate leaves out or assumes. Shown beside every result. */
export const TAX_SIMPLIFICATIONS = [
  "Calculating saves nothing. Its totals come from the Tax records at the moment you calculate; press Save estimate to keep it exactly as it is now, and use a Tax what-if to try a change without touching the records.",
  "Two Members are assessed as a couple for the whole year; the family Medicare levy and surcharge tests need both.",
  "Credits are tax withheld, franking credits and PAYG instalments. Net outcome is credits less total tax: positive is a refund, negative is an amount payable.",
  "Return items the engine does not model are listed under the result and make the estimate incomplete.",
  "Division 293 uses the Super statement's concessional contributions where the year has one that states them, and otherwise the Income stream's employer super (Superannuation Guarantee on the gross). An Income stream counts for the whole year (Life events are not applied). It does not count salary sacrifice into super, carry forward unused cap, or work out excess contributions tax.",
] as const;

/** `/tax?fy=2027`: the financial year is a URL search param, so the screen is linkable. */
export function parseTaxSearch(search: Record<string, unknown>): {
  fy?: number;
} {
  const fy = Number(search.fy);
  return Number.isInteger(fy) && fy >= 1990 && fy <= 2200 ? { fy } : {};
}

/** `/tax/records?member=1&fy=2026&label=salary&q=...`: the register's filters are in the URL. */
export function parseTaxRecordsSearch(
  search: Record<string, unknown>,
): TaxRecordFilters {
  const out: TaxRecordFilters = {};
  const member = Number(search.member);
  if (Number.isInteger(member) && member > 0) out.member = member;
  const fy = Number(search.fy);
  if (Number.isInteger(fy) && fy >= 1990 && fy <= 2200) out.fy = fy;
  if (typeof search.label === "string" && search.label.trim())
    out.label = search.label.trim();
  if (typeof search.q === "string" && search.q.trim()) out.q = search.q.trim();
  return out;
}

// --- Income statement figures ------------------------------------------------------------------

/** One field of an income statement form: money unless `text` is set. */
export type StatementField = {
  key: string;
  name: string;
  text?: boolean;
};

/**
 * The income statement's fields under the ATO statement's own headings, in its order. The last
 * group is ours, not the ATO's: the figures the Income reconciliation needs.
 */
export const STATEMENT_GROUPS: readonly {
  heading: string;
  fields: readonly StatementField[];
}[] = [
  {
    heading: "Employer",
    fields: [{ key: "employer", name: "Employer", text: true }],
  },
  {
    heading: "Income: salary and wages",
    fields: [
      { key: "gross_amount", name: "Gross amount" },
      { key: "bonuses_commissions", name: "Bonuses and commissions" },
      { key: "leave_payment_type", name: "Leave payment type", text: true },
      { key: "paid_leave_amount", name: "Paid leave amount" },
      {
        key: "salary_sacrifice_type",
        name: "Salary sacrifice type",
        text: true,
      },
      { key: "salary_sacrifice_amount", name: "Salary sacrifice amount" },
      { key: "gross_payments", name: "Total gross amount" },
    ],
  },
  {
    heading: "Tax withheld: salary and wages",
    fields: [{ key: "tax_withheld", name: "PAYGW amount" }],
  },
  {
    heading: "Lump sum amounts",
    fields: [
      { key: "lump_sum_a", name: "Lump sum payment A" },
      { key: "lump_sum_b", name: "Lump sum payment B" },
      { key: "lump_sum_d", name: "Lump sum payment D" },
      { key: "lump_sum_e", name: "Lump sum payment E" },
      { key: "lump_sum_w", name: "Lump sum payment W" },
    ],
  },
  {
    heading: "Allowances",
    fields: [{ key: "allowances", name: "Allowances" }],
  },
  {
    heading: "Deductions",
    fields: [{ key: "deductions_total", name: "Deductions total" }],
  },
  {
    heading: "Employer reported super",
    fields: [
      {
        key: "employer_super",
        name: "Employer superannuation contribution liability",
      },
    ],
  },
  {
    heading: "Other amounts",
    fields: [
      {
        key: "reportable_fringe_benefits_taxable",
        name: "Reportable fringe benefits (taxable employer status amount)",
      },
      {
        key: "reportable_employer_super",
        name: "Reportable Employer Super Contribution",
      },
      {
        key: "reportable_fringe_benefits",
        name: "Reportable fringe benefits total",
      },
    ],
  },
  {
    heading: "For the reconciliation",
    fields: [
      { key: "net_pay", name: "Net pay received" },
      {
        key: "novated_lease_post_tax",
        name: "Novated lease costs paid from pay",
      },
      { key: "other_deductions", name: "Other deductions from pay" },
      {
        key: "super_insurance_premiums",
        name: "Insurance premiums paid from the super balance",
      },
    ],
  },
];

/** The money figures of an income statement, flat, in the order shown. */
export const STATEMENT_FIGURES: readonly (readonly [string, string])[] =
  STATEMENT_GROUPS.flatMap((g) => g.fields)
    .filter((f) => !f.text)
    .map((f) => [f.key, f.name] as const);

/** The short text facts of an income statement. */
export const STATEMENT_TEXTS: readonly (readonly [string, string])[] =
  STATEMENT_GROUPS.flatMap((g) => g.fields)
    .filter((f) => f.text)
    .map((f) => [f.key, f.name] as const);

/** The figures a statement must state; the others default to 0 (net pay stays "not stated"). */
export const REQUIRED_STATEMENT_FIGURES: readonly string[] = [
  "gross_payments",
  "tax_withheld",
  "employer_super",
];

// --- Income reconciliation ---------------------------------------------------------------------

export type IncomeReconciliations =
  components["schemas"]["IncomeReconciliationsOut"];
export type IncomeReconciliation =
  components["schemas"]["IncomeReconciliationOut"];
export type IncomeSlice = components["schemas"]["IncomeSliceOut"];

export async function fetchIncomeReconciliation(
  financialYear: number,
): Promise<IncomeReconciliations> {
  const { data, error, response } = await client.GET(
    "/api/tax/income-reconciliation",
    {
      params: { query: { financial_year: financialYear } },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** A share as the API sends it (`"21.2054"`), shown to one decimal place. */
export function shortPercent(percent: string | null | undefined): string {
  if (percent === null || percent === undefined) return "";
  return `${(Math.round(Number(percent) * 10) / 10).toFixed(1)}%`;
}

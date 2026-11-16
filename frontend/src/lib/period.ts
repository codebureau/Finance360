/**
 * Periods (a from date and a to date): financial year <-> dates, and the warnings for a period that
 * looks like a mistake. The Australian financial year runs 1 July to 30 June and is named by the
 * year it ends in (2026 is FY 2025-26). Pure: dates are ISO `YYYY-MM-DD` text, never `Date`
 * objects in local time, so nothing shifts with the time zone.
 */

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Days since the epoch for an ISO date, or `null` for text that is not a real date. */
function dayNumber(iso: string): number | null {
  const m = ISO.exec(iso);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const ms = Date.UTC(y, mo - 1, d);
  const check = new Date(ms);
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d) return null;
  return ms / 86_400_000;
}

/** The financial year (the year it ends in) a date falls in, or `null` for text that is not a date. */
export function yearOfDate(iso: string): number | null {
  if (dayNumber(iso) === null) return null;
  const m = ISO.exec(iso) as RegExpExecArray;
  return Number(m[2]) >= 7 ? Number(m[1]) + 1 : Number(m[1]);
}

/** The first and last day of a financial year: 2026 is 2025-07-01 and 2026-06-30. */
export function yearDates(year: number): { from: string; to: string } {
  return { from: `${year - 1}-07-01`, to: `${year}-06-30` };
}

/** The financial year the dates are exactly (1 July to 30 June), else `null`. */
export function exactYear(from: string, to: string): number | null {
  const year = yearOfDate(to);
  if (year === null) return null;
  const dates = yearDates(year);
  return from === dates.from && to === dates.to ? year : null;
}

/** `2025-07-01` -> `1 Jul 2025`. Text that is not a date is returned as it is. */
export function formatDay(iso: string): string {
  const m = ISO.exec(iso);
  if (!m || dayNumber(iso) === null) return iso;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** `2026` -> `1 Jul 2025 to 30 Jun 2026`. */
export function yearRangeText(year: number): string {
  const { from, to } = yearDates(year);
  return `${formatDay(from)} to ${formatDay(to)}`;
}

/** The day after adding a year, less a day: the last day of a period that starts on `from` and lasts a year. */
function lastDayOfYearFrom(from: string): number | null {
  const m = ISO.exec(from);
  if (!m || dayNumber(from) === null) return null;
  const next = dayNumber(`${Number(m[1]) + 1}-${m[2]}-${m[3]}`);
  // 29 February has no counterpart next year: a year from it is 28 February's day after, less one.
  const anniversary = next ?? (dayNumber(`${Number(m[1]) + 1}-03-01`) as number);
  return anniversary - 1;
}

/** Labels for the financial years a period touches, such as `FY 2024-25 and FY 2025-26`. */
function yearsTouched(first: number, last: number, label: (year: number) => string): string {
  return first === last ? `FY ${label(first)}` : `FY ${label(first)} and FY ${label(last)}`;
}

export type PeriodWarning = "end_before_start" | "crosses_30_june" | "longer_than_a_year";

/**
 * What looks wrong with a period, in the order to say it. Warns, never blocks: a blank end is fine
 * (the record is for one day), and an incomplete or unreadable date is left to the server's message.
 */
export function periodWarnings(from: string, to: string): PeriodWarning[] {
  const start = dayNumber(from);
  const end = dayNumber(to);
  if (start === null || end === null) return [];
  if (end < start) return ["end_before_start"];
  const out: PeriodWarning[] = [];
  if (yearOfDate(from) !== yearOfDate(to)) out.push("crosses_30_june");
  const limit = lastDayOfYearFrom(from);
  if (limit !== null && end > limit) out.push("longer_than_a_year");
  return out;
}

/** The sentence for a warning. `label` names a year (the form passes `financialYearLabel`). */
export function periodWarningText(
  warning: PeriodWarning,
  from: string,
  to: string,
  label: (year: number) => string,
): string {
  switch (warning) {
    case "end_before_start":
      return "The end is before the start.";
    case "crosses_30_june":
      return `This period crosses 30 June, so it spans two financial years (${yearsTouched(
        yearOfDate(from) as number,
        yearOfDate(to) as number,
        label,
      )}). If it is meant to be one year, pick a financial year.`;
    case "longer_than_a_year":
      return "This period is longer than a year.";
  }
}

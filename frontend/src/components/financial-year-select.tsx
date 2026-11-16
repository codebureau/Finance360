import { inputClass } from "@/components/form-fields";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";

/** How many years before the current one a year list offers. */
const YEARS_BACK = 9;

/**
 * The financial years to offer, newest first: the current one and the nine before it, plus any of
 * `extra` (the years a Rule pack holds, a year already in the address) outside that range.
 */
export function financialYears(extra: readonly (number | undefined)[] = [], today = new Date()): number[] {
  const current = currentFinancialYear(today);
  const years = new Set<number>();
  for (let y = current; y >= current - YEARS_BACK; y--) years.add(y);
  for (const y of extra) if (y !== undefined) years.add(y);
  return [...years].sort((a, b) => b - a);
}

/**
 * A financial year picked from a list ("FY 2025-26"), never typed. `allLabel` adds a first choice
 * for no year at all, which hands back `undefined`.
 */
export function FinancialYearSelect({
  value,
  onChange,
  years,
  allLabel,
  describedBy,
}: {
  value: number | undefined;
  onChange: (year: number | undefined) => void;
  years: readonly number[];
  allLabel?: string;
  describedBy?: string;
}) {
  return (
    <select
      className={inputClass}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      aria-describedby={describedBy}
    >
      {allLabel !== undefined && <option value="">{allLabel}</option>}
      {years.map((y) => (
        <option key={y} value={y}>
          FY {financialYearLabel(y)}
        </option>
      ))}
    </select>
  );
}

import { useState, type ReactNode } from "react";

import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { FieldMessage, inputClass } from "@/components/form-fields";
import {
  exactYear,
  periodWarnings,
  periodWarningText,
  yearDates,
  yearOfDate,
  yearRangeText,
  type PeriodWarning,
} from "@/lib/period";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";

/** The dates a new record starts on: the financial year it is being entered for, else the current one. */
export function defaultPeriod(year?: number): { from: string; to: string } {
  return yearDates(year ?? currentFinancialYear());
}

/** The sentences for a period's warnings, for the form and for the list's marker. */
export function periodWarningTexts(from: string, to: string, only?: readonly PeriodWarning[]): string[] {
  return periodWarnings(from, to)
    .filter((w) => !only || only.includes(w))
    .map((w) => periodWarningText(w, from, to, financialYearLabel));
}

/**
 * A period: a financial year picked from a list (which is the dates, 1 July to 30 June, shown beside
 * the choice, nothing typed), or **Custom dates** for part of a year. The form keeps `from` and `to`
 * as ISO date text; the API still takes `from_on` and `to_on`.
 *
 * * An existing period opens as a financial year when its dates are exactly one, else as custom.
 * * Switching to custom keeps the dates, so only the one that changes is edited; switching back to
 *   a year sets both from it, and switching to custom again restores what had been edited.
 * * `allowBlank` (an income stream): both dates blank starts as custom, and a blank date is allowed.
 * * Warnings (an end before the start, crossing 30 June, longer than a year) show here and never
 *   block saving. `warn` limits which are shown.
 */
export function PeriodField({
  from,
  to,
  onChange,
  defaultYear,
  fromLabel = "From",
  toLabel = "To",
  fromHint,
  toHint,
  allowBlank = false,
  warn,
  error,
  legend = "Period",
}: {
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  /** The year to offer first (the one the screen shows), else the year of the dates, else the current one. */
  defaultYear?: number;
  fromLabel?: string;
  toLabel?: string;
  fromHint?: ReactNode;
  toHint?: ReactNode;
  allowBlank?: boolean;
  warn?: readonly PeriodWarning[];
  error?: unknown;
  legend?: string;
}) {
  const [custom, setCustom] = useState(() =>
    from === "" && to === "" ? allowBlank : exactYear(from, to) === null,
  );
  const [year, setYear] = useState(() => yearOfDate(to || from) ?? defaultYear ?? currentFinancialYear());
  // The dates as last edited under Custom dates, restored when the switch is turned on again.
  const [kept, setKept] = useState<{ from: string; to: string } | null>(null);

  const shown = exactYear(from, to) ?? year;
  const years = financialYears([shown, defaultYear, yearOfDate(from) ?? undefined, yearOfDate(to) ?? undefined]);
  const warnings = periodWarningTexts(from, to, warn);

  function toggle(on: boolean) {
    if (on) {
      setCustom(true);
      if (kept) onChange(kept.from, kept.to);
      return;
    }
    setKept({ from, to });
    const next = exactYear(from, to) ?? yearOfDate(to || from) ?? year;
    setYear(next);
    setCustom(false);
    const dates = yearDates(next);
    onChange(dates.from, dates.to);
  }

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      {custom ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            {fromLabel}
            <input
              className={inputClass}
              type="date"
              value={from}
              onChange={(e) => onChange(e.target.value, to)}
            />
            {fromHint && <span className="text-xs text-muted-foreground">{fromHint}</span>}
            <FieldMessage error={error} field="from_on" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {toLabel}
            <input
              className={inputClass}
              type="date"
              value={to}
              onChange={(e) => onChange(from, e.target.value)}
            />
            {toHint && <span className="text-xs text-muted-foreground">{toHint}</span>}
            <FieldMessage error={error} field="to_on" />
          </label>
        </div>
      ) : (
        <div className="flex flex-col gap-1 text-sm">
          <label className="flex flex-col gap-1">
            Financial year
            <FinancialYearSelect
              value={shown}
              years={years}
              describedBy="period-range"
              onChange={(next) => {
                if (next === undefined) return;
                setYear(next);
                const dates = yearDates(next);
                onChange(dates.from, dates.to);
              }}
            />
          </label>
          <span id="period-range" className="text-xs text-muted-foreground">
            {yearRangeText(shown)}
          </span>
          <FieldMessage error={error} field="from_on" />
          <FieldMessage error={error} field="to_on" />
        </div>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={custom} onChange={(e) => toggle(e.target.checked)} />
        Custom dates
      </label>
      {warnings.map((text) => (
        <div
          key={text}
          role="note"
          data-testid="period-warning"
          className="rounded-md border border-warning bg-warning/10 p-3 text-sm"
        >
          {text} You can still save.
        </div>
      ))}
    </fieldset>
  );
}

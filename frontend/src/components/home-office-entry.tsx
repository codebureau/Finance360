import { useState, type ReactNode } from "react";

import { FieldMessage, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Money } from "@/components/ui/money";
import { ApiError } from "@/lib/household";
import {
  casualTenThousandths,
  deductionText,
  financialYearOfDate,
  homeHoursText,
  hoursFromTenThousandths,
  monthLabel,
  monthsInRange,
  prefillRegular,
  rowTenThousandths,
  WEEKDAYS,
  type HomePattern,
  type TaxHomeOffice,
  type TaxRecordInput,
} from "@/lib/tax";

type Arrangement = "regular" | "casual";

/** One month as typed. A month the person has not edited is not stored: it follows the pattern. */
type MonthEntry = {
  working: string;
  leave: string;
  office: string;
  /** Casual only: the actual hours worked from home. */
  hours: string;
};

const dayInputClass = `${inputClass} w-20 px-2 text-right font-mono`;
const ROW_FIELDS = [
  "month",
  "working_days",
  "leave_days",
  "office_days",
  "home_days",
  "hours",
];

/** "4 days a week at home". */
export function weekdaySummary(count: number): string {
  if (count === 0) return "No weekdays chosen";
  return `${count} ${count === 1 ? "day" : "days"} a week at home`;
}

/**
 * The state and fields of a home office record's entry: the arrangement, its pattern and one row
 * per month from `fromOn` to `toOn`. The rows follow the dates; a regular month the person has not
 * edited follows the pattern (so changing the weekdays re-prefills it), and an edited one keeps
 * what was typed. Nothing here is saved until the form is: the server works the figures out again.
 */
export function useHomeOffice(
  initial: TaxHomeOffice | undefined,
  fromOn: string,
  toOn: string,
) {
  const [arrangement, setArrangement] = useState<Arrangement>(
    initial?.arrangement === "casual" ? "casual" : "regular",
  );
  const [perDay, setPerDay] = useState(initial?.hours_per_day ?? "");
  const [kind, setKind] = useState<HomePattern["kind"]>(
    initial?.office_days_per_month != null ? "monthly" : "weekly",
  );
  const [weekdays, setWeekdays] = useState<string[]>(
    initial ? (initial.home_weekdays ?? []) : WEEKDAYS.map((d) => d.code),
  );
  const [perMonth, setPerMonth] = useState(
    initial?.office_days_per_month ?? "",
  );
  // Every month of a saved record counts as edited, so a pattern change never overwrites it.
  const [edited, setEdited] = useState<Record<string, MonthEntry>>(() =>
    Object.fromEntries(
      (initial?.months ?? []).map((m) => [
        m.month,
        {
          working: m.working_days ?? "",
          leave: m.leave_days ?? "",
          office: m.office_days ?? "",
          hours: initial?.arrangement === "casual" ? m.hours : "",
        },
      ]),
    ),
  );
  const [removed, setRemoved] = useState<string[]>(() => {
    const kept = new Set((initial?.months ?? []).map((m) => m.month));
    return initial
      ? monthsInRange(fromOn, toOn).filter((m) => !kept.has(m))
      : [];
  });

  const pattern: HomePattern =
    kind === "weekly"
      ? { kind: "weekly", weekdays }
      : { kind: "monthly", perMonth };
  const range = monthsInRange(fromOn, toOn);
  const shown = range.filter((m) => !removed.includes(m));
  const missing = range.filter((m) => removed.includes(m));

  const entryFor = (month: string): MonthEntry =>
    edited[month] ?? {
      ...prefillRegular(month, pattern),
      leave: "",
      hours: "",
    };
  const edit = (month: string, patch: Partial<MonthEntry>) =>
    setEdited((all) => ({ ...all, [month]: { ...entryFor(month), ...patch } }));

  /** The months that are sent: a blank casual month is left out and counts as nothing. */
  const sent =
    arrangement === "casual"
      ? shown.filter((m) => entryFor(m).hours.trim() !== "")
      : shown;

  let totalTenThousandths = 0;
  for (const m of sent) {
    const e = entryFor(m);
    totalTenThousandths +=
      (arrangement === "casual"
        ? casualTenThousandths(e.hours.trim())
        : rowTenThousandths(perDay, e.working, e.leave, e.office)) ?? 0;
  }

  return {
    arrangement,
    /** Put the entry into the request the form sends. */
    apply(input: TaxRecordInput) {
      input.arrangement = arrangement;
      if (arrangement === "casual") {
        input.months = sent.map((m) => ({
          month: m,
          working_days: "",
          leave_days: "",
          office_days: "",
          hours: entryFor(m).hours.trim(),
        }));
        return;
      }
      input.hours_per_day = perDay.trim();
      if (kind === "weekly")
        input.home_weekdays = WEEKDAYS.map((d) => d.code).filter((c) =>
          weekdays.includes(c),
        );
      else input.office_days_per_month = perMonth.trim();
      input.months = sent.map((m) => {
        const e = entryFor(m);
        return {
          month: m,
          working_days: e.working.trim(),
          leave_days: e.leave.trim(),
          office_days: e.office.trim(),
          hours: "",
        };
      });
    },
    /** The fields: arrangement, pattern, month table and totals. */
    fields(
      error: unknown,
      saved?: { rate: string | null; year: number | null },
    ): ReactNode {
      const hasRange = range.length > 0;
      const year = financialYearOfDate(fromOn);
      const deduction =
        saved?.rate && saved.year !== null && saved.year === year
          ? deductionText(saved.rate, totalTenThousandths)
          : null;
      const fieldErrors = sent.flatMap((m, i) =>
        ROW_FIELDS.map((f) => {
          const message =
            error instanceof ApiError
              ? error.fields[`months.${i}.${f}`]
              : undefined;
          return message ? (
            <p key={`${m}-${f}`} className="text-sm text-destructive">
              {message.startsWith(monthLabel(m))
                ? message
                : `${monthLabel(m)}: ${message}`}
            </p>
          ) : null;
        }),
      );
      return (
        <>
          <p className="text-xs text-muted-foreground">
            One record covers one financial year (1 July to 30 June); enter
            another for the next year. The rate per hour comes from the Rule
            pack. If you only have a total, enter it as an amount instead.
          </p>
          <fieldset className="flex min-w-0 flex-col gap-2 text-sm">
            <legend className="mb-1 font-medium">How you work from home</legend>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="home-arrangement"
                className="mt-1"
                checked={arrangement === "regular"}
                onChange={() => setArrangement("regular")}
              />
              <span>
                Regular arrangement
                <span className="block text-xs text-muted-foreground">
                  A settled pattern, such as the same days each week. Each month
                  starts from the calendar and you adjust it.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="home-arrangement"
                className="mt-1"
                checked={arrangement === "casual"}
                onChange={() => setArrangement("casual")}
              />
              <span>
                Casual
                <span className="block text-xs text-muted-foreground">
                  No pattern: enter the hours you actually worked from home each
                  month.
                </span>
              </span>
            </label>
            <FieldMessage error={error} field="arrangement" />
          </fieldset>
          {arrangement === "regular" && (
            <>
              <label className="flex max-w-xs flex-col gap-1 text-sm">
                Hours per day
                <input
                  className={`${inputClass} text-right font-mono`}
                  inputMode="decimal"
                  placeholder="7.6"
                  value={perDay}
                  onChange={(e) => setPerDay(e.target.value)}
                />
                <FieldMessage error={error} field="hours_per_day" />
              </label>
              <fieldset className="flex min-w-0 flex-col gap-2 text-sm">
                <legend className="mb-1 font-medium">Your pattern</legend>
                <div className="flex flex-wrap gap-4">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="home-pattern"
                      checked={kind === "weekly"}
                      onChange={() => setKind("weekly")}
                    />
                    Weekdays at home
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="home-pattern"
                      checked={kind === "monthly"}
                      onChange={() => setKind("monthly")}
                    />
                    Office days a month
                  </label>
                </div>
                {kind === "weekly" ? (
                  <>
                    <div
                      className="flex flex-wrap gap-3"
                      role="group"
                      aria-label="Weekdays at home"
                    >
                      {WEEKDAYS.map((d) => (
                        <label
                          key={d.code}
                          className="flex items-center gap-1.5"
                        >
                          <input
                            type="checkbox"
                            aria-label={d.name}
                            checked={weekdays.includes(d.code)}
                            onChange={(e) =>
                              setWeekdays(
                                e.target.checked
                                  ? [...weekdays, d.code]
                                  : weekdays.filter((c) => c !== d.code),
                              )
                            }
                          />
                          {d.label}
                        </label>
                      ))}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {weekdaySummary(weekdays.length)}. The other weekdays are
                      prefilled as office days.
                    </p>
                    <FieldMessage error={error} field="home_weekdays" />
                  </>
                ) : (
                  <label className="flex flex-col gap-1">
                    Office days a month
                    <input
                      className={`${inputClass} w-28 text-right font-mono`}
                      inputMode="decimal"
                      placeholder="2"
                      value={perMonth}
                      onChange={(e) => setPerMonth(e.target.value)}
                    />
                    <span className="text-xs text-muted-foreground">
                      Prefilled into every month you have not edited, never more
                      than the month's working days. Everything else is home.
                    </span>
                    <FieldMessage error={error} field="office_days_per_month" />
                  </label>
                )}
              </fieldset>
            </>
          )}
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Months</legend>
            <p className="text-xs text-muted-foreground">
              {arrangement === "casual"
                ? "One row for each month from your From date to your To date. A month left blank is not saved and counts as nothing."
                : "One row for each month from your From date to your To date, prefilled from the calendar and your pattern. Public holidays are not known: enter them as leave. Home days are the working days less leave and office days; hours are home days times the hours per day. Change any month and it keeps what you typed."}
            </p>
            {!hasRange ? (
              <p className="text-sm text-muted-foreground">
                Choose a From and To date in one financial year to lay out the
                months.
              </p>
            ) : (
              <div className="overflow-x-auto" data-testid="month-table">
                <table className="w-auto min-w-[30rem] text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                      <th className="pr-2 font-normal">Month</th>
                      {arrangement === "regular" && (
                        <>
                          <th className="pr-2 text-right font-normal">
                            Working days
                          </th>
                          <th className="pr-2 text-right font-normal">
                            Leave days
                          </th>
                          <th className="pr-2 text-right font-normal">
                            Office days
                          </th>
                        </>
                      )}
                      <th className="pr-2 text-right font-normal">Hours</th>
                      <th className="font-normal">
                        <span className="sr-only">Remove</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((m) => {
                      const e = entryFor(m);
                      const name = monthLabel(m);
                      const hours =
                        arrangement === "regular"
                          ? homeHoursText(perDay, e.working, e.leave, e.office)
                          : null;
                      return (
                        <tr key={m} className="align-top">
                          <td className="py-1 pr-2 whitespace-nowrap">
                            <span className="inline-block leading-[var(--control-h)]">
                              {name}
                            </span>
                          </td>
                          {arrangement === "regular" ? (
                            <>
                              {(
                                [
                                  ["working", "Working days"],
                                  ["leave", "Leave days"],
                                  ["office", "Office days"],
                                ] as const
                              ).map(([key, label]) => (
                                <td key={key} className="py-1 pr-2 text-right">
                                  <input
                                    className={dayInputClass}
                                    inputMode="decimal"
                                    aria-label={`${label}, ${name}`}
                                    value={e[key]}
                                    onChange={(ev) =>
                                      edit(m, { [key]: ev.target.value })
                                    }
                                  />
                                </td>
                              ))}
                              <td
                                className="py-1 pr-2 text-right font-mono"
                                aria-label={`Hours, ${name}`}
                              >
                                <span className="inline-block min-h-[var(--control-h)] leading-[var(--control-h)]">
                                  {hours ?? "–"}
                                </span>
                              </td>
                            </>
                          ) : (
                            <td className="py-1 pr-2 text-right">
                              <input
                                className={dayInputClass}
                                inputMode="decimal"
                                aria-label={`Hours, ${name}`}
                                value={e.hours}
                                onChange={(ev) =>
                                  edit(m, { hours: ev.target.value })
                                }
                              />
                            </td>
                          )}
                          <td className="py-1">
                            <Button
                              type="button"
                              variant="ghost"
                              aria-label={`Remove ${name}`}
                              onClick={() => setRemoved((all) => [...all, m])}
                            >
                              Remove
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {fieldErrors}
            <FieldMessage error={error} field="months" />
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="outline"
                disabled={missing.length === 0}
                onClick={() =>
                  setRemoved((all) => all.filter((m) => m !== missing[0]))
                }
              >
                Add month
              </Button>
              <span className="text-xs text-muted-foreground">
                Brings back a month you removed, within your From and To dates.
              </span>
            </div>
            {hasRange && (
              <dl
                className="grid max-w-sm gap-1 text-sm"
                aria-label="Worksheet totals"
              >
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Total hours</dt>
                  <dd className="font-mono">
                    {hoursFromTenThousandths(totalTenThousandths)}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Deduction</dt>
                  <dd>
                    {deduction !== null ? (
                      <Money amount={deduction} />
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        Worked out on save from the Rule pack rate
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
            )}
          </fieldset>
        </>
      );
    },
  };
}

import { useRef, useState, type ReactNode } from "react";

import { FieldMessage, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/household";
import {
  financialYearOfDate,
  kmFromTenths,
  MAX_TRIPS,
  tripTenths,
  type TaxCar,
  type TaxRecordInput,
} from "@/lib/tax";

/** One trip as typed. `key` only identifies the row while it is on screen. */
type TripEntry = {
  key: number;
  description: string;
  km: string;
  times: string;
  on: string;
};

const ROW_FIELDS = ["description", "km", "times", "on"];
const FIELD_NAMES: Record<string, string> = {
  description: "Description",
  km: "Kilometres",
  times: "Times",
  on: "Date",
};

const cell = "px-1 py-1 align-top";
const numberInput = `${inputClass} px-2 text-right font-mono`;

function isBlank(t: TripEntry): boolean {
  return (
    t.description.trim() === "" &&
    t.km.trim() === "" &&
    (t.times.trim() === "" || t.times.trim() === "1") &&
    t.on === ""
  );
}

/**
 * The state and fields of a car kilometres record's entry: a table of trips, each with a
 * description, the kilometres of one trip, how many times it was made and an optional date. A row
 * that is completely blank is left out when saved. Nothing here is saved until the form is: the
 * server works the limit, the rate and the deduction out again from exact values.
 */
export function useCarKilometres(initial: TaxCar | undefined) {
  const next = useRef(0);
  const blank = (): TripEntry => ({
    key: next.current++,
    description: "",
    km: "",
    times: "1",
    on: "",
  });
  const [trips, setTrips] = useState<TripEntry[]>(() =>
    initial && initial.trips.length > 0
      ? initial.trips.map((t) => ({
          key: next.current++,
          description: t.description,
          km: t.km,
          times: String(t.times),
          on: t.on ?? "",
        }))
      : [blank()],
  );
  const edit = (key: number, patch: Partial<TripEntry>) =>
    setTrips((all) => all.map((t) => (t.key === key ? { ...t, ...patch } : t)));

  /** The rows that are sent: a blank row is left out. */
  const sent = trips.filter((t) => !isBlank(t));
  let totalTenths = 0;
  for (const t of sent) totalTenths += tripTenths(t.km, t.times) ?? 0;

  return {
    /** Put the entry into the request the form sends. */
    apply(input: TaxRecordInput) {
      input.trips = sent.map((t) => ({
        description: t.description.trim(),
        km: t.km.trim(),
        times: t.times.trim() === "" ? "1" : t.times.trim(),
        on: t.on,
      }));
    },
    /** The fields: the explanation, the trips table and the running total. */
    fields(
      error: unknown,
      range: { fromOn: string; toOn: string },
      saved?: { limit: number | null; year: number | null },
    ): ReactNode {
      const year = financialYearOfDate(range.fromOn);
      const limit =
        saved?.limit != null && saved.year !== null && saved.year === year
          ? saved.limit
          : null;
      const counted =
        limit !== null ? Math.min(totalTenths, limit * 10) : totalTenths;
      const fieldErrors = sent.flatMap((_, i) =>
        ROW_FIELDS.map((f) => {
          const message =
            error instanceof ApiError
              ? error.fields[`trips.${i}.${f}`]
              : undefined;
          return message ? (
            <p key={`${i}-${f}`} className="text-sm text-destructive">
              {`Trip ${i + 1}, ${FIELD_NAMES[f].toLowerCase()}: ${message}`}
            </p>
          ) : null;
        }),
      );
      return (
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Trips</legend>
          <p className="text-xs text-muted-foreground">
            One record covers one financial year (1 July to 30 June) and a
            Member has one for each year; add every work trip to it. Enter the
            kilometres of one trip and how many times you made it. The rate per
            kilometre and the limit per car come from the Rule pack, and only
            work kilometres belong here.
          </p>
          <div className="overflow-x-auto" data-testid="trips-table">
            <table className="w-auto min-w-[44rem] text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className={`${cell} font-normal`}>Description</th>
                  <th className={`${cell} text-right font-normal`}>km</th>
                  <th className={`${cell} text-right font-normal`}>Times</th>
                  <th className={`${cell} font-normal`}>Date (optional)</th>
                  <th className={`${cell} text-right font-normal`}>Total km</th>
                  <th className={`${cell} font-normal`}>
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {trips.map((t, i) => {
                  const total = tripTenths(t.km, t.times);
                  const name = `trip ${i + 1}`;
                  return (
                    <tr key={t.key}>
                      <td className={cell}>
                        <input
                          className={`${inputClass} w-64`}
                          aria-label={`Description, ${name}`}
                          placeholder="Where to or from, and why"
                          value={t.description}
                          onChange={(e) =>
                            edit(t.key, { description: e.target.value })
                          }
                        />
                      </td>
                      <td className={cell}>
                        <input
                          className={`${numberInput} w-24`}
                          inputMode="decimal"
                          aria-label={`Kilometres, ${name}`}
                          value={t.km}
                          onChange={(e) => edit(t.key, { km: e.target.value })}
                        />
                      </td>
                      <td className={cell}>
                        <input
                          className={`${numberInput} w-20`}
                          inputMode="numeric"
                          aria-label={`Times, ${name}`}
                          value={t.times}
                          onChange={(e) =>
                            edit(t.key, { times: e.target.value })
                          }
                        />
                      </td>
                      <td className={cell}>
                        <input
                          className={`${inputClass} w-40`}
                          type="date"
                          aria-label={`Date, ${name}`}
                          min={range.fromOn || undefined}
                          max={range.toOn || undefined}
                          value={t.on}
                          onChange={(e) => edit(t.key, { on: e.target.value })}
                        />
                      </td>
                      <td
                        className={`${cell} text-right font-mono`}
                        aria-label={`Total km, ${name}`}
                      >
                        <span className="inline-block min-h-[var(--control-h)] leading-[var(--control-h)]">
                          {total !== null ? kmFromTenths(total) : "–"}
                        </span>
                      </td>
                      <td className={cell}>
                        <Button
                          type="button"
                          variant="ghost"
                          aria-label={`Remove ${name}`}
                          onClick={() =>
                            setTrips((all) =>
                              all.filter((x) => x.key !== t.key),
                            )
                          }
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
          {fieldErrors}
          <FieldMessage error={error} field="trips" />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={trips.length >= MAX_TRIPS}
              onClick={() => setTrips((all) => [...all, blank()])}
            >
              Add trip
            </Button>
            {trips.length >= MAX_TRIPS && (
              <span className="text-xs text-muted-foreground">
                A record holds at most {MAX_TRIPS} trips.
              </span>
            )}
          </div>
          <dl className="grid max-w-sm gap-1 text-sm" aria-label="Trip totals">
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Total km</dt>
              <dd className="font-mono">{kmFromTenths(totalTenths)} km</dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground" role="status">
            {limit === null
              ? "The limit and the deduction are worked out on save from the Rule pack."
              : totalTenths > limit * 10
                ? `${kmFromTenths(totalTenths)} km is above the limit of ${limit} km, so ${kmFromTenths(counted)} km count.`
                : `Within the limit of ${limit} km, so every kilometre counts.`}
          </p>
        </fieldset>
      );
    },
  };
}

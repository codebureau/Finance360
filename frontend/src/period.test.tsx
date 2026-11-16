import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";

import { PeriodField } from "@/components/period-field";
import { exactYear, formatDay, periodWarnings, yearDates, yearOfDate } from "@/lib/period";

// --- the pure functions -----------------------------------------------------------------------

test("a financial year is its dates, and exactly those dates are a financial year", () => {
  expect(yearDates(2026)).toEqual({ from: "2025-07-01", to: "2026-06-30" });
  expect(exactYear("2025-07-01", "2026-06-30")).toBe(2026);
  expect(exactYear("2025-06-01", "2026-06-30")).toBeNull(); // a month early
  expect(exactYear("2025-07-01", "2026-06-29")).toBeNull(); // a day short
  expect(exactYear("2025-07-01", "")).toBeNull();
  expect(yearOfDate("2026-06-30")).toBe(2026);
  expect(yearOfDate("2026-07-01")).toBe(2027);
  expect(yearOfDate("2026-02-30")).toBeNull(); // not a real day
  expect(formatDay("2025-07-01")).toBe("1 Jul 2025");
});

test("the warnings: an end before the start, crossing 30 June, longer than a year", () => {
  expect(periodWarnings("2025-07-01", "2026-06-30")).toEqual([]); // a whole year
  expect(periodWarnings("2025-09-01", "2026-02-28")).toEqual([]); // part of a year
  expect(periodWarnings("2025-07-10", "")).toEqual([]); // one day
  expect(periodWarnings("2026-03-01", "2026-02-01")).toEqual(["end_before_start"]);
  expect(periodWarnings("2026-01-01", "2026-08-01")).toEqual(["crosses_30_june"]);
  // the mistake that was saved: 1 June 2025 to 30 June 2026 crosses 30 June and is 13 months
  expect(periodWarnings("2025-06-01", "2026-06-30")).toEqual(["crosses_30_june", "longer_than_a_year"]);
  // a year to the day is not longer than a year; one day more is
  expect(periodWarnings("2025-01-01", "2025-12-31")).toContain("crosses_30_june");
  expect(periodWarnings("2025-01-01", "2025-12-31")).not.toContain("longer_than_a_year");
  expect(periodWarnings("2025-01-01", "2026-01-01")).toContain("longer_than_a_year");
  expect(periodWarnings("2024-02-29", "2025-02-28")).not.toContain("longer_than_a_year");
  expect(periodWarnings("2024-02-29", "2025-03-01")).toContain("longer_than_a_year");
  expect(periodWarnings("not a date", "2026-06-30")).toEqual([]); // left to the server's message
});

// --- the component ----------------------------------------------------------------------------

function Harness({
  from,
  to,
  allowBlank = false,
  defaultYear,
}: {
  from: string;
  to: string;
  allowBlank?: boolean;
  defaultYear?: number;
}) {
  const [dates, setDates] = useState({ from, to });
  return (
    <form aria-label="harness">
      <PeriodField
        from={dates.from}
        to={dates.to}
        onChange={(f, t) => setDates({ from: f, to: t })}
        allowBlank={allowBlank}
        defaultYear={defaultYear}
        fromLabel="Start"
        toLabel="End"
      />
      <output aria-label="dates">{`${dates.from}|${dates.to}`}</output>
    </form>
  );
}

function dates(): string {
  return screen.getByLabelText("dates").textContent as string;
}

test("picking a financial year sets both dates and shows them beside the choice", () => {
  render(<Harness from="2025-07-01" to="2026-06-30" />);
  const form = screen.getByRole("form", { name: "harness" });
  expect(within(form).getByLabelText("Financial year")).toHaveValue("2026");
  expect(within(form).getByText("1 Jul 2025 to 30 Jun 2026")).toBeInTheDocument();
  expect(within(form).queryByLabelText("Start")).not.toBeInTheDocument(); // nothing to type
  fireEvent.change(within(form).getByLabelText("Financial year"), { target: { value: "2025" } });
  expect(dates()).toBe("2024-07-01|2025-06-30");
  expect(within(form).getByText("1 Jul 2024 to 30 Jun 2025")).toBeInTheDocument();
});

test("dates that are exactly one year open as that year; any others open as custom dates", () => {
  const { unmount } = render(<Harness from="2025-07-01" to="2026-06-30" />);
  expect(screen.getByLabelText("Custom dates")).not.toBeChecked();
  unmount();
  render(<Harness from="2025-09-01" to="2026-02-28" />);
  expect(screen.getByLabelText("Custom dates")).toBeChecked();
  expect(screen.getByLabelText("Start")).toHaveValue("2025-09-01");
  expect(screen.getByLabelText("End")).toHaveValue("2026-02-28");
});

test("Custom dates keeps the year's dates so only the changed one is edited, and keeps the edit", () => {
  render(<Harness from="2025-07-01" to="2026-06-30" />);
  fireEvent.click(screen.getByLabelText("Custom dates"));
  expect(screen.getByLabelText("Start")).toHaveValue("2025-07-01"); // prefilled from the year
  expect(screen.getByLabelText("End")).toHaveValue("2026-06-30");
  fireEvent.change(screen.getByLabelText("End"), { target: { value: "2026-03-31" } });
  expect(dates()).toBe("2025-07-01|2026-03-31");
  // back to a year, then to custom again: the edit is still there
  fireEvent.click(screen.getByLabelText("Custom dates"));
  expect(dates()).toBe("2025-07-01|2026-06-30");
  fireEvent.click(screen.getByLabelText("Custom dates"));
  expect(screen.getByLabelText("End")).toHaveValue("2026-03-31");
  expect(dates()).toBe("2025-07-01|2026-03-31");
});

test("a period that crosses 30 June, ends before it starts or runs over a year warns, and never blocks", () => {
  render(<Harness from="2025-06-01" to="2026-06-30" />);
  const notes = screen.getAllByTestId("period-warning");
  expect(notes).toHaveLength(2);
  expect(notes[0]).toHaveTextContent("crosses 30 June");
  expect(notes[0]).toHaveTextContent("FY 2024-25 and FY 2025-26");
  expect(notes[1]).toHaveTextContent("longer than a year");
  expect(notes[0]).toHaveTextContent("You can still save");
  fireEvent.change(screen.getByLabelText("End"), { target: { value: "2025-05-01" } });
  expect(screen.getAllByTestId("period-warning")).toHaveLength(1);
  expect(screen.getByTestId("period-warning")).toHaveTextContent("The end is before the start");
  fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2025-07-01" } });
  fireEvent.change(screen.getByLabelText("End"), { target: { value: "2026-06-30" } });
  expect(screen.queryByTestId("period-warning")).not.toBeInTheDocument();
});

test("with blank dates allowed (an income stream) both blank starts as custom, and the year is a shortcut", () => {
  render(<Harness from="" to="" allowBlank defaultYear={2027} />);
  expect(screen.getByLabelText("Custom dates")).toBeChecked();
  expect(screen.getByLabelText("Start")).toHaveValue("");
  fireEvent.click(screen.getByLabelText("Custom dates"));
  expect(screen.getByLabelText("Financial year")).toHaveValue("2027"); // the year the screen shows
  expect(dates()).toBe("2026-07-01|2027-06-30");
});

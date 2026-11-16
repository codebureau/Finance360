import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { financialYears } from "@/components/financial-year-select";
import { factText, parseTaxRecordsSearch, recordDates, shareText } from "@/lib/tax";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [
    { id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" },
    { id: 2, name: "Jordan Sample", date_of_birth: "1986-09-30" },
  ],
};

const label = (key: string, name: string, kind: string, section: string, types: string[], item = "") => ({
  key,
  return_item: item,
  name,
  description: key === "work_other" ? "Including working from home, phone and tools." : "",
  direction: "deduction",
  kind,
  modelled: true,
  section,
  record_types: types,
});
const setup = {
  rule_pack_version: "au/2025-26/r3",
  years: [],
  sections: [
    { key: "deductions", name: "Deductions" },
    { key: "medicare", name: "Medicare, health cover and HELP" },
  ],
  labels: [
    label("work_other", "Other work-related expenses", "money", "deductions", ["line"], "D5"),
    label("help_debt", "Has a HELP or study loan debt", "flag", "medicare", ["fact"]),
    label("surcharge_exempt_days", "Days exempt from the Medicare levy surcharge", "days", "medicare", ["fact"], "M2"),
  ],
};

type Rec = Record<string, unknown>;
const internet: Rec = {
  id: 5,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "line",
  from_on: "2025-09-12",
  to_on: null,
  description: "Home internet, work-use share",
  notes: "Invented. 40% of the year's bill is for work.",
  label_key: "work_other",
  label_name: "Other work-related expenses",
  return_item: "D5",
  kind: "money",
  amount: "1200.00",
  work_use_percent: "40",
  counted: "480.00",
  value: null,
};
const debt: Rec = {
  id: 6,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "fact",
  from_on: "2025-07-01",
  to_on: "2026-06-30",
  description: "Study loan balance outstanding",
  notes: null,
  label_key: "help_debt",
  label_name: "Has a HELP or study loan debt",
  return_item: "",
  kind: "flag",
  amount: null,
  work_use_percent: null,
  counted: null,
  value: true,
};

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

function fakeBackend(opts: { records?: Rec[]; saveError?: Response } = {}) {
  const calls: Call[] = [];
  const records = opts.records ?? [internet, debt];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/label-totals") {
        return json({
          financial_year: 2026,
          financial_year_label: "2025-26",
          uncounted: [],
          totals: [
            {
              member_id: 1,
              member_name: "Alex Sample",
              label_key: "work_other",
              label_name: "Other work-related expenses",
              return_item: "D5",
              kind: "money",
              amount: "480.00",
              days: null,
              flag: null,
              contributions: [{ record_id: 5, amount: "480.00", days: null, flag: null }],
            },
            {
              member_id: 1,
              member_name: "Alex Sample",
              label_key: "help_debt",
              label_name: "Has a HELP or study loan debt",
              return_item: "",
              kind: "flag",
              amount: null,
              days: null,
              flag: true,
              contributions: [{ record_id: 6, amount: null, days: null, flag: true }],
            },
          ],
        });
      }
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        if (request.method === "DELETE") return new Response(null, { status: 204 });
        if (request.method === "PUT" && opts.saveError) return opts.saveError;
        const found = records.find((r) => r.id === Number(one[1]));
        return found ? json(found) : json({ message: "Tax record does not exist." }, 404);
      }
      if (path === "/api/tax/records" && request.method === "POST") {
        return opts.saveError ?? json({ ...internet, id: 99 }, 201);
      }
      if (path === "/api/tax/records") return json(records);
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the register lists each record with its Member, label, dates and what it counts", async () => {
  fakeBackend();
  await renderAppAt("/tax/records");
  expect(await screen.findByRole("heading", { name: "Tax records" })).toBeInTheDocument();
  const table = await screen.findByRole("table", { name: "Tax records" });
  const row = within(table).getByRole("row", { name: /Home internet/ });
  expect(row).toHaveTextContent("Other work-related expenses (item D5)");
  expect(row).toHaveTextContent("Alex Sample");
  expect(row).toHaveTextContent("2025-09-12");
  expect(row).toHaveTextContent("480.00");
  expect(row).toHaveTextContent("40% work use");
  const fact = within(table).getByRole("row", { name: /Study loan/ });
  expect(fact).toHaveTextContent("2025-07-01 to 2026-06-30");
  expect(fact).toHaveTextContent("Yes");
});

test("a row opens the record's own page, which names the entered amount and the share", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("link", { name: "Home internet, work-use share" }));
  expect(await screen.findByRole("heading", { name: "Home internet, work-use share" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/tax/records/5");
  expect(screen.getByText("Counts toward the total")).toBeInTheDocument();
  expect(screen.getByText("Amount entered")).toBeInTheDocument();
  expect(screen.getByText("40% work use")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View audit trail for Home internet/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "FY 2025-26" })).toHaveAttribute("href", expect.stringContaining("fy=2026"));
});

test("a missing record says so, with the way back", async () => {
  fakeBackend();
  await renderAppAt("/tax/records/999");
  expect(await screen.findByRole("heading", { name: "Tax record not found" })).toBeInTheDocument();
  expect(within(screen.getByTestId("content")).getByRole("link", { name: /Tax records/ })).toBeInTheDocument();
});

test("the filters are in the address and narrow the request", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax/records?member=1&fy=2026&label=work_other&q=internet");
  await screen.findByRole("table", { name: "Tax records" });
  const asked = calls.filter((c) => c.path === "/api/tax/records").at(-1);
  expect(asked?.search).toBe("?member_id=1&financial_year=2026&label=work_other&q=internet");
  expect(screen.getByLabelText("Member")).toHaveValue("1");
  expect(screen.getByLabelText(/Financial year/)).toHaveValue("2026");
  expect(screen.getByLabelText("Return label")).toHaveValue("work_other");
  expect(screen.getByLabelText("Search")).toHaveValue("internet");

  fireEvent.change(screen.getByLabelText("Member"), { target: { value: "" } });
  await waitFor(() => expect(router.state.location.search).toEqual({ fy: 2026, label: "work_other", q: "internet" }));
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "sample charity " } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "sample charity" }));
  expect(screen.getByLabelText("Search")).toHaveValue("sample charity ");
});

test("the year is picked from a list, and All years clears it", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/tax/records");
  await screen.findByRole("table", { name: "Tax records" });
  const year = screen.getByLabelText(/Financial year/);
  expect(year.tagName).toBe("SELECT");
  expect(within(year).getByRole("option", { name: "All years" })).toBeInTheDocument();
  fireEvent.change(year, { target: { value: "2026" } });
  await waitFor(() => expect(router.state.location.search).toEqual({ fy: 2026 }));
  expect(within(year).getByRole("option", { selected: true })).toHaveTextContent("FY 2025-26");
  fireEvent.change(year, { target: { value: "" } });
  await waitFor(() => expect(router.state.location.search).toEqual({}));
});

test("the years offered are this one and the nine before, plus any given, newest first", () => {
  const today = new Date(2026, 10, 5); // FY 2026-27
  expect(financialYears([], today)).toEqual([2027, 2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018]);
  expect(financialYears([2028, undefined, 2026, 2010], today)).toEqual([
    2028, 2027, 2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2010,
  ]);
});

test("with a year chosen the Label totals show above the records and drill to them", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/tax/records?fy=2026");
  const totals = await screen.findByRole("table", { name: "Label totals" });
  expect(within(totals).getByRole("row", { name: /Other work-related expenses/ })).toHaveTextContent("480.00");
  expect(within(totals).getByRole("row", { name: /HELP or study loan/ })).toHaveTextContent("Yes");
  fireEvent.click(within(totals).getByRole("link", { name: "Other work-related expenses" }));
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ fy: 2026, member: 1, label: "work_other" }),
  );
});

test("no totals card without a year", async () => {
  fakeBackend();
  await renderAppAt("/tax/records");
  await screen.findByRole("table", { name: "Tax records" });
  expect(screen.queryByTestId("label-totals")).not.toBeInTheDocument();
});

test("a record whose period looks wrong is marked on the list, and a good one is not", async () => {
  // 1 June 2025 to 30 June 2026 crosses 30 June and is over a year: the mistake that was saved.
  const wrong: Rec = { ...debt, id: 8, description: "Wrong period", from_on: "2025-06-01", to_on: "2026-06-30" };
  const asset: Rec = { ...debt, id: 9, description: "Laptop", record_type: "depreciating_asset", from_on: "2022-03-01", to_on: null };
  fakeBackend({ records: [wrong, debt, asset] });
  await renderAppAt("/tax/records");
  const table = await screen.findByRole("table", { name: "Tax records" });
  const wrongRow = within(table).getByText("Wrong period").closest("tr") as HTMLElement;
  expect(within(wrongRow).getByText("Check dates")).toBeInTheDocument();
  expect(wrongRow).toHaveTextContent("crosses 30 June");
  expect(wrongRow).toHaveTextContent("longer than a year");
  expect(within(table).getAllByText("Check dates")).toHaveLength(1); // a whole year and an asset's dates are fine
});

test("Add Tax record starts on the financial year the register is showing", async () => {
  fakeBackend();
  await renderAppAt("/tax/records?fy=2024");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  expect(within(form).getByLabelText("Financial year")).toHaveValue("2024");
  expect(within(form).getByText("1 Jul 2023 to 30 Jun 2024")).toBeInTheDocument();
});

test("nothing matching says so; an empty register says how to start", async () => {
  fakeBackend({ records: [] });
  await renderAppAt("/tax/records?q=nothing");
  expect(await screen.findByTestId("no-records")).toHaveTextContent("No Tax record matches these filters.");
  fakeBackend({ records: [] });
  await renderAppAt("/tax/records");
  expect(await screen.findAllByTestId("no-records")).toHaveLength(1);
});

test("adding an amount sends it as text with the work-use share, and the sheet closes", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("Return label"), { target: { value: "work_other" } });
  expect(within(form).getByText("Including working from home, phone and tools.")).toBeInTheDocument();
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Invented tools" } });
  // a single day: Custom dates, then the start, and the end cleared
  fireEvent.click(within(form).getByLabelText("Custom dates"));
  fireEvent.change(within(form).getByLabelText("Date"), { target: { value: "2026-01-10" } });
  fireEvent.change(within(form).getByLabelText("To (if it spans days)"), { target: { value: "" } });
  fireEvent.change(within(form).getByLabelText("Amount ($)"), { target: { value: "200.00" } });
  fireEvent.change(within(form).getByLabelText(/Work-use share/), { target: { value: "50" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/tax/records")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    member_id: 1,
    record_type: "line",
    label: "work_other",
    description: "Invented tools",
    from_on: "2026-01-10",
    to_on: null,
    notes: null,
    amount: "200.00",
    work_use_percent: "50",
  });
  await waitFor(() => expect(screen.queryByRole("form", { name: "New Tax record" })).not.toBeInTheDocument());
});

test("a fact takes yes or no for a flag and a whole number for days", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("What it records"), { target: { value: "fact" } });
  // only facts' labels are offered, and a line's amount boxes are gone
  expect(within(form).queryByRole("option", { name: /Other work-related/ })).not.toBeInTheDocument();
  expect(within(form).queryByLabelText("Amount ($)")).not.toBeInTheDocument();
  fireEvent.change(within(form).getByLabelText("Return label"), { target: { value: "surcharge_exempt_days" } });
  fireEvent.change(within(form).getByLabelText("Days"), { target: { value: "90" } });
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Invented cover" } });
  // the form starts on a financial year; the dates sent are that year's
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    record_type: "fact",
    label: "surcharge_exempt_days",
    value: 90,
    from_on: `${currentYearStart()}-07-01`,
    to_on: `${currentYearStart() + 1}-06-30`,
  });
});

test("a refused save keeps the sheet open with the message beside the field", async () => {
  fakeBackend({
    saveError: json({ errors: [{ field: "amount", message: "AUD amounts have at most 2 decimal places." }] }, 422),
  });
  await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("Amount ($)"), { target: { value: "1.234" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  expect(await screen.findByText("AUD amounts have at most 2 decimal places.")).toBeInTheDocument();
  expect(screen.getByRole("form", { name: "New Tax record" })).toBeInTheDocument();
});

test("deleting a record is confirmed, names it, and returns to the register", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax/records/5");
  fireEvent.keyDown(await screen.findByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Tax record" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Delete Home internet, work-use share?");
  expect(dialog).toHaveTextContent("Audit trail");
  expect(calls.some((c) => c.method === "DELETE")).toBe(false); // nothing happens until confirmed
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete Tax record" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/records"));
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/tax/records/5")).toBe(true);
});

test("the sidebar offers Tax records, and Tax estimate is not current on it", async () => {
  fakeBackend();
  await renderAppAt("/tax/records");
  await screen.findByRole("heading", { name: "Tax records" });
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Tax records" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "Tax estimate" })).not.toHaveAttribute("aria-current");
});

test("small helpers", () => {
  expect(parseTaxRecordsSearch({ member: "2", fy: "2026", label: " salary ", q: " a b " })).toEqual({
    member: 2,
    fy: 2026,
    label: "salary",
    q: "a b",
  });
  expect(parseTaxRecordsSearch({ member: "x", fy: "12", label: "", q: 5 })).toEqual({});
  expect(factText({ kind: "flag", flag: false })).toBe("No");
  expect(factText({ kind: "days", days: 1 })).toBe("1 day");
  expect(factText({ kind: "days", value: 90 })).toBe("90 days");
  expect(recordDates({ from_on: "2026-01-01", to_on: null })).toBe("2026-01-01");
  expect(recordDates({ from_on: "2026-01-01", to_on: "2026-01-01" })).toBe("2026-01-01");
  expect(shareText("40")).toBe("40% work use");
  expect(shareText(null)).toBeNull();
});

function currentYearStart(): number {
  const now = new Date();
  return now.getMonth() >= 6 ? now.getFullYear() : now.getFullYear() - 1;
}

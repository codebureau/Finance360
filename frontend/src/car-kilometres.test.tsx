import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};
const label = (key: string, name: string, item: string, types: string[]) => ({
  key,
  return_item: item,
  name,
  description: "",
  direction: "deduction",
  kind: "money",
  modelled: true,
  section: "business",
  record_types: types,
});
const setup = {
  rule_pack_version: "x",
  years: [],
  sections: [{ key: "business", name: "Sole trader" }],
  labels: [
    label("business_expenses", "Business expenses", "P8/15", [
      "line",
      "car_kilometres",
    ]),
  ],
};

const trip = (
  description: string,
  km: string,
  times: number,
  total: string,
  on: string | null = null,
) => ({ description, km, times, total_km: total, on });

const car = {
  trips: [
    trip("Invented client site, return", "40.0", 10, "400.0"),
    trip("Invented supplier, return", "12.5", 4, "50.0", "2025-10-18"),
    trip("Invented errand", "7.3", 3, "21.9"),
  ],
  total_km: "471.9",
  limit_km: 3000,
  counted_km: "471.9",
  capped: false,
  rate_per_km: "0.41",
  deduction: "193.48",
  missing: [] as string[],
};
const record = {
  id: 12,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "car_kilometres",
  from_on: "2025-07-01",
  to_on: "2026-06-30",
  description: "Invented car kilometres",
  notes: null,
  label_key: "business_expenses",
  label_name: "Business expenses",
  return_item: "P8/15",
  kind: "money",
  amount: null,
  work_use_percent: null,
  counted: "193.48",
  value: null,
  statement: null,
  asset: null,
  home_office: null,
  car,
  figures_used: [
    {
      key: "tax.car.rate_per_km",
      name: "Car expenses: cents per kilometre rate",
      financial_year: 2026,
      financial_year_label: "2025-26",
      value: "0.41 a km",
      source: "ATO, Car expenses: cents per kilometre method",
      url: "https://www.ato.gov.au/invented",
      url_confirmed: false,
      retrieved: "2026-11-05",
      provisional: false,
      provisional_note: null,
      rule_pack_version: "au/2026-27/r6",
    },
    {
      key: "tax.car.max_km",
      name: "Car expenses: kilometre limit per car",
      financial_year: 2026,
      financial_year_label: "2025-26",
      value: "3000 km",
      source: "ATO, Car expenses: cents per kilometre method",
      url: "https://www.ato.gov.au/invented",
      url_confirmed: false,
      retrieved: "2026-11-05",
      provisional: false,
      provisional_note: null,
      rule_pack_version: "au/2026-27/r6",
    },
  ],
  calculation: {
    steps: [
      {
        title: "Kilometres driven",
        operands: [
          { value: "40.0", unit: "km", label: "Invented", operator: null },
          { value: "10", unit: null, label: "times", operator: "×" },
        ],
        result: {
          value: "471.9",
          unit: "km",
          label: "total kilometres",
          operator: null,
        },
        note: null,
      },
    ],
    waiting_for: [],
  },
};
const noFigures = {
  ...record,
  id: 13,
  description: "Invented car without figures",
  counted: null,
  car: {
    ...car,
    limit_km: null,
    counted_km: null,
    rate_per_km: null,
    deduction: null,
    missing: ["tax.car.rate_per_km", "tax.car.max_km"],
  },
  figures_used: [],
  calculation: {
    steps: [],
    waiting_for: [
      {
        key: "tax.car.rate_per_km",
        name: "Car expenses: cents per kilometre rate",
        financial_year: 2026,
        financial_year_label: "2025-26",
      },
    ],
  },
};
const capped = {
  ...record,
  id: 14,
  description: "Invented capped car",
  car: { ...car, limit_km: 400, counted_km: "400.0", capped: true },
};

type Call = { method: string; path: string; body?: Record<string, unknown> };

function fakeBackend(
  records: Record<string, unknown>[] = [],
  failWith?: unknown,
) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method)
        ? await request.json()
        : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/label-totals") {
        return json({
          financial_year: 2026,
          financial_year_label: "2025-26",
          totals: [],
          uncounted: [],
        });
      }
      if (path === "/api/library/check")
        return json({ checked: 0, problems: [] });
      if (path === "/api/library/links") return json([]);
      if (path === "/api/tax/records" && request.method === "POST") {
        return failWith
          ? json(failWith, 422)
          : json({ ...record, id: 99 }, 201);
      }
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        const found = records.find((r) => r.id === Number(one[1]));
        return found
          ? json(found)
          : json({ message: "Tax record does not exist." }, 404);
      }
      if (path === "/api/tax/records") return json(records);
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openForm(type = "car_kilometres") {
  await renderAppAt("/tax/records");
  fireEvent.click(
    await screen.findByRole("button", { name: "Add Tax record" }),
  );
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("What it records"), {
    target: { value: type },
  });
  fireEvent.change(within(form).getByLabelText("Return label"), {
    target: { value: "business_expenses" },
  });
  fireEvent.change(within(form).getByLabelText("Description"), {
    target: { value: "Invented car kilometres" },
  });
  return form;
}

const field = (form: HTMLElement, name: string) =>
  within(form).getByLabelText(name) as HTMLInputElement;
const tripRows = (form: HTMLElement) =>
  within(within(form).getByTestId("trips-table")).getAllByRole("row").slice(1);
const type = (form: HTMLElement, name: string, value: string) =>
  fireEvent.change(field(form, name), { target: { value } });
const post = (calls: Call[]) => calls.find((c) => c.method === "POST")?.body;
const put = (calls: Call[]) => calls.find((c) => c.method === "PUT")?.body;

test("the form has a trips table with a row to start, and Add trip appends an empty one", async () => {
  fakeBackend();
  const form = await openForm();
  const table = within(form).getByTestId("trips-table");
  for (const heading of [
    "Description",
    "km",
    "Times",
    "Date (optional)",
    "Total km",
  ]) {
    expect(
      within(table).getByRole("columnheader", { name: heading }),
    ).toBeVisible();
  }
  expect(tripRows(form)).toHaveLength(1);
  expect(field(form, "Times, trip 1").value).toBe("1");
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  expect(tripRows(form)).toHaveLength(2);
  expect(field(form, "Description, trip 2").value).toBe("");
  expect(field(form, "Kilometres, trip 2").value).toBe("");
  fireEvent.click(within(form).getByRole("button", { name: "Remove trip 2" }));
  expect(tripRows(form)).toHaveLength(1);
});

test("the table sizes to its content and scrolls inside its own container, so headings sit over their inputs", async () => {
  fakeBackend();
  const form = await openForm();
  const holder = within(form).getByTestId("trips-table");
  expect(holder).toHaveClass("overflow-x-auto");
  const table = within(holder).getByRole("table");
  expect(table).toHaveClass("w-auto");
  expect(table).not.toHaveClass("w-full");
  // every heading has an input (or the total) in its column
  const headings = within(table).getAllByRole("columnheader");
  expect(headings).toHaveLength(6);
  expect(within(tripRows(form)[0]).getAllByRole("cell")).toHaveLength(
    headings.length,
  );
});

test("each row works out its total km, and the running total is exact under the table", async () => {
  fakeBackend();
  const form = await openForm();
  type(form, "Kilometres, trip 1", "40");
  type(form, "Times, trip 1", "10");
  expect(within(form).getByLabelText("Total km, trip 1")).toHaveTextContent(
    "400.0",
  );
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  type(form, "Kilometres, trip 2", "12.5");
  type(form, "Times, trip 2", "4");
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  type(form, "Kilometres, trip 3", "7.3");
  type(form, "Times, trip 3", "3");
  expect(within(form).getByLabelText("Total km, trip 2")).toHaveTextContent(
    "50.0",
  );
  expect(within(form).getByLabelText("Total km, trip 3")).toHaveTextContent(
    "21.9",
  );
  // 400.0 + 50.0 + 21.9, in tenths: no float rounding anywhere
  expect(within(form).getByLabelText("Trip totals")).toHaveTextContent(
    "471.9 km",
  );
  // a row that is not a valid entry shows a dash and adds nothing
  type(form, "Kilometres, trip 3", "7.35");
  expect(within(form).getByLabelText("Total km, trip 3")).toHaveTextContent(
    "–",
  );
  expect(within(form).getByLabelText("Trip totals")).toHaveTextContent(
    "450.0 km",
  );
  expect(form).toHaveTextContent("worked out on save");
});

test("tenths add up exactly where floats would not", async () => {
  fakeBackend();
  const form = await openForm();
  for (let i = 2; i <= 3; i++) {
    fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  }
  for (const [i, km] of [
    [1, "0.1"],
    [2, "0.2"],
    [3, "0.3"],
  ] as const) {
    type(form, `Kilometres, trip ${i}`, km);
  }
  expect(within(form).getByLabelText("Trip totals")).toHaveTextContent(
    "0.6 km",
  );
});

test("saving sends the trips as text, leaves a blank row out and defaults times to 1", async () => {
  const calls = fakeBackend();
  const form = await openForm();
  fireEvent.click(within(form).getByLabelText("Custom dates"));
  type(form, "From", "2025-07-01");
  type(form, "To", "2026-06-30");
  type(form, "Description, trip 1", "Invented client site, return");
  type(form, "Kilometres, trip 1", "40");
  type(form, "Times, trip 1", "10");
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  type(form, "Description, trip 2", "Invented errand");
  type(form, "Kilometres, trip 2", "7.3");
  type(form, "Times, trip 2", "");
  type(form, "Date, trip 2", "2025-10-18");
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" })); // left blank
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  await waitFor(() => expect(post(calls)).toBeDefined());
  const body = post(calls) as Record<string, unknown>;
  expect(body).toMatchObject({
    record_type: "car_kilometres",
    label: "business_expenses",
    from_on: "2025-07-01",
    to_on: "2026-06-30",
  });
  expect(body.trips).toEqual([
    {
      description: "Invented client site, return",
      km: "40",
      times: "10",
      on: "",
    },
    {
      description: "Invented errand",
      km: "7.3",
      times: "1",
      on: "2025-10-18",
    },
  ]);
  expect(body).not.toHaveProperty("months");
});

test("a server error for a trip is shown against that trip", async () => {
  fakeBackend([], {
    errors: [
      { field: "trips.1.km", message: "The kilometres are more than 0." },
      {
        field: "member_id",
        message:
          "This Member already has car kilometres for FY 2025-26: add trips to that record.",
      },
    ],
  });
  const form = await openForm();
  type(form, "Description, trip 1", "Invented a");
  type(form, "Kilometres, trip 1", "5");
  fireEvent.click(within(form).getByRole("button", { name: "Add trip" }));
  type(form, "Description, trip 2", "Invented b");
  type(form, "Kilometres, trip 2", "0");
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  expect(
    await within(form).findByText(
      "Trip 2, kilometres: The kilometres are more than 0.",
    ),
  ).toBeVisible();
  expect(form).toHaveTextContent("This Member already has car kilometres");
});

test("Add trip is disabled at 500 trips", async () => {
  const many = {
    ...record,
    id: 15,
    description: "Invented many trips",
    car: {
      ...car,
      trips: Array.from({ length: 499 }, (_, i) =>
        trip(`Invented trip ${i}`, "1.0", 1, "1.0"),
      ),
    },
  };
  fakeBackend([many]);
  const form = await openEdit(15);
  const add = within(form).getByRole("button", { name: "Add trip" });
  expect(tripRows(form)).toHaveLength(499);
  expect(add).toBeEnabled();
  fireEvent.click(add);
  expect(tripRows(form)).toHaveLength(500);
  expect(add).toBeDisabled();
  expect(form).toHaveTextContent("at most 500 trips");
  fireEvent.click(
    within(form).getByRole("button", { name: "Remove trip 500" }),
  );
  expect(add).toBeEnabled();
}, 60000);

test("the Sheet opens at full width for car kilometres and collapses on request", async () => {
  fakeBackend();
  const form = await openForm("line");
  const sheetEl = screen.getByRole("dialog", { name: "New Tax record" });
  expect(sheetEl).toHaveAttribute("data-expanded", "false");
  fireEvent.change(within(form).getByLabelText("What it records"), {
    target: { value: "car_kilometres" },
  });
  await waitFor(() => expect(sheetEl).toHaveAttribute("data-expanded", "true"));
  fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
  expect(sheetEl).toHaveAttribute("data-expanded", "false");
});

test("Cancel asks before discarding a trip that was typed", async () => {
  fakeBackend();
  const form = await openForm();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  const ask = await screen.findByRole("alertdialog");
  expect(ask).toHaveTextContent("Discard your changes?");
  fireEvent.click(within(ask).getByRole("button", { name: "Keep editing" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  type(form, "Description, trip 1", "Invented trip kept");
  expect(field(form, "Description, trip 1").value).toBe("Invented trip kept");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Discard",
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "New Tax record" }),
    ).not.toBeInTheDocument(),
  );
});

async function openEdit(id: number) {
  await renderAppAt(`/tax/records/${id}`);
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit Tax record" }),
  );
  return screen.findByRole("form", { name: /^Edit / });
}

test("editing shows the saved trips and, once the limit is known, says how many km count", async () => {
  fakeBackend([capped]);
  const form = await openEdit(14);
  expect(tripRows(form)).toHaveLength(3);
  expect(field(form, "Description, trip 2").value).toBe(
    "Invented supplier, return",
  );
  expect(field(form, "Times, trip 1").value).toBe("10");
  expect(field(form, "Date, trip 2").value).toBe("2025-10-18");
  // 471.9 km against a limit of 400 km
  expect(within(form).getByRole("status")).toHaveTextContent(
    "471.9 km is above the limit of 400 km, so 400.0 km count.",
  );
  type(form, "Times, trip 1", "5"); // 200.0 + 50.0 + 21.9 = 271.9 km: now under it
  expect(within(form).getByRole("status")).toHaveTextContent(
    "Within the limit of 400 km",
  );
});

test("saving an edit sends every trip again", async () => {
  const calls = fakeBackend([record]);
  const form = await openEdit(12);
  type(form, "Times, trip 3", "4");
  fireEvent.click(within(form).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(put(calls)).toBeDefined());
  const trips = (put(calls) as { trips: Record<string, string>[] }).trips;
  expect(trips).toHaveLength(3);
  expect(trips[2]).toEqual({
    description: "Invented errand",
    km: "7.3",
    times: "4",
    on: "",
  });
});

test("the page shows the trips read-only with the totals, the rate and the working", async () => {
  fakeBackend([record]);
  await renderAppAt("/tax/records/12");
  const table = await screen.findByRole("table", { name: "Car trips" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent("Invented client site, return");
  expect(rows[0]).toHaveTextContent("40.0");
  expect(rows[0]).toHaveTextContent("10");
  expect(rows[0]).toHaveTextContent("400.0");
  expect(rows[1]).toHaveTextContent("2025-10-18");
  const card = screen.getByLabelText("Car kilometres");
  expect(card).toHaveTextContent("471.9");
  expect(card).toHaveTextContent("0.41");
  expect(card).toHaveTextContent("193.48");
  expect(within(card).queryByRole("alert")).not.toBeInTheDocument();
  // the trace needs no change: it reads the calculation and the figures the server sends
  const trace = screen.getByLabelText("How this is worked out");
  expect(trace).toHaveTextContent("Kilometres driven");
  expect(trace).toHaveTextContent("Car expenses: kilometre limit per car");
  expect(trace).toHaveTextContent("3000 km");
  expect(trace).toHaveTextContent("Car expenses: cents per kilometre rate");
});

test("a record over the limit says how many km count", async () => {
  fakeBackend([capped]);
  await renderAppAt("/tax/records/14");
  const card = await screen.findByLabelText("Car kilometres");
  expect(card).toHaveTextContent("400.0");
  expect(card).toHaveTextContent("Limited to 400 km");
});

test("a record whose figures are not captured says so and points at Rule figures", async () => {
  fakeBackend([noFigures]);
  await renderAppAt("/tax/records/13");
  const card = await screen.findByLabelText("Car kilometres");
  const alert = within(card).getByRole("alert");
  expect(alert).toHaveTextContent("tax.car.rate_per_km, tax.car.max_km");
  expect(alert).toHaveTextContent("Capture the figures on Rule figures (FY 2025-26).");
  expect(alert).not.toHaveTextContent("rulepack_update");
  expect(within(card).getAllByText("Figure needed").length).toBe(3);
  expect(screen.getByLabelText("How this is worked out")).toHaveTextContent(
    "Waiting for a figure",
  );
});

test("the register shows Figure needed for a record with no figures", async () => {
  fakeBackend([noFigures]);
  await renderAppAt("/tax/records");
  expect((await screen.findAllByText("Figure needed")).length).toBeGreaterThan(
    0,
  );
});

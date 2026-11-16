import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { IncomeStream } from "@/lib/income-streams";
import type { Scenario, ScenarioProjection } from "@/lib/scenarios";
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

// Invented salaries, as in the synthetic household.
const alex: IncomeStream = {
  id: 1,
  name: "Alex salary (Sample Employer)",
  notes: "Invented.",
  type_key: "salary",
  type_label: "Salary",
  tax_kind: "employment",
  ownership: "sole",
  owners: [{ member_id: 1, member_name: "Alex Sample", share_bp: 10000 }],
  gross_annual: "120000.00",
  take_home_annual: "91000.00",
  start_on: null,
  start_event: null,
  end_on: null,
  end_event: "retirement",
  credit_category: "cash",
};
const jordan: IncomeStream = {
  ...alex,
  id: 2,
  name: "Jordan salary (Sample Employer)",
  owners: [{ member_id: 2, member_name: "Jordan Sample", share_bp: 10000 }],
  gross_annual: "85000.00",
  take_home_annual: "66000.00",
};

type Call = { method: string; path: string; body?: Record<string, unknown> };

function fakeBackend(
  opts: {
    streams?: IncomeStream[];
    save?: (body: Record<string, unknown>) => Response;
    compare?: { id: number; name: string; field?: string }[];
  } = {},
) {
  const calls: Call[] = [];
  const streams = opts.streams ?? [alex, jordan];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/income-streams" && request.method === "GET") return json(streams);
      if (path === "/api/income-streams" && request.method === "POST")
        return opts.save ? opts.save(body ?? {}) : json({ ...alex, id: 3 }, 201);
      const one = path.match(/^\/api\/income-streams\/(\d+)$/);
      if (one && request.method === "GET") {
        const found = streams.find((s) => s.id === Number(one[1]));
        return found ? json(found) : json({ message: "Income stream 9 does not exist." }, 404);
      }
      if (one && request.method === "PUT") return opts.save ? opts.save(body ?? {}) : json({ ...alex, ...body });
      if (one && request.method === "DELETE") return new Response(null, { status: 204 });
      if (path === "/api/projection/compare" && opts.compare) {
        const scenarios = opts.compare.map(({ id, name, field }) => ({
          scenario: { id, name },
          errors: field ? [{ field, message: "Not projected." }] : [],
        }));
        return json({ scenarios });
      }
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the Income streams page lists each stream with its Member, type kind, gross and take-home", async () => {
  fakeBackend();
  await renderAppAt("/income-streams");
  const table = await screen.findByRole("table", { name: "Income streams" });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(3); // header and two
  expect(within(rows[1]).getByRole("link", { name: "Alex salary (Sample Employer)" })).toBeInTheDocument();
  expect(rows[1]).toHaveTextContent("Alex Sample");
  expect(rows[1]).toHaveTextContent("employment");
  expect(rows[1]).toHaveTextContent("120,000.00");
  expect(rows[1]).toHaveTextContent("91,000.00");
  expect(rows[1]).toHaveTextContent("From now, until retirement");
});

test("the page states what an income stream leaves out", async () => {
  fakeBackend();
  await renderAppAt("/income-streams");
  const card = await screen.findByTestId("income-simplifications");
  expect(card).toHaveTextContent("Division 293");
  expect(card).toHaveTextContent("maximum contribution base");
  expect(card).toHaveTextContent("salary sacrifice");
  expect(card).toHaveTextContent("fund fees");
  expect(card).toHaveTextContent("tax is not calculated yet");
});

test("an empty list says how to start", async () => {
  fakeBackend({ streams: [] });
  await renderAppAt("/income-streams");
  expect(await screen.findByText(/No income streams yet/)).toBeInTheDocument();
});

test("the list warns which Scenarios a salary leaves without working-years spending", async () => {
  fakeBackend({
    compare: [
      { id: 1, name: "Best", field: "spending_phases" },
      { id: 2, name: "Likely", field: "spending_phases" },
      { id: 3, name: "Worst" },
    ],
  });
  await renderAppAt("/income-streams");
  const warning = await screen.findByTestId("working-years-warning");
  expect(warning).toHaveTextContent("Best, Likely can't be projected");
  expect(within(warning).getByRole("link", { name: "Add a spending phase in Best" })).toHaveAttribute(
    "href",
    "/projection?scenario=1",
  );
});

test("the list has no warning when every Scenario can be projected", async () => {
  const calls = fakeBackend({ compare: [{ id: 1, name: "Best" }] });
  await renderAppAt("/income-streams");
  await screen.findByRole("table", { name: "Income streams" });
  await waitFor(() => expect(calls.some((c) => c.path === "/api/projection/compare")).toBe(true));
  expect(screen.queryByTestId("working-years-warning")).not.toBeInTheDocument();
});

test("the navigation reaches the page", async () => {
  fakeBackend();
  await renderAppAt("/position");
  const nav = await screen.findByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Income streams" })).toHaveAttribute("href", "/income-streams");
});

test("a new salary is sent with the Member, amounts as text and the default end at retirement", async () => {
  const calls = fakeBackend();
  await renderAppAt("/income-streams");
  fireEvent.click(await screen.findByRole("button", { name: /Add income stream/ }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Sample side job" } });
  fireEvent.change(screen.getByLabelText("Member"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText(/Gross pay a year/), { target: { value: "20000" } });
  fireEvent.change(screen.getByLabelText(/Take-home pay a year/), { target: { value: "18000" } });
  fireEvent.click(screen.getByRole("button", { name: "Create income stream" }));

  await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    name: "Sample side job",
    type_key: "salary",
    ownership: "sole",
    credit_category: "cash",
    owners: [{ member_id: 2 }],
    gross_annual: "20000",
    take_home_annual: "18000",
    start_on: null,
    end_on: null,
    end_event: "retirement",
    notes: null,
  });
});

test("a stream's dates can be a financial year (a shortcut) or any custom dates", async () => {
  const calls = fakeBackend();
  await renderAppAt("/income-streams");
  fireEvent.click(await screen.findByRole("button", { name: /Add income stream/ }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Sample year job" } });
  fireEvent.change(screen.getByLabelText(/Gross pay a year/), { target: { value: "20000" } });
  // both blank is custom (now to retirement); switching it off offers a financial year
  expect(screen.getByLabelText("Custom dates")).toBeChecked();
  fireEvent.click(screen.getByLabelText("Custom dates"));
  fireEvent.change(screen.getByLabelText("Financial year"), { target: { value: "2027" } });
  fireEvent.click(screen.getByRole("button", { name: "Create income stream" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    start_on: "2026-07-01",
    end_on: "2027-06-30",
    end_event: null,
  });
});

test("a refused save keeps the sheet open with the message beside the field", async () => {
  fakeBackend({
    save: () =>
      json({ errors: [{ field: "take_home_annual", message: "Take-home pay can't exceed gross pay." }] }, 422),
  });
  await renderAppAt("/income-streams");
  fireEvent.click(await screen.findByRole("button", { name: /Add income stream/ }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Too generous" } });
  fireEvent.change(screen.getByLabelText(/Gross pay a year/), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText(/Take-home pay a year/), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Create income stream" }));
  expect(await screen.findByText("Take-home pay can't exceed gross pay.")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Too generous");
});

test("the detail page edits in a sheet and deletes only after a confirmation that names the stream", async () => {
  const calls = fakeBackend();
  await renderAppAt("/income-streams/1");
  expect(await screen.findByRole("heading", { name: "Alex salary (Sample Employer)" })).toBeInTheDocument();
  expect(screen.getByText("Until retirement")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Edit income stream" }));
  expect(await screen.findByLabelText(/Take-home pay a year/)).toHaveValue("91000.00");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete income stream" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete Alex salary (Sample Employer)?")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(false); // nothing yet
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete income stream" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1));
  expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/api/income-streams/1");
});

test("a missing stream is a clear not-found page", async () => {
  fakeBackend();
  await renderAppAt("/income-streams/9");
  expect(await screen.findByText("Income stream not found")).toBeInTheDocument();
});

// --- the Projection screen -------------------------------------------------------------------

const ORDER = [
  { key: "cash", name: "Cash" },
  { key: "superannuation", name: "Superannuation" },
];
const scenarios: Scenario[] = [
  {
    id: 2,
    key: "likely",
    name: "Likely",
    notes: null,
    override_count: 0,
    drawdown_order: ORDER,
    drawdown_is_default: true,
    spending_is_default: true,
  },
];

const projection = (): ScenarioProjection => ({
  engine_version: 1,
  input_version: 1,
  as_at: "2026-10-25",
  years: 3,
  reference_member: { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 2 },
  funds_last_to_age: null,
  lasts_beyond_horizon: true,
  sustainable: { annual_spending: "1234.56", capped: false },
  categories: ORDER,
  members: [
    { id: 1, name: "Alex Sample", age_now: 42, retirement_year: 1 },
    { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 2 },
  ],
  flagged: [],
  rules: null,
  life_events: null,
  super_access: [],
  spending: null,
  tax: null,
  income: {
    streams: [
      {
        id: 1,
        name: "Alex salary (Sample Employer)",
        type_key: "salary",
        member_id: 1,
        gross_annual: "120000.00",
        take_home_annual: "91000.00",
        credit_category: "cash",
        start_on: null,
        start_event: null,
        end_on: null,
        end_event: "retirement",
        from_year: 0,
        until_year: 1,
        real_wage_growth: "1.5",
      },
      {
        id: 2,
        name: "Jordan salary (Sample Employer)",
        type_key: "salary",
        member_id: 2,
        gross_annual: "85000.00",
        take_home_annual: "66000.00",
        credit_category: "cash",
        start_on: null,
        start_event: null,
        end_on: null,
        end_event: "retirement",
        from_year: 0,
        until_year: 0,
        real_wage_growth: "1.5",
      },
    ],
    rates: [
      {
        key: "sg_rate",
        from_year: 0,
        financial_year: "2026-27",
        value: "12",
        layer: "rule_pack",
        source: "Invented test citation for the Super Guarantee rate (unverified)",
      },
      {
        key: "contributions_tax_rate",
        from_year: 0,
        financial_year: "2026-27",
        value: "15",
        layer: "rule_pack",
        source: "Invented test citation for the contributions tax (unverified)",
      },
    ],
  },
  rows: [0, 1, 2].map((t) => ({
    t,
    starts_on: `${2026 + t}-10-25`,
    age: 40 + t,
    categories: ORDER.map((c) => ({
      category_key: c.key,
      opening: "10.00",
      growth: "0.00",
      credited: "0.00",
      debited: "0.00",
      withdrawn: "0.00",
      closing: "10.00",
    })),
    opening: "20.00",
    closing: "20.00",
    need: "0.00",
    shortfall: "0.00",
    debits: [],
    paid_personally: "0.00",
  })),
  scenario: { id: 2, key: "likely", name: "Likely" },
  provenance: [],
});

function projectionBackend(run: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/projection/scenarios/2") return run();
      if (path === "/api/scenarios") return json(scenarios);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
}

test("the Projection lists the streams it used with their years and the Rule pack rates", async () => {
  projectionBackend(() => json(projection()));
  await renderAppAt("/projection");
  const used = await screen.findByTestId("income-used");
  const alexRow = within(used).getByTestId("income-stream-1");
  expect(alexRow).toHaveTextContent("91,000.00");
  expect(alexRow).toHaveTextContent("Real wage growth 1.5%");
  expect(alexRow).toHaveTextContent("Paid from 2026-10-25 until 2027-10-25");
  expect(within(used).getByTestId("income-stream-2")).toHaveTextContent("Not paid: the Member has already retired");
  const rates = within(used).getByTestId("income-rates");
  expect(rates).toHaveTextContent("Superannuation Guarantee rate from 2026-27");
  expect(rates).toHaveTextContent("12%");
  expect(rates).toHaveTextContent("Contributions tax rate from 2026-27");
  expect(rates).toHaveTextContent("15%");
  // Each rate's cited source is on the screen, so it can be checked against the published figure.
  const sources = within(used).getByTestId("income-rate-sources");
  expect(sources).toHaveTextContent("Super Guarantee rate (unverified)");
  expect(sources).toHaveTextContent("contributions tax (unverified)");
});

test("a run saved before income streams says so", async () => {
  projectionBackend(() => json({ ...projection(), income: null }));
  await renderAppAt("/projection");
  expect(await screen.findByTestId("income-used")).toHaveTextContent("saved before income streams were recorded");
});

const taxUsed = (): NonNullable<ScenarioProjection["tax"]> => ({
  tax_engine_version: 2,
  tax_input_version: 1,
  rule_pack_version: "au/2026-27/r7",
  div293_unavailable: false,
  payers: [{ member_id: 1, payer: "own", layer: "default" }],
  years: [0, 1].map((t) => ({
    t,
    financial_year: `${2026 + t}-${27 + t}`,
    source_financial_year: "2025-26",
    carried: true,
    frozen_eroded: 3,
    members: [{ member_id: 1, gross: "120000.00", tax: "30000.00", take_home: "90000.00" }],
  })),
  payments: [
    {
      member_id: 1,
      for_financial_year: "2026-27",
      amount: "4500.00",
      payer: "own",
      paid_year: 1,
      modelled: true,
      estimated_from_year_0: false,
      taxable_income: "300000.00",
      low_tax_contributions: "30000.00",
      div293_income: "330000.00",
      threshold: "250000.00",
      rate: "15",
    },
    {
      member_id: 1,
      for_financial_year: "2028-29",
      amount: "4500.00",
      payer: "super",
      paid_year: 1,
      modelled: true,
      estimated_from_year_0: false,
      taxable_income: "300000.00",
      low_tax_contributions: "30000.00",
      div293_income: "330000.00",
      threshold: "250000.00",
      rate: "15",
    },
    {
      // A run saved by engine 1, which did not model the super payer.
      member_id: 1,
      for_financial_year: "2027-28",
      amount: "4500.00",
      payer: "super",
      paid_year: null,
      modelled: false,
      estimated_from_year_0: false,
      taxable_income: "300000.00",
      low_tax_contributions: "30000.00",
      div293_income: "330000.00",
      threshold: "250000.00",
      rate: "15",
    },
  ],
});

test("a run with tax on shows the versions, the carry-forward and Division 293 with its inputs", async () => {
  projectionBackend(() => json({ ...projection(), tax: taxUsed() }));
  await renderAppAt("/projection");
  const used = await screen.findByTestId("tax-used");
  expect(used).toHaveTextContent("version 2, Rule pack au/2026-27/r7");
  const carry = within(used).getByTestId("tax-carry");
  expect(carry).toHaveTextContent("FY 2026-27 to 2027-28 uses the figures of FY 2025-26 carried forward");
  expect(carry).toHaveTextContent("frozen fall in real terms by inflation");
  const pays = within(used).getByTestId("div293-payments");
  expect(pays).toHaveTextContent("Division 293, FY 2026-27");
  expect(pays).toHaveTextContent("Paid in the year starting 2027-10-25, a year after the year it is for");
  expect(pays).toHaveTextContent("330,000.00");
  expect(pays).toHaveTextContent("250,000.00");
  expect(pays).toHaveTextContent("Taken from Alex Sample's super in the year starting 2027-10-25");
  expect(pays).toHaveTextContent("computed before paying from super was modelled, so nothing was taken");
});

test("with tax off the working says take-home pay is the stated amount", async () => {
  projectionBackend(() => json({ ...projection(), tax: null }));
  await renderAppAt("/projection");
  expect(await screen.findByTestId("tax-used")).toHaveTextContent("Tax is off: take-home pay is each Income stream");
});

test("a salary with no working-years spending is a named error with the way to fix it", async () => {
  projectionBackend(() =>
    json(
      {
        errors: [
          {
            field: "spending_phases",
            message:
              "Working-years spending has no value. Alex Sample and Jordan Sample earn salaries from the projection start, so spending needs a phase that starts at the projection start. Add one, or the salary would all be saved.",
          },
        ],
      },
      422,
    ),
  );
  await renderAppAt("/projection");
  const card = await screen.findByTestId("incomplete");
  expect(card).toHaveTextContent("Working-years spending has no value");
  expect(within(card).getByTestId("working-years-hint")).toHaveTextContent("a phase that starts at the projection start");
  expect(within(card).getByRole("button", { name: "Edit Household spending" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Edit Likely spending" })).toBeInTheDocument();
});

test("the simplifications state what salary leaves out", async () => {
  projectionBackend(() => json(projection()));
  await renderAppAt("/projection");
  const box = await screen.findByTestId("simplifications");
  expect(box).toHaveTextContent("Division 293");
  expect(box).toHaveTextContent("maximum contribution base");
  expect(box).toHaveTextContent("salary sacrifice");
  expect(box).toHaveTextContent("fund fees");
  expect(box).toHaveTextContent("take-home pay is the amount entered");
});

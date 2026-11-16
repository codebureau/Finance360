import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import type { Layer, SavedAssumptions, Scenario, ScenarioProjection } from "@/lib/scenarios";

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

const DEFAULT_ORDER = [
  { key: "cash", name: "Cash" },
  { key: "investments", name: "Investments" },
  { key: "superannuation", name: "Superannuation" },
  { key: "overseas_pensions", name: "Overseas pensions" },
];

const scenarios: Scenario[] = [
  { id: 1, key: "best", name: "Best", notes: null, override_count: 4, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true, spending_is_default: true },
  { id: 2, key: "likely", name: "Likely", notes: null, override_count: 0, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true, spending_is_default: true },
  { id: 3, key: "worst", name: "Worst", notes: null, override_count: 5, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true, spending_is_default: true },
];

const categoryYear = (key: string, opening: string, closing: string) => ({
  category_key: key,
  opening,
  growth: "0.00",
  credited: "0.00",
  debited: "0.00",
  withdrawn: "0.00",
  closing,
});

const rules = (over: { pension?: [number, number]; layer?: string } = {}) => ({
  pack_version: "au/2026-27/r1",
  parameters: [
    { key: "preservation_age", label: "Preservation age", source: "SIS Regulations 1994 reg 6.01 (invented test text)" },
    { key: "unrestricted_super_age", label: "Unrestricted super age", source: "SIS Regulations 1994 Sch 1 (invented test text)" },
    { key: "age_pension_age", label: "Age Pension age", source: "Social Security Act 1991 s 23 (invented test text)" },
  ],
  values: [1, 2].flatMap((id) => [
    { member_id: id, key: "preservation_age", years: 60, months: 0, layer: "rule_pack" },
    { member_id: id, key: "unrestricted_super_age", years: 65, months: 0, layer: "rule_pack" },
    {
      member_id: id,
      key: "age_pension_age",
      years: over.pension?.[0] ?? 67,
      months: over.pension?.[1] ?? 0,
      layer: over.layer ?? "rule_pack",
    },
  ]),
});

// Invented: Alex retires five years before preservation age (a gap); Jordan retires at it.
const lifeEvents = () => [
  { member_id: 1, kind: "retirement", on: "2039-03-12", age_years: 55, age_months: 0, source: "scenario", year: 13 },
  { member_id: 1, kind: "preservation_age", on: "2044-03-12", age_years: 60, age_months: 0, source: "rule_pack", year: 18 },
  { member_id: 1, kind: "unrestricted_super_age", on: "2049-03-12", age_years: 65, age_months: 0, source: "rule_pack", year: 23 },
  { member_id: 1, kind: "age_pension_age", on: "2051-03-12", age_years: 67, age_months: 0, source: "rule_pack", year: 25 },
  { member_id: 2, kind: "preservation_age", on: "2046-09-30", age_years: 60, age_months: 0, source: "rule_pack", year: 20 },
  { member_id: 2, kind: "retirement", on: "2046-09-30", age_years: 60, age_months: 0, source: "scenario", year: 20 },
];
const superAccess = () => [
  { member_id: 1, year: 18, on: "2044-10-25" },
  { member_id: 2, year: 20, on: "2046-10-25" },
];

// Invented: two years, the second short.
const projection = (over: Partial<ScenarioProjection> = {}): ScenarioProjection => ({
  engine_version: 1,
  input_version: 1,
  as_at: "2026-10-25",
  years: 2,
  reference_member: { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  funds_last_to_age: 41,
  lasts_beyond_horizon: false,
  sustainable: { annual_spending: "1234.56", capped: false },
  categories: [
    { key: "cash", name: "Cash" },
    { key: "investments", name: "Investments" },
  ],
  members: [
    { id: 1, name: "Alex Sample", age_now: 42, retirement_year: 18 },
    { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  ],
  flagged: [],
  rules: rules(),
  life_events: lifeEvents(),
  super_access: superAccess(),
  income: null,
  tax: null,
  spending: {
    layer: "household",
    phases: [
      { anchor: "last_retirement", offset_years: 0, annual_amount: "70000.00", from_year: 20, until_year: 30, empty: false },
      { anchor: "last_retirement", offset_years: 10, annual_amount: "55000.00", from_year: 30, until_year: null, empty: false },
    ],
  },
  rows: [
    {
      t: 0,
      starts_on: "2026-10-25",
      age: 40,
      categories: [categoryYear("cash", "1000.00", "700.00"), categoryYear("investments", "500.00", "500.00")],
      opening: "1500.00",
      closing: "1200.00",
      need: "300.00",
      shortfall: "0.00",
      debits: [],
      paid_personally: "0.00",
    },
    {
      t: 1,
      starts_on: "2027-10-25",
      age: 41,
      categories: [categoryYear("cash", "700.00", "0.00"), categoryYear("investments", "500.00", "0.00")],
      opening: "1200.00",
      closing: "0.00",
      need: "1500.00",
      shortfall: "300.00",
      debits: [],
      paid_personally: "0.00",
    },
  ],
  scenario: { id: 2, key: "likely", name: "Likely" },
  provenance: [
    { key: "return_rate", scope_type: "category", scope_ref: "investments", value: "7", layer: "household" },
    { key: "annual_saving", scope_type: "household", scope_ref: "", value: "20000.00", layer: "household" },
    { key: "retirement_age", scope_type: "member", scope_ref: "1", value: "55", layer: "scenario" },
  ],
  ...over,
});

const defaultsLayer: Layer = {
  scenario_id: null,
  rows: [
    { key: "return_rate", scope_type: "category", scope_ref: "investments", value: "7", overridden_by: ["Best", "Worst"] },
    { key: "annual_saving", scope_type: "household", scope_ref: "", value: "20000.00", overridden_by: [] },
  ],
};

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

type Handlers = {
  project?: (scenarioId: number) => Response;
  save?: (body: Record<string, unknown>) => Response;
};

function fakeBackend({ project, save }: Handlers = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      const run = path.match(/^\/api\/projection\/scenarios\/(\d+)$/);
      if (run) {
        const id = Number(run[1]);
        return project
          ? project(id)
          : json(projection({ scenario: { id, key: scenarios[id - 1].key, name: scenarios[id - 1].name } }));
      }
      if (path === "/api/scenarios" && request.method === "GET") return json(scenarios);
      if (path === "/api/scenarios" && request.method === "POST") {
        return json({ id: 4, key: null, name: body?.name, notes: null, override_count: 0, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true }, 201);
      }
      const order = path.match(/^\/api\/scenarios\/(\d+)\/drawdown-order$/);
      if (order && request.method === "PUT") {
        const found = scenarios[Number(order[1]) - 1];
        return json({ ...found, drawdown_is_default: body?.order === null });
      }
      if (path === "/api/assumptions" && request.method === "GET") {
        return json(url.searchParams.has("scenario_id") ? { scenario_id: 2, rows: [] } : defaultsLayer);
      }
      if (path === "/api/assumptions" && request.method === "PUT") {
        return save ? save(body ?? {}) : json({ ...defaultsLayer, review: [] } satisfies SavedAssumptions);
      }
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const runs = (calls: Call[]) => calls.filter((c) => c.path.startsWith("/api/projection/scenarios/"));

test("a line debit is listed with what was requested, paid and paid personally, and flagged when unpaid", async () => {
  const base = projection();
  const debit = (over: Record<string, string>) => ({
    member_id: 1,
    category_key: "superannuation",
    reason: "div293",
    ref: "FY2025-26",
    requested: "4500.00",
    paid: "4500.00",
    unpaid: "0.00",
    ...over,
  });
  const rows = base.rows.map((r, i) =>
    i === 0
      ? { ...r, debits: [debit({})] }
      : { ...r, debits: [debit({ ref: "FY2026-27", paid: "3000.00", unpaid: "1500.00" })], paid_personally: "1500.00" },
  );
  fakeBackend({ project: () => json({ ...base, rows }) });
  await renderAppAt("/projection");

  const list = await screen.findByTestId("line-debit-rows");
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Division 293 (FY2025-26) (Alex Sample)");
  expect(items[0]).toHaveTextContent("Requested 4,500.00, paid from superannuation 4,500.00, paid personally 0.00.");
  expect(items[1]).toHaveTextContent("Requested 4,500.00, paid from superannuation 3,000.00, paid personally 1,500.00.");
  const notices = screen.getAllByTestId("debit-unpaid");
  expect(notices).toHaveLength(1); // only the debit with an unpaid part
  expect(notices[0]).toHaveTextContent(
    "Division 293 could not be fully paid from superannuation in year 2 (Alex Sample): 1,500.00 paid personally.",
  );
});

test("a run with no line debits shows no debit section", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  await screen.findByTestId("projection-table");
  expect(screen.queryByTestId("line-debits")).not.toBeInTheDocument();
});

test("Likely is shown on arrival, with the Scenarios offered as links", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");

  const headline = await screen.findByTestId("headline");
  expect(within(headline).getByText(/Funds last to age 41/)).toBeInTheDocument();
  expect(within(headline).getByText(/Scenario Likely/)).toBeInTheDocument();
  expect(runs(calls).map((c) => c.path)).toEqual(["/api/projection/scenarios/2"]);

  const switcher = screen.getByRole("region", { name: "Scenario" });
  expect(within(switcher).getAllByRole("link").map((l) => l.textContent)).toEqual(["Best", "Likely", "Worst", "Compare Scenarios"]);
  expect(within(switcher).getByRole("link", { name: "Likely" })).toHaveAttribute("aria-current", "page");
});

test("switching Scenario puts it in the URL and shows that Scenario's result", async () => {
  const calls = fakeBackend({
    project: (id) =>
      json(
        id === 1
          ? projection({ lasts_beyond_horizon: true, funds_last_to_age: null, scenario: { id: 1, key: "best", name: "Best" } })
          : projection(),
      ),
  });
  const { router } = await renderAppAt("/projection");
  await screen.findByText(/Funds last to age 41/);

  fireEvent.click(within(screen.getByRole("region", { name: "Scenario" })).getByRole("link", { name: "Best" }));

  expect(await screen.findByText(/Funds last beyond the horizon/)).toBeInTheDocument();
  expect(router.state.location.search).toEqual({ scenario: 1 });
  expect(runs(calls).map((c) => c.path)).toContain("/api/projection/scenarios/1");
  expect(screen.getByRole("link", { name: "Best" })).toHaveAttribute("aria-current", "page");
});

test("a Scenario in the URL is selected on arrival", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection?scenario=3");
  await screen.findByTestId("headline");
  expect(runs(calls).map((c) => c.path)).toEqual(["/api/projection/scenarios/3"]);
  expect(screen.getByRole("link", { name: "Worst" })).toHaveAttribute("aria-current", "page");
});

test("the Assumptions used are listed with the layer that set each", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  const panel = await screen.findByTestId("assumptions-used");

  const rows = within(panel).getAllByRole("listitem");
  expect(within(rows[0]).getByText("Investments return per year (%)")).toBeInTheDocument();
  expect(within(rows[0]).getByText("7%")).toBeInTheDocument();
  expect(within(rows[0]).getByText("Household default")).toBeInTheDocument();
  expect(within(rows[1]).getByText("20,000.00")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Retirement age: Alex Sample")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Likely")).toBeInTheDocument(); // the Scenario's own value
  expect(within(panel).getByText(/illustrative placeholders/)).toBeInTheDocument();
});

test("editing the Household defaults sends only what changed, then flags the Scenarios that did not move", async () => {
  const calls = fakeBackend({
    save: () =>
      json({
        ...defaultsLayer,
        review: [{ key: "return_rate", scope_type: "category", scope_ref: "investments", scenarios: ["Best", "Worst"] }],
      }),
  });
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household defaults" }));

  const field = await screen.findByLabelText("Investments return per year (%)");
  expect(field).toHaveValue("7");
  expect(screen.getByText("Best, Worst state their own value, so will not move.")).toBeInTheDocument();
  fireEvent.change(field, { target: { value: "6.5" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  const notice = await screen.findByTestId("review-notice");
  expect(within(notice).getByText("Investments return per year (%):")).toBeInTheDocument();
  expect(within(notice).getByRole("link", { name: "Best" })).toBeInTheDocument();
  expect(within(notice).getByRole("link", { name: "Worst" })).toBeInTheDocument();

  const put = calls.find((c) => c.method === "PUT");
  expect(put?.body).toEqual({
    scenario_id: null,
    changes: [{ key: "return_rate", scope_type: "category", scope_ref: "investments", value: "6.5" }],
  });
  fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }));
  expect(screen.queryByTestId("review-notice")).not.toBeInTheDocument();
});

test("the Tax assumptions are choices, and picking one sends its word", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely" }));

  const tax = await screen.findByLabelText("Tax in the projection");
  expect(tax.tagName).toBe("SELECT");
  expect(tax).toHaveValue(""); // not stated: off
  fireEvent.change(tax, { target: { value: "on" } });
  fireEvent.change(screen.getByLabelText("Division 293 paid by: Alex Sample"), { target: { value: "super" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
    scenario_id: 2,
    changes: [
      { key: "tax_in_projection", scope_type: "household", scope_ref: "", value: "on" },
      { key: "div293_payer", scope_type: "member", scope_ref: "1", value: "super" },
    ],
  });
});

test("a Scenario's own sheet shows what it inherits and clearing a field removes the override", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely" }));

  const field = await screen.findByLabelText("Investments return per year (%)");
  expect(field).toHaveValue("");
  expect(field).toHaveAttribute("placeholder", "7"); // the Household default it inherits
  fireEvent.change(field, { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
    scenario_id: 2,
    changes: [{ key: "return_rate", scope_type: "category", scope_ref: "investments", value: "5" }],
  });
});

test("a rejected value is shown beside its field and the sheet stays open", async () => {
  fakeBackend({
    save: () => json({ errors: [{ field: "inflation", message: "Rates have at most 4 decimal places of a percent." }] }, 422),
  });
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household defaults" }));
  fireEvent.change(await screen.findByLabelText("Inflation per year (%)"), { target: { value: "2.55555" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText(/at most 4 decimal places/)).toBeInTheDocument();
  expect(screen.getByLabelText("Inflation per year (%)")).toBeInTheDocument();
});

test("an unset Assumption is named, with the way to set it", async () => {
  fakeBackend({
    project: () =>
      json(
        {
          errors: [
            { field: "spending_phases", message: "Spending has no phases. Set it in the Household defaults or in Likely." },
          ],
        },
        422,
      ),
  });
  await renderAppAt("/projection");
  const box = await screen.findByTestId("incomplete");
  expect(within(box).getByText(/Spending has no phases/)).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Edit Household defaults" })).toBeInTheDocument();
});

test("a Scenario is added, copying the current one, and then selected", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: /Add Scenario/ }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Lean years" } });
  expect(screen.getByLabelText(/Start from the Assumptions in Likely/)).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Add Scenario" }));

  await waitFor(() => expect(router.state.location.search).toEqual({ scenario: 4 }));
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/scenarios")?.body).toEqual({
    name: "Lean years",
    copy_from: 2,
  });
});

test("the sustainable retirement income is shown beside the headline", async () => {
  fakeBackend();
  await renderAppAt("/projection");

  const box = await screen.findByTestId("sustainable");
  expect(within(box).getByText("1,234.56")).toBeInTheDocument();
  expect(within(box).queryByText("At least")).not.toBeInTheDocument();
  expect(within(box).getByText(/still be met in full to age 42/)).toBeInTheDocument();
});

test("a capped sustainable amount is shown as a minimum", async () => {
  fakeBackend({
    project: () => json(projection({ sustainable: { annual_spending: "1000000000.00", capped: true } })),
  });
  await renderAppAt("/projection");

  const box = await screen.findByTestId("sustainable");
  expect(within(box).getByText("At least")).toBeInTheDocument();
  expect(within(box).getByText(/No limit was found/)).toBeInTheDocument();
});

test("the stated simplifications are on the screen beside the answer", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  const panel = await screen.findByTestId("simplifications");
  expect(within(panel).getByText(/Property, vehicles and loans are not projected/)).toBeInTheDocument();
  expect(within(panel).getByText(/later of its owner.s retirement and preservation age/)).toBeInTheDocument();
  // The old wording ("nothing is saved and nothing is spent") went with B7: a salary now funds
  // spending, and spending before the last retirement is a phase from the projection start.
  expect(within(panel).getByText(/spending phase starting at the projection start/)).toBeInTheDocument();
  expect(within(panel).getByText(/except for a Member with a salary stream/)).toBeInTheDocument();
  expect(within(panel).getByText(/Salary: take-home pay is the amount entered/)).toBeInTheDocument();
  expect(within(panel).getByText(/split equally across Members/)).toBeInTheDocument();
  expect(within(panel).getByText(/headline age is the youngest Member/)).toBeInTheDocument();
});

test("the year table shows balances by Category and marks the first short year", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  const table = await screen.findByTestId("projection-table");
  expect(within(table).getByRole("columnheader", { name: "Cash" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Investments" })).toBeInTheDocument();
  const start = within(table).getByTestId("start-row");
  expect(within(start).getByText("1,000.00")).toBeInTheDocument();
  const rows = within(table).getAllByRole("row");
  const short = rows.filter((r) => r.getAttribute("data-short") === "true");
  expect(short).toHaveLength(1);
  expect(within(short[0]).getByText("Short")).toBeInTheDocument();
  expect(within(short[0]).getByText("41")).toBeInTheDocument(); // the headline age, at the start of that year
});

test("Accounts left out are listed with the reason", async () => {
  fakeBackend({
    project: () =>
      json(
        projection({
          flagged: [{ account_id: 7, name: "Sample UK Pension", category_key: "overseas_pensions", status: "no_rate" }],
        }),
      ),
  });
  await renderAppAt("/projection");
  const list = await screen.findByTestId("flagged-accounts");
  expect(within(list).getByText(/Sample UK Pension/)).toBeInTheDocument();
  expect(within(list).getByText(/no FX rate/)).toBeInTheDocument();
});

test("the sidebar links to the Projection screen", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/position");
  fireEvent.click(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("link", { name: "Projection" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/projection"));
  expect(await screen.findByRole("heading", { name: "Projection" })).toBeInTheDocument();
});

test("the drawdown order used is shown with the Assumptions", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  const panel = await screen.findByTestId("assumptions-used");
  expect(within(panel).getByTestId("drawdown-order")).toHaveTextContent("Drawdown order: Cash, then Investments.");
});

test("moving a Category in a Scenario's sheet saves the whole order, and Reset returns to the default", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely" }));
  const editor = await screen.findByTestId("drawdown-order-editor");
  expect(within(editor).getByText("This is the default order.")).toBeInTheDocument();
  expect(within(editor).getByRole("button", { name: "Reset to default order" })).toBeDisabled();
  expect(within(editor).getByRole("button", { name: "Move Cash up" })).toBeDisabled();

  fireEvent.click(within(editor).getByRole("button", { name: "Move Investments up" }));
  expect(within(editor).getAllByRole("listitem").map((l) => l.textContent)).toEqual([
    "1.Investments",
    "2.Cash",
    "3.Superannuation",
    "4.Overseas pensions",
  ]);
  expect(within(editor).getByText("This Scenario states its own order.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(calls.some((c) => c.path === "/api/scenarios/2/drawdown-order")).toBe(true));
  expect(calls.find((c) => c.path === "/api/scenarios/2/drawdown-order")?.body).toEqual({
    order: ["investments", "cash", "superannuation", "overseas_pensions"],
  });
});

test("Reset to default order sends null, and an untouched order sends nothing", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely" }));
  const editor = await screen.findByTestId("drawdown-order-editor");
  fireEvent.click(within(editor).getByRole("button", { name: "Move Cash down" }));
  fireEvent.click(within(editor).getByRole("button", { name: "Reset to default order" }));
  expect(within(editor).getAllByRole("listitem")[0]).toHaveTextContent("Cash");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/assumptions" && c.method === "PUT")).toBe(true));
  expect(calls.some((c) => c.path.endsWith("/drawdown-order"))).toBe(false);
});

test("the Household defaults sheet has no order to edit", async () => {
  fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household defaults" }));
  await screen.findByLabelText("Investments return per year (%)");
  expect(screen.queryByTestId("drawdown-order-editor")).not.toBeInTheDocument();
});

test("each Member's Rule pack ages are shown with the pack version and the sources", async () => {
  fakeBackend({ project: () => json(projection({ rules: rules({ pension: [66, 6] }) })) });
  await renderAppAt("/projection");

  const panel = await screen.findByTestId("rules-used");
  expect(within(panel).getByText("au/2026-27/r1")).toBeInTheDocument();
  const alex = within(screen.getByTestId("rules-member-1"));
  expect(alex.getByText("Alex Sample")).toBeInTheDocument();
  expect(alex.getByText("60 years")).toBeInTheDocument();
  expect(alex.getByText("65 years")).toBeInTheDocument();
  expect(alex.getByText("66 years 6 months")).toBeInTheDocument();
  expect(within(screen.getByTestId("rules-member-2")).getByText("66 years 6 months")).toBeInTheDocument();
  expect(within(panel).getByText(/Social Security Act 1991 s 23/)).toBeInTheDocument();
});

test("a Scenario's own Rule pack age is labelled with the Scenario", async () => {
  fakeBackend({ project: () => json(projection({ rules: rules({ pension: [70, 0], layer: "scenario" }) })) });
  await renderAppAt("/projection");

  const alex = within(await screen.findByTestId("rules-member-1"));
  expect(alex.getByText("70 years")).toBeInTheDocument();
  expect(alex.getByText("Likely")).toBeInTheDocument();
});

test("the Life events timeline lists each Member's dates in order and shows the gap before super opens", async () => {
  fakeBackend({ project: () => json(projection()) });
  await renderAppAt("/projection");

  const alex = within(await screen.findByTestId("life-events-member-1"));
  const rows = alex.getAllByRole("listitem").map((li) => li.textContent);
  expect(rows[0]).toMatch(/Retirement at 55 years.*2039-03-12/);
  expect(rows[1]).toMatch(/Preservation age at 60 years.*2044-03-12/);
  expect(rows[2]).toMatch(/Unrestricted super age at 65 years.*2049-03-12/);
  expect(alex.getByTestId("super-opens-1")).toHaveTextContent("2044-10-25");
  expect(screen.getByTestId("super-gap-1")).toHaveTextContent(/Alex Sample retires 5 years before Superannuation/);
  // Jordan retires at preservation age: no gap.
  expect(screen.queryByTestId("super-gap-2")).not.toBeInTheDocument();
  expect(screen.getByTestId("simplifications")).not.toHaveTextContent(/Superannuation and Overseas pensions can be drawn from each/);
  expect(screen.getByTestId("simplifications")).toHaveTextContent(/later of its owner's retirement and preservation age/);
});

test("a Scenario's rule override is labelled on the timeline", async () => {
  const events = lifeEvents().map((e) =>
    e.member_id === 1 && e.kind === "preservation_age" ? { ...e, source: "scenario" } : e,
  );
  fakeBackend({ project: () => json(projection({ life_events: events })) });
  await renderAppAt("/projection");

  const alex = within(await screen.findByTestId("life-events-member-1"));
  expect(alex.getByText("Likely")).toBeInTheDocument();
});

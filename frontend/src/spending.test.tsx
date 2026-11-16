import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { Scenario, ScenarioProjection, SpendingLayer, SpendingSaved } from "@/lib/scenarios";
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

const ORDER = [
  { key: "cash", name: "Cash" },
  { key: "investments", name: "Investments" },
];

const scenario = (id: number, key: string, name: string, own = false): Scenario => ({
  id,
  key,
  name,
  notes: null,
  override_count: 0,
  drawdown_order: ORDER,
  drawdown_is_default: true,
  spending_is_default: !own,
});
const scenarios = [scenario(1, "best", "Best"), scenario(2, "likely", "Likely"), scenario(3, "worst", "Worst", true)];

// Invented: the Household default is two phases; Worst states its own single phase.
const DEFAULT_PHASES = [
  { anchor: "last_retirement", offset_years: 0, annual_amount: "70000.00" },
  { anchor: "last_retirement", offset_years: 10, annual_amount: "55000.00" },
];
const WORST_PHASES = [{ anchor: "last_retirement", offset_years: 0, annual_amount: "85000.00" }];

const projection = (id: number): ScenarioProjection => ({
  engine_version: 1,
  input_version: 1,
  as_at: "2026-10-25",
  years: 2,
  reference_member: { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  funds_last_to_age: null,
  lasts_beyond_horizon: true,
  sustainable: { annual_spending: "1234.56", capped: false },
  categories: ORDER,
  members: [
    { id: 1, name: "Alex Sample", age_now: 42, retirement_year: 18 },
    { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  ],
  flagged: [],
  rules: null,
  life_events: null,
  super_access: [],
  income: null,
  tax: null,
  spending:
    id === 3
      ? {
          layer: "scenario",
          phases: [{ ...WORST_PHASES[0], from_year: 20, until_year: null, empty: false }],
        }
      : {
          layer: "household",
          phases: [
            { ...DEFAULT_PHASES[0], from_year: 20, until_year: 30, empty: false },
            { ...DEFAULT_PHASES[1], from_year: 30, until_year: null, empty: false },
          ],
        },
  rows: [
    {
      t: 0,
      starts_on: "2026-10-25",
      age: 40,
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
    },
  ],
  scenario: { id, key: scenarios[id - 1].key, name: scenarios[id - 1].name },
  provenance: [],
});

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };
type Handlers = { save?: (body: Record<string, unknown>) => Response };

function fakeBackend({ save }: Handlers = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      const run = path.match(/^\/api\/projection\/scenarios\/(\d+)$/);
      if (run) return json(projection(Number(run[1])));
      if (path === "/api/scenarios") return json(scenarios);
      if (path === "/api/spending-phases" && request.method === "GET") {
        const id = url.searchParams.get("scenario_id");
        return json({
          scenario_id: id === null ? null : Number(id),
          phases: id === null ? DEFAULT_PHASES : id === "3" ? WORST_PHASES : [],
          overridden_by: id === null ? ["Worst"] : [],
        } satisfies SpendingLayer);
      }
      if (path === "/api/spending-phases" && request.method === "PUT") {
        return save
          ? save(body ?? {})
          : json({ scenario_id: null, phases: DEFAULT_PHASES, overridden_by: [], review: [] } satisfies SpendingSaved);
      }
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT" && c.path === "/api/spending-phases");

test("the Projection shows the spending phases it used, as ages, and which layer supplied them", async () => {
  fakeBackend();
  await renderAppAt("/projection");

  const card = await screen.findByTestId("spending-used");
  expect(within(card).getByText("Spending in Likely")).toBeInTheDocument();
  expect(within(card).getByText(/uses the Household default list/)).toBeInTheDocument();
  expect(within(card).getByText("Household default")).toBeInTheDocument();
  const [first, second] = within(card).getAllByTestId("spending-phase");
  expect(first).toHaveTextContent("From the last retirement");
  expect(first).toHaveTextContent("From age 60 to 70"); // the youngest Member is 40: years 20 to 30
  expect(first).toHaveTextContent("70,000.00");
  expect(second).toHaveTextContent("10 years after the last retirement");
  expect(second).toHaveTextContent("to the horizon");
  expect(second).toHaveTextContent("55,000.00");
});

test("a Scenario with its own phases says so", async () => {
  fakeBackend();
  await renderAppAt("/projection?scenario=3");
  const card = await screen.findByTestId("spending-used");
  expect(within(card).getByText(/states its own spending phases/)).toBeInTheDocument();
  expect(within(card).getByText("Worst")).toBeInTheDocument(); // the badge
  expect(within(card).getAllByTestId("spending-phase")).toHaveLength(1);
});

test("the Household spending sheet lists the phases, and an added phase is sent with the whole list", async () => {
  const calls = fakeBackend({
    save: () =>
      json({
        scenario_id: null,
        phases: DEFAULT_PHASES,
        overridden_by: ["Worst"],
        review: [{ key: "spending_phases", scope_type: "household", scope_ref: "", scenarios: ["Worst"] }],
      }),
  });
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household spending" }));

  expect(await screen.findByLabelText("Phase 1 amount a year")).toHaveValue("70000.00");
  expect(screen.getByLabelText("Phase 2 years after")).toHaveValue("10");
  expect(screen.getByLabelText("Phase 1 starts after")).toHaveValue("last_retirement");

  fireEvent.click(screen.getByRole("button", { name: /Add phase/ }));
  fireEvent.change(screen.getByLabelText("Phase 3 starts after"), { target: { value: "start" } });
  fireEvent.change(screen.getByLabelText("Phase 3 years after"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Phase 3 amount a year"), { target: { value: "30000" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  const notice = await screen.findByTestId("review-notice");
  expect(within(notice).getByText("Household spending phases:")).toBeInTheDocument();
  expect(within(notice).getByRole("link", { name: "Worst" })).toBeInTheDocument();
  expect(puts(calls)[0].body).toEqual({
    scenario_id: null,
    phases: [
      { anchor: "last_retirement", offset_years: 0, annual_amount: "70000.00" },
      { anchor: "last_retirement", offset_years: 10, annual_amount: "55000.00" },
      { anchor: "start", offset_years: 2, annual_amount: "30000" },
    ],
  });
});

test("a phase is edited and another removed, and the list sent is the whole new list", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household spending" }));
  fireEvent.change(await screen.findByLabelText("Phase 1 amount a year"), { target: { value: "72000" } });
  fireEvent.click(screen.getByRole("button", { name: "Remove phase 2" }));
  expect(screen.queryByLabelText("Phase 2 amount a year")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(puts(calls)).toHaveLength(1));
  expect(puts(calls)[0].body).toEqual({
    scenario_id: null,
    phases: [{ anchor: "last_retirement", offset_years: 0, annual_amount: "72000" }],
  });
});

test("an offset that is not a whole number is sent so the server names the field", async () => {
  const calls = fakeBackend({
    save: () =>
      json({ errors: [{ field: "spending_phases.0.offset_years", message: "Enter a whole number of years from 0 to 120." }] }, 422),
  });
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household spending" }));
  fireEvent.change(await screen.findByLabelText("Phase 1 years after"), { target: { value: "ten" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText(/whole number of years from 0 to 120/)).toBeInTheDocument();
  expect(puts(calls)[0].body?.phases).toMatchObject([{ offset_years: -1 }, { offset_years: 10 }]);
  // The sheet stays open with what was typed.
  expect(screen.getByLabelText("Phase 1 years after")).toHaveValue("ten");
});

test("field errors appear beside the phase they belong to", async () => {
  fakeBackend({
    save: () =>
      json(
        {
          errors: [
            { field: "spending_phases.1.annual_amount", message: "Enter an amount above 0." },
            { field: "spending_phases.1.offset_years", message: "Another phase already starts at this point. Each phase needs its own." },
          ],
        },
        422,
      ),
  });
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Household spending" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save" }));

  const [first, second] = await screen.findAllByTestId("phase-row");
  expect(within(second).getByText("Enter an amount above 0.")).toBeInTheDocument();
  expect(within(second).getByText(/Each phase needs its own/)).toBeInTheDocument();
  expect(within(first).queryByText("Enter an amount above 0.")).not.toBeInTheDocument();
});

test("a Scenario that inherits shows the default list and gets its own only when asked", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely spending" }));

  expect(await screen.findByTestId("spending-inherited")).toHaveTextContent(/Likely uses the Household default phases/);
  const inherited = screen.getByTestId("inherited-phases");
  expect(within(inherited).getByText("70,000.00")).toBeInTheDocument();
  expect(screen.queryByLabelText("Phase 1 amount a year")).not.toBeInTheDocument();

  // Saving without giving it its own list sends null: it keeps inheriting.
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(puts(calls)).toHaveLength(1));
  expect(puts(calls)[0].body).toEqual({ scenario_id: 2, phases: null });
});

test("giving a Scenario its own phases starts from the default and replaces it whole", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Likely spending" }));
  fireEvent.click(await screen.findByRole("button", { name: "Give Likely its own phases" }));

  expect(screen.getByLabelText("Phase 1 amount a year")).toHaveValue("70000.00");
  expect(screen.getByText(/replaces the Household default as a whole/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove phase 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(puts(calls)).toHaveLength(1));
  expect(puts(calls)[0].body).toEqual({
    scenario_id: 2,
    phases: [{ anchor: "last_retirement", offset_years: 0, annual_amount: "70000.00" }],
  });
});

test("a Scenario with its own phases can go back to the Household default", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection?scenario=3");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Worst spending" }));
  expect(await screen.findByLabelText("Phase 1 amount a year")).toHaveValue("85000.00");
  fireEvent.click(screen.getByRole("button", { name: "Use the Household default phases" }));
  expect(screen.getByTestId("spending-inherited")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(puts(calls)).toHaveLength(1));
  expect(puts(calls)[0].body).toEqual({ scenario_id: 3, phases: null });
});

test("a Scenario's own phases are the first sheet shown when it has some", async () => {
  fakeBackend();
  await renderAppAt("/projection?scenario=3");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Worst spending" }));
  expect(await screen.findAllByTestId("phase-row")).toHaveLength(1);
  expect(screen.queryByTestId("spending-inherited")).not.toBeInTheDocument();
});

test("a Scenario that cannot be projected for want of spending offers the spending editor", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.startsWith("/api/projection/scenarios/")) {
        return json({ errors: [{ field: "spending_phases", message: "Spending has no phases. Set it in the Household defaults or in Likely." }] }, 422);
      }
      if (path === "/api/scenarios") return json(scenarios);
      if (path === "/api/household") return json(household);
      return json({ message: "not mocked" }, 404);
    }),
  );
  await renderAppAt("/projection");
  const box = await screen.findByTestId("incomplete");
  expect(within(box).getByText(/Spending has no phases/)).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Edit Household spending" })).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Edit Likely spending" })).toBeInTheDocument();
});

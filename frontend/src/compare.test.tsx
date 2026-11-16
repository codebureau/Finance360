import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { ComparedScenario, Comparison } from "@/lib/scenarios";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};

const DEFAULT_ORDER = [
  { key: "cash", name: "Cash" },
  { key: "investments", name: "Investments" },
  { key: "superannuation", name: "Superannuation" },
  { key: "overseas_pensions", name: "Overseas pensions" },
];

const scenarios = [
  { id: 1, key: "best", name: "Best", notes: null, override_count: 1, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true, spending_is_default: true },
  { id: 2, key: "likely", name: "Likely", notes: null, override_count: 0, drawdown_order: DEFAULT_ORDER, drawdown_is_default: true, spending_is_default: true },
];

// Invented figures.
const compared = (id: number, name: string, over: Partial<ComparedScenario> = {}): ComparedScenario => ({
  scenario: { id, key: name.toLowerCase(), name },
  resolved: true,
  errors: [],
  horizon_age: 95,
  drawdown_order: DEFAULT_ORDER,
  funds_last_to_age: null,
  lasts_beyond_horizon: true,
  sustainable: { annual_spending: "90000.00", capped: false },
  spending: null,
  provenance: [
    {
      key: "annual_saving",
      scope_type: "household",
      scope_ref: "",
      value: id === 1 ? "80000.00" : "70000.00",
      layer: id === 1 ? "scenario" : "household",
    },
    { key: "horizon_age", scope_type: "household", scope_ref: "", value: "95", layer: "household" },
  ],
  ...over,
});

function stub(data: Comparison) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/projection/compare") return json(data);
      if (path === "/api/scenarios") return json(scenarios);
      if (path === "/api/household") return json(household);
      if (path.startsWith("/api/projection/scenarios/")) return new Promise<Response>(() => {});
      return json({ message: "not mocked" }, 404);
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

test("Compare Scenarios is offered beside the Scenario switcher", async () => {
  stub({ scenarios: [], differing: [], drawdown_differs: false, spending_differs: false });
  const { router } = await renderAppAt("/projection");
  const switcher = await screen.findByRole("region", { name: "Scenario" });
  fireEvent.click(within(switcher).getByRole("link", { name: "Compare Scenarios" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/projection/compare"));
});

test("Scenarios sit side by side with only the differing Assumptions listed", async () => {
  stub({
    scenarios: [
      compared(1, "Best"),
      compared(2, "Likely", {
        lasts_beyond_horizon: false,
        funds_last_to_age: 81,
        sustainable: { annual_spending: "55000.00", capped: false },
      }),
    ],
    differing: [{ key: "annual_saving", scope_ref: "" }],
    drawdown_differs: false,
    spending_differs: false,
  });
  await renderAppAt("/projection/compare");
  const table = await screen.findByTestId("comparison-table");

  expect(within(screen.getByTestId("result-1")).getByText("Beyond the horizon")).toBeInTheDocument();
  expect(within(screen.getByTestId("result-2")).getByText("Funds last to age 81")).toBeInTheDocument();
  expect(within(screen.getByTestId("sustainable-1")).getByText("90,000.00")).toBeInTheDocument();
  expect(within(screen.getByTestId("sustainable-2")).getByText("55,000.00")).toBeInTheDocument();

  const rows = within(table).getAllByTestId("differing-row");
  expect(rows).toHaveLength(1);
  expect(within(rows[0]).getByText("Annual saving ($)")).toBeInTheDocument();
  expect(within(rows[0]).getByText("80,000.00")).toBeInTheDocument();
  expect(within(rows[0]).getByText("70,000.00")).toBeInTheDocument();
  expect(within(rows[0]).getAllByText("Own")).toHaveLength(1);
  expect(within(table).queryByText("Horizon age")).not.toBeInTheDocument();
});

test("an unresolvable Scenario shows its message in its own column and the others still show", async () => {
  stub({
    scenarios: [
      compared(1, "Best"),
      compared(2, "Likely", {
        resolved: false,
        errors: [{ field: "spending_phases", message: "Spending has no phases. Set it in Likely." }],
        horizon_age: null,
        drawdown_order: [],
        funds_last_to_age: null,
        lasts_beyond_horizon: null,
        sustainable: null,
        provenance: [],
      }),
    ],
    differing: [],
    drawdown_differs: false,
    spending_differs: false,
  });
  await renderAppAt("/projection/compare");
  await screen.findByTestId("comparison-table");

  const bad = screen.getByTestId("result-2");
  expect(within(bad).getByText("Not projected")).toBeInTheDocument();
  expect(within(bad).getByText(/Spending has no phases/)).toBeInTheDocument();
  expect(within(bad).getByRole("link", { name: "Fix this in Likely" })).toHaveAttribute(
    "href",
    "/projection?scenario=2",
  );
  expect(within(screen.getByTestId("result-1")).getByText("Beyond the horizon")).toBeInTheDocument();
  expect(within(screen.getByTestId("sustainable-1")).getByText("90,000.00")).toBeInTheDocument();
});

test("the drawdown order is shown only when Scenarios differ in it", async () => {
  const own = [
    { key: "investments", name: "Investments" },
    { key: "cash", name: "Cash" },
    { key: "superannuation", name: "Superannuation" },
    { key: "overseas_pensions", name: "Overseas pensions" },
  ];
  stub({
    scenarios: [compared(1, "Best"), compared(2, "Likely", { drawdown_order: own })],
    differing: [],
    drawdown_differs: true,
    spending_differs: false,
  });
  await renderAppAt("/projection/compare");
  const row = await screen.findByTestId("drawdown-row");
  const [first, second] = within(row).getAllByRole("list");
  expect(within(first).getAllByRole("listitem").map((l) => l.textContent)).toEqual(DEFAULT_ORDER.map((c) => c.name));
  expect(within(second).getAllByRole("listitem").map((l) => l.textContent)).toEqual(own.map((c) => c.name));
});

const phase = (offset: number, amount: string, from: number, until: number | null) => ({
  anchor: "last_retirement",
  offset_years: offset,
  annual_amount: amount,
  from_year: from,
  until_year: until,
  empty: false,
});

test("spending phases get a row, with each Scenario's list, only when the lists differ", async () => {
  stub({
    scenarios: [
      compared(1, "Best", {
        spending: { layer: "household", phases: [phase(0, "70000.00", 20, 30), phase(10, "55000.00", 30, null)] },
      }),
      compared(2, "Likely", {
        spending: { layer: "scenario", phases: [phase(0, "85000.00", 20, null)] },
      }),
    ],
    differing: [],
    drawdown_differs: false,
    spending_differs: true,
  });
  await renderAppAt("/projection/compare");
  const row = await screen.findByTestId("spending-row");
  const [first, second] = within(row).getAllByRole("list");
  const text = (list: HTMLElement) => within(list).getAllByRole("listitem").map((l) => l.textContent);
  expect(text(first)).toEqual([
    expect.stringContaining("70,000.00"),
    expect.stringContaining("55,000.00"),
  ]);
  expect(text(first)[1]).toContain("10 years after the last retirement");
  expect(text(second)).toEqual([expect.stringContaining("85,000.00")]);
  expect(text(second)[0]).toContain("From the last retirement");
  expect(within(row).getAllByText("Own")).toHaveLength(1); // only the Scenario that states its own
});

test("no spending row when every Scenario uses the same phases", async () => {
  stub({ scenarios: [compared(1, "Best"), compared(2, "Likely")], differing: [], drawdown_differs: false, spending_differs: false });
  await renderAppAt("/projection/compare");
  await screen.findByTestId("comparison-table");
  expect(screen.queryByTestId("spending-row")).not.toBeInTheDocument();
});

test("no drawdown row when every Scenario uses the same order", async () => {
  stub({ scenarios: [compared(1, "Best"), compared(2, "Likely")], differing: [], drawdown_differs: false, spending_differs: false });
  await renderAppAt("/projection/compare");
  await screen.findByTestId("comparison-table");
  expect(screen.queryByTestId("drawdown-row")).not.toBeInTheDocument();
});

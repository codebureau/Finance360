import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { RunHeader, SavedRun } from "@/lib/projection-runs";
import type { Scenario } from "@/lib/scenarios";
import { openAllWorking, renderAppAt } from "@/test-utils";

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

const scenarios: Scenario[] = [
  {
    id: 2,
    key: "likely",
    name: "Likely",
    notes: null,
    override_count: 0,
    drawdown_is_default: true,
    spending_is_default: true,
    drawdown_order: [
      { key: "cash", name: "Cash" },
      { key: "investments", name: "Investments" },
    ],
  },
];

const header = (over: Partial<RunHeader> = {}): RunHeader => ({
  id: 7,
  created_at: "2026-10-26T01:30:00Z",
  label: "Baseline",
  scenario_id: 2,
  scenario_name: "Likely",
  engine_version: 1,
  current_engine: true,
  funds_last_to_age: 41,
  lasts_beyond_horizon: false,
  rule_pack_version: "au/2026-27/r1",
  ...over,
});

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

// Invented: two spending phases, as the run recorded them (the youngest Member is 40, retiring in year 20).
const phases = () => ({
  layer: "household",
  phases: [
    { anchor: "last_retirement", offset_years: 0, annual_amount: "70000.00", from_year: 20, until_year: 30, empty: false },
    { anchor: "last_retirement", offset_years: 10, annual_amount: "55000.00", from_year: 30, until_year: null, empty: false },
  ],
});

// Invented: one year. A run with no Rule pack version is one saved before spending phases too.
const saved = (run: RunHeader): SavedRun => ({
  engine_version: run.engine_version,
  input_version: 1,
  as_at: "2026-10-25",
  years: 1,
  reference_member: { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  funds_last_to_age: run.funds_last_to_age,
  lasts_beyond_horizon: run.lasts_beyond_horizon,
  sustainable: { annual_spending: "1234.56", capped: false },
  categories: [{ key: "cash", name: "Cash" }],
  members: [
    { id: 1, name: "Alex Sample", age_now: 42, retirement_year: 18 },
    { id: 2, name: "Jordan Sample", age_now: 40, retirement_year: 20 },
  ],
  flagged: [],
  rules: run.rule_pack_version === null ? null : rules(),
  life_events: run.rule_pack_version === null ? null : lifeEvents(),
  super_access: superAccess(),
  income: null,
  tax: null,
  age_pension: null,
  spending: run.rule_pack_version === null ? null : phases(),
  rows: [
    {
      t: 0,
      starts_on: "2026-10-25",
      age: 40,
      categories: [categoryYear("cash", "1000.00", "700.00")],
      opening: "1000.00",
      closing: "700.00",
      need: "300.00",
      shortfall: "0.00",
      debits: [],
      paid_personally: "0.00",
      age_pension: null,
    },
  ],
  scenario: { id: run.scenario_id, key: null, name: run.scenario_name },
  provenance: [
    { key: "return_rate", scope_type: "category", scope_ref: "cash", value: "3", layer: "household" },
    { key: "retirement_age", scope_type: "member", scope_ref: "1", value: "55", layer: "scenario" },
    // What a run saved before spending phases stored: the retired Assumption, still readable.
    ...(run.rule_pack_version === null
      ? [{ key: "annual_spending", scope_type: "household", scope_ref: "", value: "70000.00", layer: "household" }]
      : []),
  ],
  run,
});

type Call = { method: string; path: string; body?: Record<string, unknown> };

function fakeBackend(runs: RunHeader[] = [header()]) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body = request.method === "POST" ? await request.json().catch(() => undefined) : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/household") return json(household);
      if (path === "/api/scenarios") return json(scenarios);
      if (path === "/api/projection/scenarios/2") {
        const { run, ...projection } = saved(header());
        void run;
        return json({ ...projection, scenario: { id: 2, key: "likely", name: "Likely" } });
      }
      if (path === "/api/projection/runs" && request.method === "POST") {
        return json(header({ id: 8, label: (body?.label as string | null) ?? null }), 201);
      }
      if (path === "/api/projection/runs") return json(runs);
      const rerun = path.match(/^\/api\/projection\/runs\/(\d+)\/rerun$/);
      if (rerun) return json(header({ id: 9, label: "Re-run of run 7", engine_version: 2 }), 201);
      const one = path.match(/^\/api\/projection\/runs\/(\d+)$/);
      if (one) {
        const found = runs.find((r) => r.id === Number(one[1])) ?? header({ id: Number(one[1]), engine_version: 2 });
        return json(saved(found));
      }
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("Save run keeps the projection with an optional label, then links to the saved run", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  fireEvent.click(await screen.findByRole("button", { name: "Save run" }));
  fireEvent.change(await screen.findByLabelText("Label (optional)"), { target: { value: "Before the raise" } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save run" }));

  const notice = await screen.findByTestId("run-saved");
  expect(within(notice).getByRole("link", { name: "Open the saved run" })).toHaveAttribute(
    "href",
    "/projection/runs/8",
  );
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/projection/runs")?.body).toEqual({
    scenario_id: 2,
    label: "Before the raise",
  });
});

test("an empty label is sent as no label", async () => {
  const calls = fakeBackend();
  await renderAppAt("/projection");
  await openAllWorking();
  fireEvent.click(await screen.findByRole("button", { name: "Save run" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save run" }));
  await screen.findByTestId("run-saved");
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ scenario_id: 2, label: null });
});

test("the saved runs are listed with their result and engine, each opening its run", async () => {
  fakeBackend([
    header(),
    header({ id: 6, label: null, lasts_beyond_horizon: true, funds_last_to_age: null, engine_version: 0, current_engine: false }),
  ]);
  await renderAppAt("/projection/runs");
  const rows = await screen.findAllByRole("row");
  const [, first, second] = rows;
  expect(within(first).getByText("Baseline")).toBeInTheDocument();
  expect(within(first).getByText("Funds last to age 41")).toBeInTheDocument();
  expect(within(first).getByText("v1")).toBeInTheDocument();
  expect(within(first).getByRole("link")).toHaveAttribute("href", "/projection/runs/7");
  expect(within(second).getByText("No label")).toBeInTheDocument();
  expect(within(second).getByText("Lasts beyond the horizon")).toBeInTheDocument();
  expect(within(second).getByText("Older engine")).toBeInTheDocument();
});

test("with no saved runs the list says how to save one", async () => {
  fakeBackend([]);
  await renderAppAt("/projection/runs");
  expect(await screen.findByTestId("no-runs")).toHaveTextContent("Nothing saved yet");
});

test("a saved run shows its stored result and engine, with no re-run when the engine is current", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();
  expect(await screen.findByRole("heading", { name: "Baseline" })).toBeInTheDocument();
  const headline = await screen.findByTestId("headline");
  expect(within(headline).getByText(/Funds last to age 41/)).toBeInTheDocument();
  expect(within(headline).getByText(/Computed by engine v1/)).toBeInTheDocument();
  const used = screen.getByTestId("assumptions-used");
  expect(within(used).getByText("Retirement age: Alex Sample")).toBeInTheDocument();
  expect(within(used).getByText("55")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Re-run with current engine" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("engine-notice")).not.toBeInTheDocument();
});

test("a saved run leads with the same balances chart, ahead of its working", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  const chart = await screen.findByTestId("balances-chart");
  expect(screen.getByRole("heading", { name: "How the balances move" })).toBeInTheDocument();
  expect(chart).toHaveAttribute("data-form", "stacked-area");
  expect(chart.compareDocumentPosition(screen.getByTestId("assumptions-used")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByTestId("projection-table")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show working: Year by year" }));
  expect(await screen.findByTestId("projection-table")).toBeInTheDocument();
});

test("a run saved before the Age Pension was calculated says so", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();
  expect(await screen.findByTestId("age-pension-used")).toHaveTextContent(
    "saved before the Age Pension was calculated",
  );
});

test("an older engine's run offers a re-run, which saves a new run and opens it", async () => {
  const calls = fakeBackend([header({ engine_version: 1, current_engine: false })]);
  const { router } = await renderAppAt("/projection/runs/7");
  const notice = await screen.findByTestId("engine-notice");
  expect(within(notice).getByText(/exactly what engine v1 computed/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Re-run with current engine" }));

  await waitFor(() => expect(router.state.location.pathname).toBe("/projection/runs/9"));
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/projection/runs/7/rerun")).toBe(true);
});

test("a saved run shows the Rule pack ages it recorded", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  const panel = await screen.findByTestId("rules-used");
  expect(within(panel).getByText("au/2026-27/r1")).toBeInTheDocument();
  expect(within(screen.getByTestId("rules-member-1")).getByText("67 years")).toBeInTheDocument();
  expect(within(panel).getByText(/Social Security Act 1991 s 23/)).toBeInTheDocument();
});

test("a run saved before Rule packs were recorded still renders, saying it has no ages", async () => {
  fakeBackend([header({ rule_pack_version: null })]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  expect(await screen.findByTestId("headline")).toBeInTheDocument();
  expect(screen.getByTestId("rules-used")).toHaveTextContent(/saved before Rule packs were recorded/);
  expect(screen.queryByTestId("rules-member-1")).not.toBeInTheDocument();
});

test("a run saved before income streams were recorded still renders, saying it has none", async () => {
  fakeBackend([header()]); // the fake run carries income: null, as one saved before income streams
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  expect(await screen.findByTestId("headline")).toBeInTheDocument();
  expect(screen.getByTestId("income-used")).toHaveTextContent(/saved before income streams were recorded/);
});

test("a saved run shows the Life events it recorded and the super access date it used", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  const alex = within(await screen.findByTestId("life-events-member-1"));
  expect(alex.getByText(/Preservation age/)).toBeInTheDocument();
  expect(alex.getByTestId("super-opens-1")).toHaveTextContent("2044-10-25");
  expect(screen.getByTestId("super-gap-1")).toBeInTheDocument();
});

test("a run saved before Life events were recorded says so and still shows when super opens", async () => {
  fakeBackend([header({ rule_pack_version: null })]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  const panel = await screen.findByTestId("life-events");
  expect(panel).toHaveTextContent(/saved before Life events were recorded/);
  expect(within(panel).getByTestId("super-opens-1")).toHaveTextContent("2044-10-25");
  expect(within(panel).queryByTestId("super-gap-1")).not.toBeInTheDocument();
});

test("a saved run shows the spending phases it used, as ages, with where they came from", async () => {
  fakeBackend([header()]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  const panel = await screen.findByTestId("spending-phases");
  expect(within(panel).getByText("Household default")).toBeInTheDocument();
  const [first, second] = within(panel).getAllByTestId("spending-phase");
  expect(first).toHaveTextContent("From the last retirement");
  expect(first).toHaveTextContent("From age 60 to 70"); // the youngest Member is 40: year 20 to year 30
  expect(first).toHaveTextContent("70,000.00");
  expect(second).toHaveTextContent("10 years after the last retirement");
  expect(second).toHaveTextContent("From age 70 to the horizon");
  expect(second).toHaveTextContent("55,000.00");
});

test("a run saved before spending phases still renders, with the annual spending it stored", async () => {
  fakeBackend([header({ rule_pack_version: null })]);
  await renderAppAt("/projection/runs/7");
  await openAllWorking();

  expect(await screen.findByTestId("headline")).toBeInTheDocument();
  expect(screen.getByTestId("spending-phases")).toHaveTextContent(/saved before spending phases were recorded/);
  expect(screen.queryByTestId("spending-phase")).not.toBeInTheDocument();
  const used = screen.getByTestId("assumptions-used");
  expect(within(used).getByText("Annual spending (before spending phases)")).toBeInTheDocument();
  expect(within(used).getByText("70,000.00")).toBeInTheDocument();
});

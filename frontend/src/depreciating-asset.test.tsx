import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};
const labels = [
  {
    key: "business_expenses",
    return_item: "P8/15",
    name: "Business expenses",
    description: "",
    direction: "deduction",
    kind: "money",
    modelled: true,
    section: "business",
    record_types: ["line", "depreciating_asset"],
  },
  {
    key: "salary",
    return_item: "1",
    name: "Salary or wages",
    description: "",
    direction: "income",
    kind: "money",
    modelled: true,
    section: "business",
    record_types: ["line"],
  },
];
const setup = { rule_pack_version: "x", years: [], sections: [{ key: "business", name: "Sole trader" }], labels };

const year = (fy: number, days: number, opening: string, deduction: string, closing: string) => ({
  financial_year: fy,
  financial_year_label: `${fy - 1}-${String(fy % 100).padStart(2, "0")}`,
  days,
  opening,
  decline: deduction,
  deduction,
  closing,
});

const camera = {
  id: 5,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "depreciating_asset",
  from_on: "2026-01-01",
  to_on: null,
  description: "Invented camera",
  notes: null,
  label_key: "business_expenses",
  label_name: "Business expenses",
  return_item: "P8/15",
  kind: "money",
  amount: "2000.00",
  work_use_percent: "100",
  counted: null,
  value: null,
  statement: null,
  asset: {
    effective_life_years: "4",
    claim_in_full: false,
    multiplier_percent: 200,
    missing: [],
    schedule: [
      year(2026, 181, "2000.00", "495.89", "1504.11"),
      year(2027, 365, "1504.11", "752.06", "752.05"),
    ],
  },
};
const noFigure = {
  ...camera,
  id: 6,
  description: "Invented drone",
  asset: { ...camera.asset, multiplier_percent: null, missing: ["tax.depreciation.dv_percent"], schedule: [] },
};

type Call = { method: string; path: string; body?: Record<string, unknown> };

function fakeBackend(records: Record<string, unknown>[] = []) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/label-totals") {
        return json({
          financial_year: 2026,
          financial_year_label: "2025-26",
          totals: [],
          uncounted: [
            {
              record_id: 6,
              member_id: 1,
              description: "Invented drone",
              label_name: "Business expenses",
              missing: ["tax.depreciation.dv_percent"],
              note: "Left out of Business expenses: the Rule pack has no tax.depreciation.dv_percent for FY 2025-26.",
            },
          ],
        });
      }
      if (path === "/api/library/check") return json({ checked: 0, problems: [] });
      if (path === "/api/library/links") return json([]);
      if (path === "/api/tax/records" && request.method === "POST") return json({ ...camera, id: 99 }, 201);
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        const found = records.find((r) => r.id === Number(one[1]));
        return found ? json(found) : json({ message: "Tax record does not exist." }, 404);
      }
      if (path === "/api/tax/records") return json(records);
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("an asset is entered with its cost, effective life, work-use share and claim in full, all as text", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("What it records"), { target: { value: "depreciating_asset" } });
  // only the labels that take an asset are offered
  const options = within(form).getAllByRole("option").map((o) => o.textContent);
  expect(options).toContain("Business expenses (item P8/15)");
  expect(options).not.toContain("Salary or wages (item 1)");
  fireEvent.change(within(form).getByLabelText("Return label"), { target: { value: "business_expenses" } });
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Invented camera" } });
  fireEvent.change(within(form).getByLabelText(/^Start date/), { target: { value: "2026-01-01" } });
  fireEvent.change(within(form).getByLabelText("Cost ($)"), { target: { value: "2000.00" } });
  fireEvent.change(within(form).getByLabelText(/^Effective life \(years\)/), { target: { value: "4" } });
  fireEvent.change(within(form).getByLabelText(/^Work-use share/), { target: { value: "80" } });
  fireEvent.click(within(form).getByLabelText(/Claim in full in the first year/));
  // the form says eligibility is the household's to check
  expect(within(form).getByText(/does not check whether the asset is eligible/)).toBeInTheDocument();
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    member_id: 1,
    record_type: "depreciating_asset",
    label: "business_expenses",
    description: "Invented camera",
    from_on: "2026-01-01",
    to_on: null,
    notes: null,
    amount: "2000.00",
    work_use_percent: "80",
    effective_life_years: "4",
    claim_in_full: true,
  });
});

test("an asset's page shows its year-by-year schedule, each year linking to that year's records", async () => {
  fakeBackend([camera]);
  await renderAppAt("/tax/records/5");
  const table = await screen.findByRole("table", { name: "Depreciation schedule" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("2025-26");
  expect(rows[0]).toHaveTextContent("181 days");
  expect(rows[0]).toHaveTextContent("2,000.00");
  expect(rows[0]).toHaveTextContent("495.89");
  expect(rows[0]).toHaveTextContent("1,504.11");
  expect(rows[1]).toHaveTextContent("2026-27");
  expect(rows[1]).toHaveTextContent("752.06");
  expect(within(rows[1]).getByRole("link", { name: "2026-27" })).toHaveAttribute(
    "href",
    "/tax/records?fy=2027&label=business_expenses",
  );
  expect(screen.getByText("Still used")).toBeInTheDocument();
});

test("an asset whose figure is not captured says so and shows no invented schedule", async () => {
  fakeBackend([noFigure]);
  await renderAppAt("/tax/records/6");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("tax.depreciation.dv_percent");
  expect(alert).toHaveTextContent("Capture the figure on Rule figures (FY 2025-26).");
  expect(within(alert).getByRole("link", { name: "Rule figures (FY 2025-26)" })).toHaveAttribute(
    "href",
    "/rule-figures?fy=2026",
  );
  expect(alert).not.toHaveTextContent("rulepack_update");
  expect(screen.queryByRole("table", { name: "Depreciation schedule" })).not.toBeInTheDocument();
});

test("label totals name an asset they leave out, linking to it, and do not say the year is empty", async () => {
  fakeBackend([noFigure]);
  await renderAppAt("/tax/records?fy=2026");
  const notice = await screen.findByTestId("uncounted");
  expect(notice).toHaveTextContent("These totals are incomplete");
  expect(within(notice).getByRole("link", { name: "Invented drone" })).toHaveAttribute("href", "/tax/records/6");
  expect(notice).toHaveTextContent("Left out of Business expenses");
  expect(screen.queryByText("No Tax record falls in this financial year yet.")).not.toBeInTheDocument();
  expect((await screen.findAllByText("Figure needed")).length).toBeGreaterThan(0);
});

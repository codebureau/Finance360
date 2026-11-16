import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { pieArcs, shortPercent } from "@/lib/tax";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// Invented figures. The first Member has a positive gap, the second a negative one.
const slice = (key: string, name: string, amount: string, share_percent: string | null) => ({
  key,
  name,
  amount,
  share_percent,
});
const alex = {
  member_id: 1,
  name: "Alex Sample",
  gross_pay: "100000.00",
  employer_super: "12000.00",
  package: "112000.00",
  slices: [
    slice("tax", "Tax withheld (includes Medicare levy)", "24000.00", "21.4286"),
    slice("super", "Super", "12000.00", "10.7143"),
    slice("lease", "Novated lease costs", "5000.00", "4.4643"),
    slice("other", "Other deductions", "1000.00", "0.8929"),
    slice("take_home", "Take-home", "69000.00", "61.6071"),
    slice("unreconciled", "Unreconciled", "1000.00", "0.8928"),
  ],
  unreconciled: "1000.00",
  reportable_fringe_benefits: "8000.00",
  super_insurance_premiums: "600.00",
  take_home_worked_out: "0.00",
};
const jordan = {
  member_id: 2,
  name: "Jordan Sample",
  gross_pay: "50000.00",
  employer_super: "6000.00",
  package: "56000.00",
  slices: [
    slice("tax", "Tax withheld (includes Medicare levy)", "9000.00", "16.0714"),
    slice("super", "Super", "6000.00", "10.7143"),
    slice("lease", "Novated lease costs", "0.00", "0"),
    slice("other", "Other deductions", "0.00", "0"),
    slice("take_home", "Take-home", "41500.00", "73.2143"),
  ],
  unreconciled: "-500.00",
  reportable_fringe_benefits: "0.00",
  super_insurance_premiums: "0.00",
  take_home_worked_out: "0.00",
};

function fakeBackend(members: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/tax/income-reconciliation") {
        return json({
          financial_year: 2097,
          financial_year_label: "2096-97",
          members,
          household: members.length ? { ...alex, member_id: null, name: "Household" } : null,
        });
      }
      if (path === "/api/tax/setup") return json({ rule_pack_version: "x", years: [], sections: [], labels: [] });
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

test("each Member's table shows the slices and Unreconciled, with the percentages and the pie", async () => {
  fakeBackend([alex, jordan]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  const table = within(card).getByRole("table");
  expect(within(table).getByRole("row", { name: /Take-home/ })).toHaveTextContent("61.6%");
  expect(within(table).getByTestId("unreconciled")).toHaveTextContent("1,000.00");
  expect(within(card).getByRole("img", { name: /Where the total package goes: Tax withheld/ })).toBeInTheDocument();
  expect(within(card).getByText("Total package (100%)")).toBeInTheDocument();
});

test("take-home worked out from the statements that state no net pay says so, whole or in part", async () => {
  // Alex states none: all of take-home is worked out, and nothing is Unreconciled. Jordan states some.
  const worked = { ...alex, slices: alex.slices.filter((s) => s.key !== "unreconciled"), unreconciled: "0.00", take_home_worked_out: "69000.00" };
  const mixed = { ...jordan, take_home_worked_out: "30000.00" };
  fakeBackend([worked, mixed]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const alexCard = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(alexCard).getByTestId("take-home-worked-out")).toHaveTextContent(/^Worked out, not stated$/);
  expect(within(alexCard).getByTestId("unreconciled")).toHaveTextContent("0.00");
  const jordanCard = screen.getByLabelText("Income reconciliation for Jordan Sample");
  expect(within(jordanCard).getByTestId("take-home-worked-out")).toHaveTextContent("Includes 30,000.00 worked out, not stated");
});

test("a Member whose statements all state net pay shows no worked-out note", async () => {
  fakeBackend([alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(card).queryByTestId("take-home-worked-out")).not.toBeInTheDocument();
});

test("reportable fringe benefits and super insurance premiums are beside and under the chart, not slices", async () => {
  fakeBackend([alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(card).getByText("Reportable fringe benefits").nextElementSibling).toHaveTextContent("8,000.00");
  expect(within(within(card).getByRole("table")).queryByText(/fringe/i)).not.toBeInTheDocument();
  expect(within(card).getByTestId("insurance-note")).toHaveTextContent("600.00");
  expect(within(within(card).getByRole("table")).queryByText(/insurance/i)).not.toBeInTheDocument();
});

test("a negative Unreconciled is shown, signed, as a note: not a slice, and the chart still draws", async () => {
  fakeBackend([jordan, alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Jordan Sample");
  const row = within(card).getByTestId("unreconciled");
  expect(row).toHaveTextContent("-500.00");
  expect(row).toHaveTextContent("Not drawn");
  expect(within(card).getByTestId("negative-note")).toBeInTheDocument();
  const chart = within(card).getByRole("img");
  expect(chart.querySelectorAll("path")).toHaveLength(3); // tax, super, take-home; no empty or negative arc
  expect(chart.getAttribute("aria-label")).not.toMatch(/Unreconciled/);
});

test("the Household is shown after the Members when there are several", async () => {
  fakeBackend([alex, jordan]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  expect(await screen.findByLabelText("Income reconciliation for Household")).toBeInTheDocument();
});

test("a year with no income statement says so", async () => {
  fakeBackend([]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  expect(await screen.findByTestId("reconciliation-empty")).toHaveTextContent("FY 2096-97");
});

test("pieArcs draws the shares in proportion, skips an empty slice and handles one whole slice", () => {
  expect(pieArcs([])).toEqual([]);
  expect(pieArcs([{ key: "a", percent: "0" }])).toEqual([]);
  const whole = pieArcs([
    { key: "a", percent: "100" },
    { key: "b", percent: "0" },
  ]);
  expect(whole.map((a) => a.key)).toEqual(["a"]);
  expect(whole[0].path).toContain("A 50 50");
  const halves = pieArcs([
    { key: "a", percent: "50" },
    { key: "b", percent: "50" },
  ]);
  expect(halves.map((a) => a.key)).toEqual(["a", "b"]);
  expect(shortPercent("21.4286")).toBe("21.4%");
  expect(shortPercent(null)).toBe("");
});

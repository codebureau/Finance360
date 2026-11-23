import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { shortPercent } from "@/lib/tax";
import { renderAppAt } from "@/test-utils";

// jsdom has no canvas: stand in for the ECharts host and keep the option it was given.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const options: { label: string; current: any }[] = [];
vi.mock("@/components/echart", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  default: ({ option, label }: { option: any; label: string }) => {
    options.push({ label, current: option });
    return <div data-testid="mock-echart" role="img" aria-label={label} />;
  },
}));

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

const openFigures = (card: HTMLElement) => fireEvent.click(within(card).getByRole("button", { name: /Show figures/ }));

test("each Member's card leads with a headline and a stacked bar; the table is behind Show figures", async () => {
  fakeBackend([alex, jordan]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(card).getByText(/Take-home is 61\.6% of the package; tax withheld is the next largest\./)).toBeInTheDocument();
  expect(await within(card).findByTestId("mock-echart")).toHaveAccessibleName(/Stacked bar chart/);
  expect(within(card).queryByRole("table")).not.toBeInTheDocument();
  fireEvent.click(within(card).getByRole("button", { name: /Show figures/ }));
  const table = within(card).getByRole("table");
  expect(within(table).getByRole("row", { name: /Take-home/ })).toHaveTextContent("61.6%");
  expect(within(table).getByTestId("unreconciled")).toHaveTextContent("1,000.00");
  expect(within(card).getByText("Total package (100%)")).toBeInTheDocument();
});

test("take-home worked out from the statements that state no net pay says so, whole or in part", async () => {
  // Alex states none: all of take-home is worked out, and nothing is Unreconciled. Jordan states some.
  const worked = { ...alex, slices: alex.slices.filter((s) => s.key !== "unreconciled"), unreconciled: "0.00", take_home_worked_out: "69000.00" };
  const mixed = { ...jordan, take_home_worked_out: "30000.00" };
  fakeBackend([worked, mixed]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const alexCard = await screen.findByLabelText("Income reconciliation for Alex Sample");
  openFigures(alexCard);
  expect(within(alexCard).getByTestId("take-home-worked-out")).toHaveTextContent(/^Worked out, not stated$/);
  expect(within(alexCard).getByTestId("unreconciled")).toHaveTextContent("0.00");
  const jordanCard = screen.getByLabelText("Income reconciliation for Jordan Sample");
  openFigures(jordanCard);
  expect(within(jordanCard).getByTestId("take-home-worked-out")).toHaveTextContent("Includes 30,000.00 worked out, not stated");
});

test("a Member whose statements all state net pay shows no worked-out note", async () => {
  fakeBackend([alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  openFigures(card);
  expect(within(card).queryByTestId("take-home-worked-out")).not.toBeInTheDocument();
});

test("reportable fringe benefits and super insurance premiums are beside and under the chart, not slices", async () => {
  fakeBackend([alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(card).getByText("Reportable fringe benefits").parentElement).toHaveTextContent("8,000.00");
  openFigures(card);
  expect(within(within(card).getByRole("table")).queryByText(/fringe/i)).not.toBeInTheDocument();
  expect(within(card).getByTestId("insurance-note")).toHaveTextContent("600.00");
  expect(within(within(card).getByRole("table")).queryByText(/insurance/i)).not.toBeInTheDocument();
});

test("a negative Unreconciled is shown, signed, as a note: not a slice, and the chart still draws", async () => {
  fakeBackend([jordan, alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Jordan Sample");
  expect(within(card).getByTestId("negative-note")).toHaveTextContent("-500.00");
  const bar = options.filter((o) => /Jordan Sample/.test(o.label)).at(-1)!.current;
  // tax, super, take-home: no segment for the negative gap
  expect(bar.series.map((x: { name: string }) => x.name)).toEqual(["Tax withheld", "Super", "Take-home"]);
  openFigures(card);
  const row = within(card).getByTestId("unreconciled");
  expect(row).toHaveTextContent("-500.00");
  expect(row).toHaveTextContent("Not drawn");
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

test("a positive Unreconciled is drawn as its own segment, last, and a part with nothing is not", async () => {
  fakeBackend([alex]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  await screen.findByLabelText("Income reconciliation for Alex Sample");
  const bar = options.filter((o) => /Alex Sample/.test(o.label)).at(-1)!.current;
  expect(bar.series.map((x: { name: string }) => x.name)).toEqual([
    "Tax withheld",
    "Super",
    "Novated lease costs",
    "Other deductions",
    "Take-home",
    "Unreconciled",
  ]);
});

test("a Member whose figures are all nil shows a note, not an empty chart", async () => {
  const empty = { ...alex, gross_pay: "0.00", employer_super: "0.00", package: "0.00", unreconciled: "0.00", slices: alex.slices.map((s) => ({ ...s, amount: "0.00" })), reportable_fringe_benefits: "0.00", super_insurance_premiums: "0.00" };
  fakeBackend([empty]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  const card = await screen.findByLabelText("Income reconciliation for Alex Sample");
  expect(within(card).getByText("No figures on the income statements.")).toBeInTheDocument();
  expect(within(card).queryByTestId("mock-echart")).not.toBeInTheDocument();
});

test("shortPercent shows one decimal place", () => {
  expect(shortPercent("21.4286")).toBe("21.4%");
  expect(shortPercent(null)).toBe("");
});

test("every card starts on the stacked bar, each with its own chart-type switch", async () => {
  fakeBackend([alex, jordan]);
  await renderAppAt("/tax/reconciliation?fy=2097");
  await screen.findByLabelText("Income reconciliation for Household");
  const charts = await screen.findAllByTestId("share-chart");
  expect(charts).toHaveLength(3);
  charts.forEach((c) => expect(c).toHaveAttribute("data-form", "stacked-bar"));
  // One radio group per card: all three show "Stacked bar" selected.
  const selected = screen.getAllByRole("radio", { name: "Stacked bar" });
  expect(selected).toHaveLength(3);
  selected.forEach((r) => expect(r).toBeChecked());
});

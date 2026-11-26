import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { estimateBody, financialYearLabel, parseTaxSearch } from "@/lib/tax";
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

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [
    { id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" },
    { id: 2, name: "Jordan Sample", date_of_birth: "1986-09-30" },
  ],
};

const label = (key: string, name: string, kind = "money", section = "employment", modelled = true, item = "") => ({
  key,
  return_item: item,
  name,
  description: "",
  direction: "income",
  kind,
  modelled,
  section,
});
const labels = [
  label("salary", "Salary and wages (gross)", "money", "employment", true, "1"),
  label("tax_withheld", "Tax withheld from employment"),
  label("help_debt", "Has a HELP or study loan debt", "flag", "medicare"),
  label("surcharge_exempt_days", "Days exempt from the Medicare levy surcharge", "days", "medicare"),
  label("government_allowances", "Australian Government allowances and payments", "money", "other_items", false, "5"),
];
const sections = [
  { key: "employment", name: "Employment" },
  { key: "medicare", name: "Medicare, health cover and HELP" },
  { key: "other_items", name: "Other return items (not modelled)" },
];

// Invented figures, in a fictional pack and year.
const setupWith = (years: unknown[]) => ({ rule_pack_version: "au/2026-27/r3", years, sections, labels });
const heldYear = {
  financial_year: 2097,
  label: "2096-97",
  complete: true,
  missing: [],
  provisional: [],
};

// Invented, and each line adds up: tax + levy - offsets = total; credits - total = net.
const line = (p: {
  taxable: string;
  tax: string;
  offsets: string;
  levy: string;
  total: string;
  withheld: string;
  franking: string;
  credits: string;
  net: string;
}) => ({
  taxable_income: p.taxable,
  tax_on_taxable_income: p.tax,
  offsets: p.offsets,
  medicare_levy: p.levy,
  medicare_levy_surcharge: "0.00",
  help_repayment: "0.00",
  total_tax: p.total,
  tax_withheld: p.withheld,
  franking_credits: p.franking,
  payg_instalments: "0.00",
  credits: p.credits,
  net_outcome: p.net,
});
const alexLine = line({ taxable: "80000.00", tax: "10610.00", offsets: "0.00", levy: "2400.00", total: "13010.00", withheld: "14000.00", franking: "0.00", credits: "14000.00", net: "990.00" });
const jordanLine = line({ taxable: "28000.00", tax: "1000.00", offsets: "450.00", levy: "840.00", total: "1390.00", withheld: "800.00", franking: "200.00", credits: "1000.00", net: "-390.00" });
const householdLine = line({ taxable: "108000.00", tax: "11610.00", offsets: "450.00", levy: "3240.00", total: "14400.00", withheld: "14800.00", franking: "200.00", credits: "15000.00", net: "600.00" });
const estimate = (more: Record<string, unknown> = {}) => ({
  financial_year: 2097,
  financial_year_label: "2096-97",
  engine_version: 1,
  rule_pack_version: "au/2096-97/r2",
  incomplete: false,
  provisional: [],
  members: [
    { member_id: 1, name: "Alex Sample", total_income: "80000.00", total_deductions: "0.00", low_income_offset: "0.00", small_business_offset: "0.00", surcharge_rate: "0", notes: ["Capital loss of 10.00 carried forward."], ...alexLine },
    { member_id: 2, name: "Jordan Sample", total_income: "28000.00", total_deductions: "0.00", low_income_offset: "450.00", small_business_offset: "0.00", surcharge_rate: "0", notes: [], ...jordanLine },
  ],
  household: { ...householdLine, surcharge_income: "108000.00", surcharge_thresholds: "family", surcharge_tier: 0 },
  unmodelled: [],
  division_293: { members: [], figures_used: [], waiting_for: [] },
  ...more,
});

const figure = (key: string, name: string, value: string, provisional_note: string | null = null) => ({
  key,
  name,
  financial_year: 2097,
  financial_year_label: "2096-97",
  value,
  source: "ATO, Division 293 tax on concessional contributions",
  url: "https://www.ato.gov.au/example",
  url_confirmed: false,
  retrieved: "2096-11-05",
  provisional: provisional_note !== null,
  provisional_note,
  rule_pack_version: "au/2096-97/r7",
  origin: "shipped",
  capture_id: null,
  quote: null,
  captured_by: null,
  captured_at: null,
  shipped_value: null,
  method: null,
});
const operand = (value: string, label: string, operator: string | null = null, unit: string | null = "AUD") => ({
  value,
  unit,
  label,
  operator,
});
const div293Member = (more: Record<string, unknown> = {}) => ({
  member_id: 1,
  name: "Alex Sample",
  liability: "1200.00",
  applies: true,
  div293_income: "258000.00",
  inputs: [
    { label: "Taxable income", amount: "234600.00", source: "This estimate, from the Tax records for FY 2096-97" },
    { label: "Concessional contributions received: Sample Super", amount: "14400.00", source: "Super statement for Sample Super, FY 2096-97" },
    { label: "Division 293 threshold", amount: "250000.00", source: "Rule pack tax.div293.threshold, FY 2096-97" },
  ],
  steps: [
    {
      title: "Amount taxed",
      operands: [operand("14400.00", "low tax contributions"), operand("8000.00", "income over the threshold", "or")],
      result: operand("8000.00", "the lesser of the two"),
      note: "Division 293 tax applies to the lesser of the two amounts.",
    },
  ],
  contributions_source: "statement",
  received: "14400.00",
  cap: "30000.00",
  cap_status: "ok",
  cap_warning: null,
  released: null,
  released_source: null,
  notes: [],
  ...more,
});
const withDiv293 = (member: Record<string, unknown> = {}, figures = [figure("tax.div293.rate", "Division 293 tax rate", "15%")]) =>
  estimate({ division_293: { members: [div293Member(member)], figures_used: figures, waiting_for: [] } });

const total = (member_id: number, member_name: string, label_key: string, label_name: string, amount: string, ids: number[]) => ({
  member_id,
  member_name,
  label_key,
  label_name,
  return_item: label_key === "salary" ? "1" : "",
  kind: "money",
  amount,
  days: null,
  flag: null,
  contributions: ids.map((record_id) => ({ record_id, amount: null, days: null, flag: null })),
});
const registerTotals = [
  total(1, "Alex Sample", "salary", "Salary and wages (gross)", "80000.00", [11]),
  total(2, "Jordan Sample", "salary", "Salary and wages (gross)", "28000.00", [12, 13]),
];

type Call = { method: string; path: string; search?: string; body?: Record<string, unknown> };

function fakeBackend(opts: { years?: unknown[]; result?: () => Response } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = request.method === "POST" ? await request.json() : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/tax/label-totals") {
        return json({ financial_year: 2097, financial_year_label: "2096-97", totals: registerTotals, uncounted: [] });
      }
      if (path === "/api/tax/setup") return json(setupWith(opts.years ?? [heldYear]));
      if (path === "/api/tax/estimate") return opts.result ? opts.result() : json(estimate());
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the screen shows each Member's totals from the Tax records, with no box to type a total in", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax?fy=2097");
  expect(await screen.findByRole("heading", { name: "Tax estimate" })).toBeInTheDocument();
  const totals = await screen.findByRole("table", { name: "Label totals" });
  const alex = within(totals).getByRole("row", { name: /Alex Sample/ });
  expect(alex).toHaveTextContent("Salary and wages (gross)");
  expect(alex).toHaveTextContent("item 1");
  expect(alex).toHaveTextContent("80,000.00");
  expect(within(totals).getByRole("row", { name: /Jordan Sample/ })).toHaveTextContent("2");
  expect(screen.queryByRole("textbox", { name: /Salary and wages/ })).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Financial year/)).toHaveValue("2097");
  expect(calls.find((c) => c.path === "/api/tax/label-totals")?.search).toBe("?financial_year=2097");
});

test("a total drills to the records behind it, in the Tax records register", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/tax?fy=2097");
  const totals = await screen.findByRole("table", { name: "Label totals" });
  fireEvent.click(within(totals).getAllByRole("link", { name: "Salary and wages (gross)" })[1]);
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/records"));
  expect(router.state.location.search).toEqual({ fy: 2097, member: 2, label: "salary" });
});

test("the primary action is the Calculate button in the page header", async () => {
  fakeBackend();
  await renderAppAt("/tax?fy=2097");
  const heading = await screen.findByRole("heading", { name: "Tax estimate" });
  const button = screen.getByRole("button", { name: "Calculate" });
  expect(heading.parentElement?.parentElement).toContainElement(button);
});

test("with no captured year the screen says so and how to fix it, naming the year", async () => {
  fakeBackend({ years: [] });
  await renderAppAt("/tax?fy=2026");
  const notice = await screen.findByTestId("no-figures");
  expect(notice).toHaveTextContent("No tax figures have been captured");
  expect(notice).toHaveTextContent("FY 2025-26");
  expect(notice).toHaveTextContent("Capture them on Rule figures (FY 2025-26).");
  expect(within(notice).getByRole("link", { name: "Rule figures (FY 2025-26)" })).toHaveAttribute(
    "href",
    "/rule-figures?fy=2026",
  );
  expect(notice).not.toHaveTextContent("rulepack_update");
});

test("a year the pack lacks says so beside the year, even when another year is held", async () => {
  fakeBackend();
  await renderAppAt("/tax?fy=2098");
  const notice = await screen.findByTestId("year-not-held");
  expect(notice).toHaveTextContent("FY 2097-98");
  expect(notice).toHaveTextContent("never uses another year");
});

test("Calculate sends the Members, not totals, and shows each Member and the Household", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));

  const result = await screen.findByTestId("tax-result");
  const post = calls.find((c) => c.path === "/api/tax/estimate");
  expect(post?.body).toEqual({ financial_year: 2097, dependants: 0, member_ids: [1, 2] });
  fireEvent.click(within(result).getByRole("button", { name: "Show figures: Estimate" }));
  const table = within(result).getByRole("table", { name: "Estimate" });
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
    "Item",
    "Alex",
    "Jordan",
    "Household",
  ]);
  const net = within(table).getByRole("row", { name: /Net outcome/ });
  expect(net).toHaveTextContent("990.00");
  expect(net).toHaveTextContent("-390.00");
  expect(net).toHaveTextContent("600.00");
  expect(within(table).getByRole("row", { name: /Medicare levy surcharge/ })).toBeInTheDocument();
  expect(result).toHaveTextContent("Alex: Capital loss of 10.00 carried forward.");
  expect(result).toHaveTextContent("Rule pack au/2096-97/r2, engine v1. Not saved until you press Save estimate.");
  expect(screen.queryByTestId("incomplete")).not.toBeInTheDocument();
});

test("a figure for a return item the engine does not model is listed and marks the estimate incomplete", async () => {
  fakeBackend({
    result: () =>
      json(
        estimate({
          incomplete: true,
          unmodelled: [
            {
              member_id: 2,
              name: "Jordan Sample",
              label_key: "government_allowances",
              return_item: "5",
              label_name: "Australian Government allowances and payments",
              amount: "500.00",
            },
          ],
        }),
      ),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const notice = await screen.findByTestId("incomplete");
  expect(notice).toHaveTextContent("Jordan Sample: item 5, Australian Government allowances and payments");
  expect(notice).toHaveTextContent("500.00");
  expect(within(screen.getByTestId("tax-result")).getByText("Incomplete")).toBeInTheDocument();
});

test("a provisional figure is shown as a warning on the estimate", async () => {
  fakeBackend({
    result: () => json(estimate({ provisional: [{ key: "tax.help.t1", note: "announced, not yet law" }] })),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const warning = await screen.findByTestId("provisional");
  expect(warning).toHaveTextContent("tax.help.t1");
  expect(warning).toHaveTextContent("announced, not yet law");
});

test("a year with no figures fails with the server's message naming the year and the fix", async () => {
  fakeBackend({
    years: [],
    result: () =>
      json(
        {
          errors: [
            {
              field: "financial_year",
              message:
                "The Rule pack (au/2026-27/r3) holds no tax figures for FY 2025-26, and an estimate never uses another year's. Capture them on Rule figures (FY 2025-26).",
            },
          ],
        },
        422,
      ),
  });
  await renderAppAt("/tax?fy=2026");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const error = await screen.findByTestId("tax-error");
  expect(error).toHaveTextContent("holds no tax figures for FY 2025-26");
  expect(error).toHaveTextContent("Capture them on Rule figures (FY 2025-26).");
  expect(within(error).getByRole("link", { name: "Open Rule figures for FY 2025-26" })).toHaveAttribute(
    "href",
    "/rule-figures?fy=2026",
  );
  expect(screen.queryByTestId("tax-result")).not.toBeInTheDocument();
});

test("calculating puts the year in the address, so the screen is linkable", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/tax");
  fireEvent.change(await screen.findByLabelText(/Financial year/), { target: { value: "2097" } });
  fireEvent.click(screen.getByRole("button", { name: "Calculate" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ fy: 2097 }));
});

test("the request body carries the year, the children and the Members only", () => {
  expect(estimateBody({ financialYear: 2097, dependants: " 2 ", memberIds: [1, 2] })).toEqual({
    financial_year: 2097,
    dependants: 2,
    member_ids: [1, 2],
  });
  expect(estimateBody({ financialYear: 1, dependants: "x", memberIds: [] }).dependants).toBe(-1);
});

test("small helpers", () => {
  expect(financialYearLabel(2027)).toBe("2026-27");
  expect(financialYearLabel(2100)).toBe("2099-00");
  expect(parseTaxSearch({ fy: "2027" })).toEqual({ fy: 2027 });
  expect(parseTaxSearch({ fy: "abc" })).toEqual({});
  expect(parseTaxSearch({ fy: "12" })).toEqual({});
});

test("Division 293 shows each input with its source, the steps including the lesser-of, and the figures used", async () => {
  fakeBackend({ result: () => json(withDiv293()) });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const card = await screen.findByTestId("div293");
  expect(within(card).getByRole("heading", { name: "Division 293 for FY 2096-97" })).toBeInTheDocument();
  expect(within(card).getByText("Super statement for Sample Super, FY 2096-97")).toBeInTheDocument();
  expect(within(card).getByText("Rule pack tax.div293.threshold, FY 2096-97")).toBeInTheDocument();
  expect(within(card).getByText("Amount taxed")).toBeInTheDocument();
  expect(within(card).getByText("the lesser of the two")).toBeInTheDocument();
  expect(within(card).getByText("Division 293 tax rate")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "How it works" })).toHaveAttribute("href", "/guide/division-293");
  expect(within(card).queryByTestId("div293-actual")).toBeNull();
  expect(within(card).queryByText("Provisional")).toBeNull();
});

test("a provisional Division 293 figure marks the working provisional", async () => {
  fakeBackend({
    result: () => json(withDiv293({}, [figure("tax.div293.rate", "Division 293 tax rate", "15%", "Confirm on Rule figures")])),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const card = await screen.findByTestId("div293");
  expect(within(card).getAllByText("Provisional").length).toBeGreaterThan(0);
  expect(within(card).getByText("Confirm on Rule figures")).toBeInTheDocument();
});

test("the amount released from super is shown beside the prediction, and the cap warning when near or over", async () => {
  fakeBackend({
    result: () =>
      json(
        withDiv293({
          released: "2160.00",
          released_source: "Super statement: Sample Super",
          cap_status: "over",
          cap_warning: "Concessional contributions received (35000.00) are over the cap (30000.00) by 5000.00.",
        }),
      ),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const actual = await screen.findByTestId("div293-actual");
  expect(actual).toHaveTextContent("2,160.00");
  expect(actual).toHaveTextContent("Predicted");
  const cap = screen.getByTestId("div293-cap");
  expect(cap).toHaveTextContent("Over the cap");
  expect(cap).toHaveTextContent("over the cap (30000.00) by 5000.00");
});

test("Division 293 names Income statements as the source of contributions, with the timing note", async () => {
  fakeBackend({
    result: () =>
      json(
        withDiv293({
          contributions_source: "income_statement",
          notes: ["These are the employer's figures by period worked, not by date received."],
        }),
      ),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  expect(await screen.findByTestId("div293-cap")).toHaveTextContent("(Income statements)");
  expect(await screen.findByText(/by period worked, not by date received/)).toBeInTheDocument();
});

test("Division 293 says what it is waiting for when the Rule pack lacks a figure", async () => {
  fakeBackend({
    result: () =>
      json(
        estimate({
          division_293: {
            members: [],
            figures_used: [],
            waiting_for: [
              { key: "tax.div293.threshold", name: "Division 293 threshold", financial_year: 2097, financial_year_label: "2096-97" },
            ],
          },
        }),
      ),
  });
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const waiting = await screen.findByTestId("div293-waiting");
  expect(waiting).toHaveTextContent("Division 293 threshold");
  expect(within(waiting).getByRole("link", { name: /Capture on Rule figures/ })).toBeInTheDocument();
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const lastOption = (): any => options.filter((o) => /Waterfall chart/.test(o.label)).at(-1)!.current;

test("the waterfall starts at the Household and a Member can be chosen; each draws its own steps", async () => {
  options.length = 0;
  fakeBackend();
  await renderAppAt("/tax?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const card = await screen.findByRole("region", { name: "How the tax becomes the result" });
  // Household: the engine's lines, with the credits summed from the Members.
  expect(within(card).getByText(/The Household gets a refund of 600\.00/)).toBeInTheDocument();
  expect(within(card).getByRole("combobox", { name: "For" })).toHaveValue("household");
  expect(lastOption().xAxis.data).toEqual([
    "Tax on taxable income",
    "Offsets",
    "Medicare levy",
    "Total tax",
    "Tax withheld",
    "Franking credits",
    "Refund",
  ]);
  fireEvent.change(within(card).getByRole("combobox", { name: "For" }), { target: { value: "2" } });
  expect(within(card).getByText(/Jordan pays 390\.00 more than the credits cover/)).toBeInTheDocument();
});

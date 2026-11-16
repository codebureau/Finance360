import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { parseCompareSearch, parseYearSearch } from "@/lib/tax-estimates";
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

const label = (key: string, name: string, kind = "money", record_types: string[] = ["line"], item = "") => ({
  key,
  return_item: item,
  name,
  description: "",
  direction: "income",
  kind,
  modelled: true,
  section: "employment",
  record_types,
});
const setup = {
  rule_pack_version: "au/2026-27/r3",
  years: [{ financial_year: 2026, label: "2025-26", complete: true, missing: [], provisional: [] }],
  sections: [{ key: "employment", name: "Employment" }],
  labels: [
    label("salary", "Salary and wages (gross)", "money", ["line", "income_statement"], "1"),
    label("work_other", "Other work-related expenses", "money", ["line", "depreciating_asset"], "D5"),
    label("help_debt", "Has a HELP or study loan debt", "flag", ["fact"]),
  ],
};

// Invented figures.
const line = (taxable: string, total: string, net: string) => ({
  taxable_income: taxable,
  tax_on_taxable_income: "10610.00",
  offsets: "0.00",
  medicare_levy: "2400.00",
  medicare_levy_surcharge: "0.00",
  help_repayment: "0.00",
  total_tax: total,
  credits: "14000.00",
  net_outcome: net,
});
const member = (id: number, name: string, taxable: string, total: string, net: string) => ({
  member_id: id,
  name,
  total_income: taxable,
  total_deductions: "0.00",
  low_income_offset: "0.00",
  small_business_offset: "0.00",
  surcharge_rate: "0",
  notes: [],
  ...line(taxable, total, net),
});
const money = (amount: string) => ({ amount, days: null, flag: null });

const view = (more: Record<string, unknown> = {}) => ({
  financial_year: 2026,
  financial_year_label: "2025-26",
  engine_version: 2,
  rule_pack_version: "au/2026-27/r3",
  dependants: 0,
  incomplete: false,
  provisional: [],
  whatif_id: null,
  whatif_name: null,
  members: [
    member(1, "Alex Sample", "80000.00", "13010.00", "990.00"),
    member(2, "Jordan Sample", "28000.00", "1390.00", "-390.00"),
  ],
  household: { ...line("108000.00", "14400.00", "600.00"), surcharge_income: "108000.00", surcharge_thresholds: "family", surcharge_tier: 0 },
  unmodelled: [],
  totals: [
    {
      member_id: 1,
      member_name: "Alex Sample",
      label_key: "salary",
      label_name: "Salary and wages (gross)",
      return_item: "1",
      kind: "money",
      value: money("80000.00"),
      overridden: false,
      source: "records",
      contributions: [{ record_id: 4, description: "Income statement, Sample Employer", value: money("80000.00") }],
    },
  ],
  overrides: [],
  prefill: [],
  rerun_of: null,
  ...more,
});

const header = (over: Record<string, unknown> = {}) => ({
  id: 3,
  created_at: "2026-11-14T01:30:00Z",
  label: "Base",
  financial_year: 2026,
  financial_year_label: "2025-26",
  whatif_id: null,
  whatif_name: null,
  engine_version: 2,
  current_engine: true,
  rule_pack_version: "au/2026-27/r3",
  incomplete: false,
  has_provisional: false,
  ...over,
});

const whatif = (over: Record<string, unknown> = {}) => ({
  id: 5,
  name: "Jordan earns more",
  financial_year: 2026,
  financial_year_label: "2025-26",
  notes: "Invented.",
  origin: "user",
  created_at: "2026-11-14T01:00:00Z",
  overrides: [
    {
      op: "set",
      member_id: 2,
      label_key: "salary",
      label_name: "Salary and wages (gross)",
      value: money("95000.00"),
      record_id: null,
      record: null,
    },
  ],
  estimate_count: 0,
  ...over,
});

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

function fakeBackend(
  options: {
    whatifs?: ReturnType<typeof whatif>[];
    estimates?: ReturnType<typeof header>[];
    saved?: Record<string, unknown>;
    putError?: unknown;
  } = {},
) {
  const calls: Call[] = [];
  const whatifs = options.whatifs ?? [whatif()];
  const estimates = options.estimates ?? [header()];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json().catch(() => undefined) : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/household") return json(household);
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/tax/records") {
        return json([{ id: 9, description: "Donation to Sample Charity", member_name: "Alex Sample" }]);
      }
      if (path === "/api/tax/whatifs" && request.method === "POST") {
        return json(whatif({ id: 6, name: body?.name, overrides: [] }), 201);
      }
      if (path === "/api/tax/whatifs") return json(whatifs);
      const one = path.match(/^\/api\/tax\/whatifs\/(\d+)$/);
      if (one && request.method === "PUT") {
        if (options.putError) return json(options.putError, 422);
        return json(whatif({ overrides: [...whatifs[0].overrides, ...(body?.overrides as unknown[]).slice(-1)] }));
      }
      if (one && request.method === "DELETE") return new Response(null, { status: 204 });
      if (one) return json(whatifs.find((w) => w.id === Number(one[1])) ?? whatif());
      if (path === "/api/tax/estimate") return json({ ...view(), division_293: { members: [], figures_used: [], waiting_for: [] } });
      if (path === "/api/tax/label-totals") {
        return json({ financial_year: 2026, financial_year_label: "2025-26", totals: [], uncounted: [] });
      }
      if (path === "/api/tax/estimates/preview") {
        return json(
          view({
            whatif_id: body?.whatif_id ?? null,
            whatif_name: body?.whatif_id ? "Jordan earns more" : null,
            overrides: body?.whatif_id
              ? [
                  {
                    op: "set",
                    summary: "Jordan Sample: Salary and wages (gross) set to a stated total.",
                    member_id: 2,
                    label_key: "salary",
                    label_name: "Salary and wages (gross)",
                    value: money("95000.00"),
                    replaced: money("85000.00"),
                    record_id: null,
                    description: null,
                    contributions: [],
                  },
                ]
              : [],
          }),
        );
      }
      if (path === "/api/tax/estimates" && request.method === "POST") {
        return json(header({ id: 11, label: (body?.label as string | null) ?? null, whatif_id: body?.whatif_id ?? null }), 201);
      }
      if (path === "/api/tax/estimates") return json(estimates);
      if (path === "/api/tax/estimates/compare") {
        return json({
          financial_year: 2026,
          financial_year_label: "2025-26",
          a: header({ id: 3, label: "Base" }),
          b: header({ id: 4, label: "Higher", whatif_id: 5, whatif_name: "Jordan earns more" }),
          members: [
            { member_id: 1, name: "Alex Sample" },
            { member_id: 2, name: "Jordan Sample" },
          ],
          labels: [
            { member_id: 2, member_name: "Jordan Sample", label_key: "salary", label_name: "Salary and wages (gross)", return_item: "1", kind: "money", a: money("85000.00"), b: money("95000.00"), delta: money("10000.00") },
          ],
          results: ["taxable_income", "total_tax", "net_outcome"].flatMap((key, i) =>
            [null, 1, 2].map((member_id) => ({
              key,
              name: key === "net_outcome" ? "Net outcome" : key === "total_tax" ? "Total tax" : "Taxable income",
              member_id,
              a: "1000.00",
              b: member_id === 2 ? (i === 2 ? "-1500.00" : "2000.00") : "1000.00",
              delta: member_id === 2 ? (i === 2 ? "-2500.00" : "1000.00") : "0.00",
            })),
          ),
          dependants_a: 0,
          dependants_b: 0,
          same_engine: true,
          rule_pack_differs: false,
        });
      }
      const rerun = path.match(/^\/api\/tax\/estimates\/(\d+)\/rerun$/);
      if (rerun) return json(header({ id: 12, label: "Re-run of estimate 3" }), 201);
      const saved = path.match(/^\/api\/tax\/estimates\/(\d+)$/);
      if (saved) {
        return json(
          options.saved ?? {
            estimate: estimates.find((e) => e.id === Number(saved[1])) ?? header({ id: Number(saved[1]) }),
            view: view(),
            reproduces: true,
          },
        );
      }
      if (path === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the search params for the year and for the two compared estimates are read strictly", () => {
  expect(parseYearSearch({ fy: "2026" })).toEqual({ fy: 2026 });
  expect(parseYearSearch({ fy: "20" })).toEqual({});
  expect(parseCompareSearch({ a: "3", b: "4" })).toEqual({ a: 3, b: 4 });
  expect(parseCompareSearch({ a: "x", b: "-1" })).toEqual({});
});

test("the Tax what-ifs list names each what-if, its year and how many overrides and estimates it has", async () => {
  fakeBackend({ whatifs: [whatif({ estimate_count: 2 })] });
  await renderAppAt("/tax/whatifs");
  const row = (await screen.findByRole("link", { name: "Jordan earns more" })).closest("tr") as HTMLElement;
  expect(within(row).getByText("FY 2025-26")).toBeInTheDocument();
  expect(within(row).getAllByRole("cell")[2]).toHaveTextContent("1");
  expect(within(row).getAllByRole("cell")[3]).toHaveTextContent("2");
  expect(within(row).getByRole("link")).toHaveAttribute("href", "/tax/whatifs/5");
});

test("a new Tax what-if is created in a dialog, then opened to add its overrides", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax/whatifs");
  fireEvent.click(await screen.findByRole("button", { name: "New Tax what-if" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Try a bonus" } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create Tax what-if" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/whatifs/6"));
  const made = calls.find((c) => c.method === "POST" && c.path === "/api/tax/whatifs");
  expect(made?.body).toMatchObject({ name: "Try a bonus", overrides: [] });
});

test("the editor lists the overrides as absolute, and adds a stated total as the whole list", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/whatifs/5");
  const list = await screen.findByRole("list", { name: "Overrides" });
  expect(within(list).getByText(/Jordan: Salary and wages \(gross\) is stated as a total/)).toBeInTheDocument();
  expect(screen.getByText(/Each override is absolute/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Add override" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByLabelText("Return label"), { target: { value: "salary" } });
  fireEvent.change(within(dialog).getByLabelText(/The total, in dollars/), { target: { value: "70000.00" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add override" }));

  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT");
  const sent = put?.body?.overrides as Record<string, unknown>[];
  expect(sent).toHaveLength(2); // the whole list: the existing one, then the new one
  expect(sent[1]).toMatchObject({ op: "set", member_id: 1, label: "salary", amount: "70000.00" });
});

test("a refused override keeps the dialog open with the server's message", async () => {
  fakeBackend({
    putError: { errors: [{ field: "overrides[1]", message: "This Member does not exist." }] },
  });
  await renderAppAt("/tax/whatifs/5");
  fireEvent.click(await screen.findByRole("button", { name: "Add override" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(await within(dialog).findByLabelText("Return label"), { target: { value: "salary" } });
  fireEvent.change(within(dialog).getByLabelText(/The total, in dollars/), { target: { value: "1.00" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add override" }));
  expect(await within(dialog).findByText("This Member does not exist.")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("a hypothetical depreciating asset is added with its own fields", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/whatifs/5");
  fireEvent.click(await screen.findByRole("button", { name: "Add override" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("What to do"), { target: { value: "add_record" } });
  fireEvent.change(within(dialog).getByLabelText("Type"), { target: { value: "depreciating_asset" } });
  fireEvent.change(await within(dialog).findByLabelText("Return label"), { target: { value: "work_other" } });
  fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "Sample laptop" } });
  fireEvent.change(within(dialog).getByLabelText("Cost, in dollars"), { target: { value: "2400.00" } });
  fireEvent.change(within(dialog).getByLabelText("Effective life, in years"), { target: { value: "3" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add override" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const sent = calls.find((c) => c.method === "PUT")?.body?.overrides as { record?: Record<string, unknown> }[];
  expect(sent[1].record).toMatchObject({
    record_type: "depreciating_asset",
    label: "work_other",
    description: "Sample laptop",
    amount: "2400.00",
    effective_life_years: "3",
    member_id: 1,
    from_on: "2025-07-01",
  });
});

test("Calculate shows the what-if's result, each override's effect and the totals, and Save estimate keeps it", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax/whatifs/5");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const applied = await screen.findByTestId("overrides-applied");
  expect(within(applied).getByText(/Jordan Sample: Salary and wages \(gross\) set to a stated total/)).toBeInTheDocument();
  expect(within(applied).getByText(/replacing/)).toBeInTheDocument();
  expect(within(screen.getByTestId("totals-used")).getByText(/Income statement, Sample Employer/)).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/tax/estimates/preview")?.body).toMatchObject({
    financial_year: 2026,
    member_ids: [1, 2],
    whatif_id: 5,
  });

  fireEvent.click(screen.getByRole("button", { name: "Save estimate" }));
  fireEvent.change(await screen.findByLabelText("Label (optional)"), { target: { value: "Pay rise" } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save estimate" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/estimates/11"));
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/tax/estimates")?.body).toMatchObject({
    whatif_id: 5,
    label: "Pay rise",
    financial_year: 2026,
  });
});

test("deleting a Tax what-if is confirmed, naming it", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax/whatifs/5");
  fireEvent.keyDown(await screen.findByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Tax what-if" }));
  const confirm = await screen.findByRole("alertdialog");
  expect(within(confirm).getByText("Delete Jordan earns more?")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  fireEvent.click(within(confirm).getByRole("button", { name: "Delete Tax what-if" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/whatifs"));
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/tax/whatifs/5")).toBe(true);
});

test("the Tax estimate's Save estimate saves the base, with no what-if, then opens it", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/tax?fy=2026");
  fireEvent.click(await screen.findByRole("button", { name: "Calculate" }));
  const result = await screen.findByTestId("tax-result");
  fireEvent.click(within(result).getByRole("button", { name: "Save estimate" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save estimate" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/estimates/11"));
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/tax/estimates")?.body).toEqual({
    financial_year: 2026,
    dependants: 0,
    member_ids: [1, 2],
    whatif_id: null,
    label: null,
  });
});

test("the saved estimates are listed with their basis and engine; two of one year compare", async () => {
  fakeBackend({
    estimates: [
      header({ id: 4, label: "Higher", whatif_id: 5, whatif_name: "Jordan earns more", incomplete: true }),
      header({ id: 3, engine_version: 1, current_engine: false }),
    ],
  });
  const { router } = await renderAppAt("/tax/estimates");
  const rows = await screen.findAllByRole("row");
  const [, first, second] = rows;
  expect(within(first).getByText("Higher")).toBeInTheDocument();
  expect(within(first).getByText("Jordan earns more")).toBeInTheDocument();
  expect(within(first).getByText("Incomplete")).toBeInTheDocument();
  expect(within(second).getByText("The base (your records)")).toBeInTheDocument();
  expect(within(second).getByText("Older engine")).toBeInTheDocument();
  expect(within(first).getByRole("link")).toHaveAttribute("href", "/tax/estimates/4");

  const compare = screen.getByRole("button", { name: "Compare" });
  expect(compare).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Compare estimate 4" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Compare estimate 3" }));
  expect(compare).toBeEnabled();
  fireEvent.click(compare);
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/estimates/compare"));
  expect(router.state.location.search).toMatchObject({ a: 4, b: 3 });
});

test("two estimates of different years cannot be compared", async () => {
  fakeBackend({
    estimates: [header({ id: 4, financial_year: 2025, financial_year_label: "2024-25" }), header({ id: 3 })],
  });
  await renderAppAt("/tax/estimates");
  fireEvent.click(await screen.findByRole("checkbox", { name: "Compare estimate 4" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Compare estimate 3" }));
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
  expect(screen.getByText("Only estimates of the same financial year can be compared.")).toBeInTheDocument();
});

test("with nothing saved the list says how to save", async () => {
  fakeBackend({ estimates: [] });
  await renderAppAt("/tax/estimates");
  expect(await screen.findByTestId("no-estimates")).toHaveTextContent("Nothing saved yet");
});

test("the comparison shows each changed total and every result line with its signed change", async () => {
  fakeBackend();
  await renderAppAt("/tax/estimates/compare?a=3&b=4");
  expect(await screen.findByRole("heading", { name: "Compare FY 2025-26" })).toBeInTheDocument();
  const labels = within(screen.getByTestId("label-deltas"));
  expect(labels.getByText("Jordan: Salary and wages (gross)")).toBeInTheDocument();
  expect(labels.getByText("+")).toBeInTheDocument();
  const jordan = within(screen.getByTestId("result-deltas-2"));
  const net = jordan.getByText("Net outcome").closest("tr") as HTMLElement;
  expect(within(net).getByText("-2,500.00", { selector: "span:not(.sr-only)" })).toBeInTheDocument();
  expect(within(net).getByText(/less/)).toBeInTheDocument();
  const alex = within(screen.getByTestId("result-deltas-1"));
  expect(within(alex.getByText("Net outcome").closest("tr") as HTMLElement).getByText(/no change/)).toBeInTheDocument();
  expect(screen.queryByTestId("engine-differs")).not.toBeInTheDocument();
});

test("a saved estimate shows its stored result and that it reproduces, with no re-run on the current engine", async () => {
  fakeBackend();
  await renderAppAt("/tax/estimates/3");
  expect(await screen.findByRole("heading", { name: "Base" })).toBeInTheDocument();
  expect(screen.getByTestId("reproduces")).toHaveTextContent("gives exactly this result");
  expect(screen.queryByRole("button", { name: "Re-run with current engine" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("engine-notice")).not.toBeInTheDocument();
});

test("a prefilled total shows its Income stream source and a forecast that records replaced says why", async () => {
  const prefilled = {
    ...view().totals[0],
    member_id: 2,
    member_name: "Jordan Sample",
    source: "prefill",
    value: money("85000.00"),
    contributions: [],
  };
  fakeBackend({
    saved: {
      estimate: header(),
      view: view({
        totals: [view().totals[0], prefilled],
        prefill: [
          {
            member_id: 1,
            member_name: "Alex Sample",
            label_key: "salary",
            label_name: "Salary and wages (gross)",
            stream_id: 1,
            stream_name: "Alex salary",
            value: money("0.00"),
            status: "records_win",
            text: "Not counted: the Tax register already has records for this income and year.",
          },
          {
            member_id: 2,
            member_name: "Jordan Sample",
            label_key: "salary",
            label_name: "Salary and wages (gross)",
            stream_id: 2,
            stream_name: "Jordan salary",
            value: money("85000.00"),
            status: "counted",
            text: "Income stream 'Jordan salary': stated annual amount $85,000.00",
          },
        ],
      }),
      reproduces: true,
    },
  });
  await renderAppAt("/tax/estimates/3");
  const card = await screen.findByTestId("prefill");
  const lines = within(card).getAllByTestId("prefill-line");
  expect(lines[0]).toHaveTextContent("Not counted: records used");
  expect(lines[0]).toHaveTextContent("already has records");
  expect(lines[1]).toHaveTextContent("Counted");
  expect(lines[1]).toHaveTextContent("stated annual amount $85,000.00");
  const used = within(screen.getByTestId("totals-used"));
  expect(used.getAllByText("Prefilled from Income streams")).toHaveLength(1);
});

test("an older engine's estimate offers a re-run, which saves a new estimate and opens it", async () => {
  const calls = fakeBackend({
    saved: {
      estimate: header({ engine_version: 1, current_engine: false }),
      view: view({ engine_version: 1 }),
      reproduces: null,
    },
  });
  const { router } = await renderAppAt("/tax/estimates/3");
  const notice = await screen.findByTestId("engine-notice");
  expect(within(notice).getByText(/exactly what engine v1 computed/)).toBeInTheDocument();
  expect(screen.queryByTestId("reproduces")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Re-run with current engine" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/tax/estimates/12"));
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/tax/estimates/3/rerun")).toBe(true);
});

test("the sidebar's tax group links to the what-ifs and the saved estimates", async () => {
  fakeBackend();
  await renderAppAt("/tax/estimates");
  expect(screen.getAllByRole("link", { name: "Tax what-ifs" })[0]).toHaveAttribute("href", "/tax/whatifs");
  expect(screen.getAllByRole("link", { name: "Saved estimates" })[0]).toHaveAttribute("href", "/tax/estimates");
});

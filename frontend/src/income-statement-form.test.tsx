import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

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
const setup = {
  rule_pack_version: "x",
  years: [],
  sections: [{ key: "income", name: "Income" }],
  labels: [
    {
      key: "salary",
      return_item: "1",
      name: "Salary or wages",
      description: "",
      direction: "income",
      kind: "money",
      modelled: true,
      section: "income",
      record_types: ["line"],
    },
  ],
};

type Rec = Record<string, unknown>;
const base = {
  member_id: 1,
  member_name: "Alex Sample",
  from_on: "2096-07-01",
  to_on: "2097-06-30",
  notes: null,
  label_key: "salary",
  label_name: "Salary or wages",
  return_item: "1",
  kind: "money",
  work_use_percent: null,
  value: null,
};
const statement: Rec = {
  ...base,
  id: 7,
  record_type: "income_statement",
  description: "Income statement, Sample Employer",
  amount: "100000.00",
  counted: null,
  statement: {
    gross_payments: "100000.00",
    tax_withheld: "24000.00",
    employer_super: "12000.00",
    net_pay: "69000.00",
    reportable_fringe_benefits: "0.00",
    reportable_employer_super: "0.00",
    novated_lease_post_tax: "0.00",
    other_deductions: "0.00",
    super_insurance_premiums: "0.00",
  },
};
const plainSalary: Rec = {
  ...base,
  id: 8,
  record_type: "line",
  description: "Salary, Sample Employer",
  amount: "100000.00",
  counted: "100000.00",
  statement: null,
};

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

function fakeBackend(opts: { records?: Rec[]; saveError?: Response; reconciled?: boolean } = {}) {
  const calls: Call[] = [];
  const records = opts.records ?? [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json() : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/income-reconciliation") {
        return json({ financial_year: 2097, financial_year_label: "2096-97", members: [], household: null });
      }
      if (path === "/api/library/check") return json({ checked: 0, problems: [] });
      if (path === "/api/library/links") return json([]);
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        if (request.method === "PUT") return opts.saveError ?? json(statement);
        const found = records.find((r) => r.id === Number(one[1]));
        return found ? json(found) : json({ message: "Tax record does not exist." }, 404);
      }
      if (path === "/api/tax/records" && request.method === "POST") {
        return opts.saveError ?? json({ ...statement, id: 99 }, 201);
      }
      if (path === "/api/tax/records") {
        const member = url.searchParams.get("member_id");
        return json(records.filter((r) => member === null || r.member_id === Number(member)));
      }
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

async function openNewStatement() {
  fireEvent.click(await screen.findByRole("button", { name: "Add income statement" }));
  return screen.findByRole("form", { name: "New income statement" });
}

test("an empty year offers Add income statement, which starts on that year and sends the figures as text", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/reconciliation?fy=2097");
  expect(await screen.findByTestId("reconciliation-empty")).toHaveTextContent("Add income statement");
  const form = await openNewStatement();
  // a statement has no Return label to choose, and starts on the year being looked at
  expect(within(form).queryByLabelText("Return label")).not.toBeInTheDocument();
  // as a financial year, which is the dates: nothing is typed
  expect(within(form).getByLabelText("Financial year")).toHaveValue("2097");
  expect(within(form).getByText("1 Jul 2096 to 30 Jun 2097")).toBeInTheDocument();
  expect(within(form).queryByLabelText("Statement from")).not.toBeInTheDocument();
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Income statement, Invented Pty" } });
  fireEvent.change(within(form).getByLabelText("Total gross amount ($)"), { target: { value: "100000.00" } });
  fireEvent.change(within(form).getByLabelText("PAYGW amount ($)"), { target: { value: "24000.00" } });
  fireEvent.change(within(form).getByLabelText("Employer superannuation contribution liability ($)"), { target: { value: "12000.00" } });
  fireEvent.change(within(form).getByLabelText("Net pay received ($)"), { target: { value: "69000.00" } });
  fireEvent.change(within(form).getByLabelText("Reportable fringe benefits total ($)"), { target: { value: "8000.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  // optional figures left blank are left out (the server counts them as 0); amounts stay text
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    member_id: 1,
    record_type: "income_statement",
    label: "",
    description: "Income statement, Invented Pty",
    from_on: "2096-07-01",
    to_on: "2097-06-30",
    notes: null,
    statement: {
      gross_payments: "100000.00",
      tax_withheld: "24000.00",
      employer_super: "12000.00",
      net_pay: "69000.00",
      reportable_fringe_benefits: "8000.00",
    },
  });
  await waitFor(() => expect(screen.queryByRole("form", { name: "New income statement" })).not.toBeInTheDocument());
});

test("net pay can be left blank: it is sent without net_pay (not stated, not zero)", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/reconciliation?fy=2097");
  const form = await openNewStatement();
  expect(within(form).getByLabelText("Net pay received ($)")).toHaveAttribute("placeholder", "Not stated");
  expect(within(form).getByLabelText("Net pay received ($)")).not.toHaveAttribute("aria-required", "true");
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Income statement, Invented Pty" } });
  fireEvent.change(within(form).getByLabelText("Total gross amount ($)"), { target: { value: "100000.00" } });
  fireEvent.change(within(form).getByLabelText("PAYGW amount ($)"), { target: { value: "24000.00" } });
  fireEvent.change(within(form).getByLabelText("Employer superannuation contribution liability ($)"), { target: { value: "12000.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  const sent = calls.find((c) => c.method === "POST")?.body as { statement: Record<string, string> };
  expect(sent.statement).toEqual({ gross_payments: "100000.00", tax_withheld: "24000.00", employer_super: "12000.00" });
});

test("a statement's detail page has Edit, filled with its figures, and saves them with PUT", async () => {
  const calls = fakeBackend({ records: [statement] });
  await renderAppAt("/tax/records/7");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Tax record" }));
  const form = await screen.findByRole("form", { name: "Edit Income statement, Sample Employer" });
  expect(within(form).getByLabelText("Total gross amount ($)")).toHaveValue("100000.00");
  expect(within(form).getByLabelText("Insurance premiums paid from the super balance ($)")).toHaveValue("0.00");
  fireEvent.change(within(form).getByLabelText("Net pay received ($)"), { target: { value: "69500.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT");
  expect(put?.path).toBe("/api/tax/records/7");
  expect(put?.body).toMatchObject({
    record_type: "income_statement",
    statement: { gross_payments: "100000.00", net_pay: "69500.00", other_deductions: "0.00" },
  });
});

test("a server error on a figure shows beside that figure and keeps the sheet open", async () => {
  fakeBackend({
    saveError: json(
      {
        errors: [
          { field: "tax_withheld", message: "Enter an amount (0 if there was none)." },
          { field: "other_deductions", message: "Enter 0 or more." },
        ],
      },
      422,
    ),
  });
  await renderAppAt("/tax/reconciliation?fy=2097");
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Total gross amount ($)"), { target: { value: "1.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  const tax = within(form).getByLabelText("PAYGW amount ($)").closest("label") as HTMLElement;
  expect(await within(tax).findByText("Enter an amount (0 if there was none).")).toBeInTheDocument();
  const other = within(form).getByLabelText(/^Other deductions from pay/).closest("label") as HTMLElement;
  expect(within(other).getByText("Enter 0 or more.")).toBeInTheDocument();
  expect(screen.getByRole("form", { name: "New income statement" })).toBeInTheDocument();
});

test("adding a statement where the Member-year has a plain salary line warns, and still saves", async () => {
  const calls = fakeBackend({ records: [plainSalary] });
  await renderAppAt("/tax/reconciliation?fy=2097");
  const form = await openNewStatement();
  const warning = await within(form).findByTestId("double-count");
  expect(warning).toHaveTextContent("Salary, Sample Employer");
  expect(warning).toHaveTextContent("count them twice");
  // the register was asked about this Member and year only
  expect(calls.some((c) => c.path === "/api/tax/records" && c.search.includes("member_id=1") && c.search.includes("financial_year=2097"))).toBe(true);
  // another Member has no plain salary line, so no warning
  fireEvent.change(within(form).getByLabelText(/^Member/), { target: { value: "2" } });
  await waitFor(() => expect(within(form).queryByTestId("double-count")).not.toBeInTheDocument());
  // and a warning never blocks: saving still goes to the server
  fireEvent.change(within(form).getByLabelText(/^Member/), { target: { value: "1" } });
  await within(form).findByTestId("double-count");
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
});

test("adding a plain salary line where the Member-year has a statement warns, and the statement is named", async () => {
  fakeBackend({ records: [statement] });
  await renderAppAt("/tax/records");
  fireEvent.click(await screen.findByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.click(within(form).getByLabelText("Custom dates"));
  fireEvent.change(within(form).getByLabelText("Date"), { target: { value: "2097-01-10" } });
  fireEvent.change(within(form).getByLabelText("Return label"), { target: { value: "salary" } });
  expect(await within(form).findByTestId("double-count")).toHaveTextContent("Income statement, Sample Employer");
});

test("editing a statement does not warn about itself", async () => {
  const calls = fakeBackend({ records: [statement] });
  await renderAppAt("/tax/records/7");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Tax record" }));
  const form = await screen.findByRole("form", { name: "Edit Income statement, Sample Employer" });
  await waitFor(() =>
    expect(calls.some((c) => c.path === "/api/tax/records" && c.search.includes("financial_year=2097"))).toBe(true),
  );
  expect(within(form).queryByTestId("double-count")).not.toBeInTheDocument();
});

test("the form follows the ATO statement's headings in order, with the reconciliation fields last", async () => {
  fakeBackend();
  await renderAppAt("/tax/reconciliation?fy=2097");
  const form = await openNewStatement();
  const headings = Array.from(form.querySelectorAll("legend")).map((l) => l.textContent);
  expect(headings).toEqual([
    "Period",
    "Employer",
    "Income: salary and wages",
    "Tax withheld: salary and wages",
    "Lump sum amounts",
    "Allowances",
    "Deductions",
    "Employer reported super",
    "Other amounts",
    "For the reconciliation",
  ]);
  const income = within(form).getByText("Income: salary and wages").closest("fieldset") as HTMLElement;
  expect(Array.from(income.querySelectorAll("label")).map((l) => l.textContent)).toEqual([
    "Gross amount ($)",
    "Bonuses and commissions ($)",
    "Leave payment type",
    "Paid leave amount ($)",
    "Salary sacrifice type",
    "Salary sacrifice amount ($)",
    "Total gross amount ($)",
  ]);
  const lumps = within(form).getByText("Lump sum amounts").closest("fieldset") as HTMLElement;
  expect(Array.from(lumps.querySelectorAll("label")).map((l) => l.textContent)).toEqual([
    "Lump sum payment A ($)",
    "Lump sum payment B ($)",
    "Lump sum payment D ($)",
    "Lump sum payment E ($)",
    "Lump sum payment W ($)",
  ]);
  const ours = within(form).getByText("For the reconciliation").closest("fieldset") as HTMLElement;
  expect(within(ours).getByLabelText("Net pay received ($)")).toBeInTheDocument();
});

test("the new fields are sent as text: amounts as decimal text, the types as free text", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/reconciliation?fy=2097");
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Income statement, Invented Pty" } });
  fireEvent.change(within(form).getByLabelText("Total gross amount ($)"), { target: { value: "100000.00" } });
  fireEvent.change(within(form).getByLabelText("PAYGW amount ($)"), { target: { value: "24000.00" } });
  fireEvent.change(within(form).getByLabelText("Employer superannuation contribution liability ($)"), { target: { value: "12000.00" } });
  fireEvent.change(within(form).getByLabelText("Net pay received ($)"), { target: { value: "69000.00" } });
  fireEvent.change(within(form).getByLabelText("Employer"), { target: { value: "Invented Pty" } });
  fireEvent.change(within(form).getByLabelText("Salary sacrifice type"), { target: { value: "Novated lease" } });
  fireEvent.change(within(form).getByLabelText("Bonuses and commissions ($)"), { target: { value: "5000.00" } });
  fireEvent.change(within(form).getByLabelText("Lump sum payment D ($)"), { target: { value: "700.50" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    statement: {
      employer: "Invented Pty",
      salary_sacrifice_type: "Novated lease",
      bonuses_commissions: "5000.00",
      lump_sum_d: "700.50",
    },
  });
});

const withComparison: Rec = {
  ...statement,
  statement: { ...(statement.statement as Rec), bonuses_commissions: "5000.00", salary_sacrifice_amount: "1000.00" },
  previous_year: {
    record_id: 9,
    description: "Income statement, Sample Employer (last year)",
    financial_year: 2096,
    financial_year_label: "2095-96",
    changes: [
      { figure: "bonuses_commissions", current: "5000.00", previous: "2000.00", change: "3000.00" },
      { figure: "salary_sacrifice_amount", current: "1000.00", previous: "3000.00", change: "-2000.00" },
      { figure: "lump_sum_a", current: "0.00", previous: "0.00", change: "0.00" },
    ],
  },
};

test("a statement with a previous year shows each figure beside last year's, with the change", async () => {
  fakeBackend({ records: [withComparison] });
  await renderAppAt("/tax/records/7");
  const section = await screen.findByLabelText("Compared with last year");
  expect(section).toHaveTextContent("2095-96");
  const bonus = within(section).getByTestId("compare-bonuses_commissions");
  expect(bonus).toHaveTextContent("Bonuses and commissions");
  expect(bonus).toHaveTextContent("5,000.00");
  expect(bonus).toHaveTextContent("2,000.00");
  expect(bonus).toHaveTextContent("3,000.00");
  const sacrifice = within(section).getByTestId("compare-salary_sacrifice_amount");
  expect(sacrifice).toHaveTextContent("-2,000.00");
  // a figure that is 0 in both years is not listed
  expect(within(section).queryByTestId("compare-lump_sum_a")).not.toBeInTheDocument();
});

test("a statement with no previous year shows no comparison", async () => {
  fakeBackend({ records: [{ ...statement, previous_year: null }] });
  await renderAppAt("/tax/records/7");
  await screen.findByText("Statement figures");
  expect(screen.queryByLabelText("Compared with last year")).not.toBeInTheDocument();
});

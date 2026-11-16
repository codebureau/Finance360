import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { currentFinancialYear } from "@/lib/tax";
import { everyday, fakeBackend, json, type A, type Call } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

const fund: A = {
  ...everyday,
  id: 20,
  name: "Sample Super",
  category_key: "superannuation",
  category_name: "Superannuation",
  account_type_key: "super_fund_account",
  account_type_name: "Super fund account",
  tags: [],
};

type Row = Record<string, unknown> & { id: number };

const saved = (over: Partial<Row> = {}): Row => ({
  id: 5,
  account_id: 20,
  financial_year: 2025,
  period_start: "2024-07-01",
  period_end: "2025-06-30",
  currency: "AUD",
  opening: "900.00",
  contributions_in: "200.00",
  investment_returns: "50.00",
  withdrawals: "30.00",
  closing: "1120.00",
  contributions_tax: null,
  fees_before_rebate: null,
  rebate: null,
  fees_inside_investment: null,
  division_293_released: null,
  other_withdrawals: null,
  insurance_premiums: null,
  concessional_received: null,
  non_concessional_received: null,
  options: [],
  valuation_id: 77,
  ticked_checks: [],
  checks: [],
  recorded_at: "2026-11-01T00:00:00",
  ...over,
});

/**
 * Super statements behind fetch, on top of the Accounts backend. The live check here is a stand-in
 * for the server's: the roll "adds up" only when closing is 1220.00 (the real rules are tested in
 * the backend).
 */
function backend(accounts: A[], statements: Row[] = []) {
  fakeBackend(accounts);
  const accountsFetch = globalThis.fetch;
  let rows = [...statements];
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      if (path === "/api/library/links") return json([]); // no documents
      const mine =
        /^\/api\/accounts\/(\d+)\/super-statements(\/checks)?$/.exec(path) ??
        /^\/api\/super-statements\/(\d+)$/.exec(path);
      if (!mine) return accountsFetch(request);
      const body =
        request.method === "POST" || request.method === "PUT"
          ? ((await request.clone().json()) as Record<string, unknown>)
          : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (mine[2]) {
        const warn = body?.closing !== "1220.00";
        return json([
          {
            name: "roll",
            status: warn ? "warn" : "ok",
            message: warn
              ? `Opening + contributions in + investment returns - withdrawals is AUD 1220.00, but the closing balance is ${String(body?.closing)}.`
              : "The balance roll adds up to the closing balance.",
          },
          { name: "outflows", status: "not_applicable", message: "No outflow breakdown was entered." },
          { name: "options", status: "not_applicable", message: "No Investment options entered." },
        ]);
      }
      if (request.method === "GET" && /^\/api\/super-statements\/\d+$/.test(path)) {
        const found = rows.find((r) => r.id === Number(mine[1]));
        return found ? json(found) : json({ message: "Not found." }, 404);
      }
      if (request.method === "GET") return json(rows);
      if (request.method === "DELETE") {
        rows = rows.filter((r) => r.id !== Number(mine[1]));
        return new Response(null, { status: 204 });
      }
      const made = saved({ ...body, id: request.method === "POST" ? 9 : 10, financial_year: 2026 });
      rows = [made, ...rows.filter((r) => r.id !== Number(mine[1]))];
      return json(made, request.method === "POST" ? 201 : 200);
    }),
  );
  return calls;
}

const field = (name: RegExp | string) => screen.getByLabelText(name);
const type = (name: RegExp | string, value: string) =>
  fireEvent.change(field(name), { target: { value } });

async function openAddForm() {
  fireEvent.click(await screen.findByRole("button", { name: "Add Super statement" }));
  return within(await screen.findByRole("form", { name: "Add Super statement to Sample Super" }));
}

function fillRoll(closing: string) {
  type(/^Opening balance/, "1000.00");
  type(/^Contributions in/, "200.00");
  type(/^Investment returns/, "50.00");
  type(/^Withdrawals/, "30.00");
  type(/^Closing balance/, closing);
}

test("only a super fund Account has the Super statements section", async () => {
  backend([everyday]);
  await renderAppAt(`/accounts/${everyday.id}`);
  await screen.findByRole("region", { name: `Valuations of ${everyday.name}` });
  expect(screen.queryByRole("region", { name: /^Super statements of/ })).not.toBeInTheDocument();
});

test("the list shows each year's closing balance, its option count and the checks ticked", async () => {
  backend(
    [fund],
    [
      saved({
        options: [{ name: "Growth", units: null, unit_price: null, value: "1120.00" }],
        ticked_checks: ["roll"],
      }),
      saved({ id: 6, financial_year: 2024, closing: "800.00" }),
    ],
  );
  await renderAppAt(`/accounts/${fund.id}`);
  const region = within(await screen.findByRole("region", { name: "Super statements of Sample Super" }));
  expect(await region.findByText("FY 2024-25")).toBeInTheDocument();
  expect(region.getByText("FY 2023-24")).toBeInTheDocument();
  expect(region.getByText("1 Investment option")).toBeInTheDocument();
  expect(region.getByText("Balance roll only")).toBeInTheDocument();
  expect(region.getByText("You checked: Balance roll")).toBeInTheDocument();
  expect(region.getByText("1,120.00")).toBeInTheDocument();
});

test("a roll that does not add up warns beside the figures and needs a tick to save", async () => {
  const calls = backend([fund]);
  await renderAppAt(`/accounts/${fund.id}`);
  const form = await openAddForm();
  fillRoll("1225.00");

  const alert = await form.findByRole("alert");
  expect(alert).toHaveTextContent("the closing balance is 1225.00");
  const save = form.getByRole("button", { name: "Add Super statement" });
  expect(save).toBeDisabled();
  expect(form.getByText("Tick that you have checked the figures to save.")).toBeInTheDocument();

  fireEvent.click(form.getByRole("checkbox", { name: /I have checked this/ }));
  expect(save).toBeEnabled();
  fireEvent.click(save);

  await waitFor(() => expect(calls.some((c) => c.method === "POST" && !c.path.endsWith("/checks"))).toBe(true));
  const post = calls.find((c) => c.method === "POST" && !c.path.endsWith("/checks"))!;
  expect(post.path).toBe("/api/accounts/20/super-statements");
  const year = currentFinancialYear() - 1;
  expect(post.body).toMatchObject({
    period_start: `${year - 1}-07-01`,
    period_end: `${year}-06-30`,
    opening: "1000.00",
    closing: "1225.00",
    ticked_checks: ["roll"],
    contributions_tax: null, // blank is "not stated", never zero
    concessional_received: null,
    options: [],
  });
});

test("a roll that adds up needs no tick, and a changed figure takes the tick away again", async () => {
  const calls = backend([fund]);
  await renderAppAt(`/accounts/${fund.id}`);
  const form = await openAddForm();
  fillRoll("1220.00");
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/checks"))).toBe(true));
  await waitFor(() => expect(form.queryByRole("alert")).not.toBeInTheDocument());
  expect(form.getByRole("button", { name: "Add Super statement" })).toBeEnabled();

  type(/^Closing balance/, "1221.00");
  fireEvent.click(await form.findByRole("checkbox", { name: /I have checked this/ }));
  expect(form.getByRole("button", { name: "Add Super statement" })).toBeEnabled();
  type(/^Closing balance/, "1222.00"); // the warning is a different one now: tick again
  await waitFor(() => expect(form.getByRole("checkbox", { name: /I have checked this/ })).not.toBeChecked());
  expect(form.getByRole("button", { name: "Add Super statement" })).toBeDisabled();
});

test("the optional breakdown, contributions received and option rows are sent when entered", async () => {
  const calls = backend([fund]);
  await renderAppAt(`/accounts/${fund.id}`);
  const form = await openAddForm();
  fillRoll("1220.00");

  expect(form.queryByLabelText(/^Contributions tax/)).not.toBeInTheDocument();
  fireEvent.click(form.getByRole("button", { name: "Add outflow breakdown (optional)" }));
  type(/^Contributions tax/, "20.00");
  type(/^Division 293 released/, "0.00");
  type(/^Concessional contributions received/, "150.00");

  fireEvent.click(form.getByRole("button", { name: "Add option" }));
  fireEvent.click(form.getByRole("button", { name: "Add option" }));
  fireEvent.change(form.getAllByLabelText(/^Option \d name/)[0], { target: { value: "Growth" } });
  fireEvent.change(form.getByLabelText("Option 1 units"), { target: { value: "100.5" } });
  fireEvent.change(form.getByLabelText("Option 1 value"), { target: { value: "700.00" } });
  fireEvent.change(form.getAllByLabelText(/^Option \d name/)[1], { target: { value: "Mistake" } });
  fireEvent.click(form.getByRole("button", { name: "Remove option 2" }));
  expect(form.getAllByLabelText(/^Option \d name/)).toHaveLength(1);

  await waitFor(() => expect(form.getByRole("button", { name: "Add Super statement" })).toBeEnabled());
  fireEvent.click(form.getByRole("button", { name: "Add Super statement" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && !c.path.endsWith("/checks"))).toBe(true));
  const post = calls.find((c) => c.method === "POST" && !c.path.endsWith("/checks"))!;
  expect(post.body).toMatchObject({
    contributions_tax: "20.00",
    division_293_released: "0.00", // a stated zero stays a zero
    other_withdrawals: null,
    concessional_received: "150.00",
    options: [{ name: "Growth", units: "100.5", unit_price: null, value: "700.00" }],
    ticked_checks: [],
  });
});

test("correcting opens the saved figures with the year fixed and sends a PUT", async () => {
  const calls = backend([fund], [saved({ ticked_checks: ["roll"] })]);
  await renderAppAt(`/accounts/${fund.id}`);
  const trigger = await screen.findByRole("button", { name: "Actions for Super statement 2024-25" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Correct Super statement" }));

  const form = within(await screen.findByRole("form", { name: "Correct Super statement 2024-25" }));
  expect(field(/^Opening balance/)).toHaveValue("900.00");
  expect(form.getByRole("combobox")).toHaveValue("2025");
  expect(form.getByRole("combobox")).toHaveTextContent("FY 2024-25");
  type(/^Investment returns/, "60.00");
  type(/^Closing balance/, "1130.00");
  await waitFor(() => expect(form.getByRole("checkbox", { name: /I have checked this/ })).toBeChecked());
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));

  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT")!;
  expect(put.path).toBe("/api/super-statements/5");
  expect(put.body).toMatchObject({ closing: "1130.00", ticked_checks: ["roll"] });
});

test("removing is confirmed first and names what goes with it", async () => {
  const calls = backend([fund], [saved()]);
  await renderAppAt(`/accounts/${fund.id}`);
  const trigger = await screen.findByRole("button", { name: "Actions for Super statement 2024-25" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Super statement" }));

  const dialog = within(await screen.findByRole("alertdialog"));
  expect(dialog.getByText("Remove the 2024-25 Super statement of Sample Super?")).toBeInTheDocument();
  expect(dialog.getByText(/30 June Valuation is removed with it/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  fireEvent.click(dialog.getByRole("button", { name: "Remove Super statement" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/api/super-statements/5");
});

test("clicking a row opens the statement read-only, with every figure and the ticked checks", async () => {
  backend(
    [fund],
    [
      saved({
        contributions_tax: "31.00",
        concessional_received: "150.00",
        options: [{ name: "Growth", units: "100.000000", unit_price: "11.200000", value: "1120.00" }],
        ticked_checks: ["roll"],
        checks: [
          { name: "roll", status: "warn", message: "The roll is out by 1.00." },
          { name: "outflows", status: "not_applicable", message: "No outflow breakdown was entered." },
          { name: "options", status: "ok", message: "The options add up." },
        ],
      }),
    ],
  );
  await renderAppAt(`/accounts/${fund.id}`);
  const row = (await screen.findByText("FY 2024-25")).closest("tr")!;
  fireEvent.click(within(row).getByText("1,120.00"));

  expect(await screen.findByRole("heading", { name: "Super statement FY 2024-25" })).toBeInTheDocument();
  expect(screen.getByText("Opening balance")).toBeInTheDocument();
  expect(screen.getByText("900.00")).toBeInTheDocument();
  expect(screen.getByText("Contributions tax")).toBeInTheDocument();
  expect(screen.getByText("31.00")).toBeInTheDocument();
  expect(screen.getAllByText("Not stated").length).toBeGreaterThan(0); // the outflows it left out
  expect(screen.getByText("Concessional contributions received")).toBeInTheDocument();
  expect(screen.getByText("150.00")).toBeInTheDocument();
  const options = within(screen.getByRole("region", { name: "Investment options" }));
  expect(options.getByText("Growth")).toBeInTheDocument();
  expect(options.getByText("100.000000")).toBeInTheDocument();
  const checks = within(screen.getByRole("list", { name: "Checks" }));
  expect(checks.getByText("You checked this")).toBeInTheDocument();
  expect(checks.getByText("The roll is out by 1.00.")).toBeInTheDocument();
  expect(checks.getByText("Agrees")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Documents" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Correct Super statement" })).toBeInTheDocument();
});

test("the view corrects and removes with the same forms as the row menu", async () => {
  const calls = backend([fund], [saved({ ticked_checks: ["roll"] })]);
  await renderAppAt(`/accounts/${fund.id}/super-statements/5`);
  fireEvent.click(await screen.findByRole("button", { name: "Correct Super statement" }));
  const form = within(await screen.findByRole("form", { name: "Correct Super statement 2024-25" }));
  type(/^Closing balance/, "1130.00");
  await waitFor(() => expect(form.getByRole("checkbox", { name: /I have checked this/ })).toBeChecked());
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  expect(calls.find((c) => c.method === "PUT")!.path).toBe("/api/super-statements/5");

  fireEvent.keyDown(await screen.findByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Super statement" }));
  const dialog = within(await screen.findByRole("alertdialog"));
  expect(calls.some((c) => c.method === "DELETE")).toBe(false); // confirmed first
  fireEvent.click(dialog.getByRole("button", { name: "Remove Super statement" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  // Back on the Account once it is gone.
  expect(await screen.findByRole("region", { name: "Super statements of Sample Super" })).toBeInTheDocument();
});

test("an address for a removed or unknown statement says so and links back", async () => {
  backend([fund], []);
  await renderAppAt(`/accounts/${fund.id}/super-statements/5`);
  expect(await screen.findByRole("heading", { name: "Super statement not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Account" })).toBeInTheDocument();
});

test("an Account page lists its documents", async () => {
  backend([fund], []);
  await renderAppAt(`/accounts/${fund.id}`);
  expect(await screen.findByRole("heading", { name: "Documents" })).toBeInTheDocument();
});

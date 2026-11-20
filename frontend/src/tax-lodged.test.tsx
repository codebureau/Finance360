import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { parseLodgedSearch } from "@/lib/tax-lodged";
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

// Invented figures.
const KEYS = [
  "taxable_income",
  "tax_on_taxable_income",
  "offsets",
  "medicare_levy",
  "medicare_levy_surcharge",
  "help_repayment",
  "total_tax",
  "credits",
  "net_outcome",
] as const;

const notice = (id: number, assessed: string, current: boolean, medicare: string, taxable = "85106.00") => ({
  id,
  member_id: 2,
  financial_year: 2026,
  financial_year_label: "2025-26",
  assessed_on: assessed,
  items: [
    { key: "taxable_income", amount: taxable },
    { key: "medicare_levy", amount: medicare },
    { key: "net_outcome", amount: "557.54" },
  ],
  other: current ? [{ name: "Sample processing adjustment", amount: "25.00" }] : [],
  recorded_at: "2026-10-09T01:00:00",
  current,
  superseded_by_id: current ? null : 8,
});

const line = (key: string, engine: string, lodged: string | null, difference: string | null, status: string) => ({
  key,
  status,
  engine,
  lodged,
  difference,
});

const worksheet = (more: Record<string, unknown> = {}) => ({
  member_id: 2,
  member_name: "Jordan Sample",
  financial_year: 2026,
  financial_year_label: "2025-26",
  dependants: 0,
  current: notice(8, "2026-10-09", true, "1702.07"),
  history: [notice(8, "2026-10-09", true, "1702.07"), notice(7, "2026-08-14", false, "1712.12", "85606.00")],
  lines: [
    line("taxable_income", "85106.00", "85106.00", "0.00", "matched"),
    line("tax_on_taxable_income", "16319.80", null, null, "not_lodged"),
    line("offsets", "6.00", null, null, "not_lodged"),
    line("medicare_levy", "1702.12", "1702.07", "0.05", "differs"),
    line("medicare_levy_surcharge", "1276.59", null, null, "not_lodged"),
    line("help_repayment", "0.00", null, null, "not_lodged"),
    line("total_tax", "19292.51", null, null, "not_lodged"),
    line("credits", "19850.00", null, null, "not_lodged"),
    line("net_outcome", "557.49", "557.54", "-0.05", "rounding"),
  ],
  unmatched: [{ name: "Sample processing adjustment", amount: "25.00", is_other: true }],
  matched_count: 2,
  differs_count: 1,
  incomplete: false,
  engine_version: 3,
  rule_pack_version: "au/2026-27/r3",
  problems: [],
  ...more,
});

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

function fakeBackend(sheet: Record<string, unknown> = worksheet(), writeError?: unknown) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method) ? await request.json().catch(() => undefined) : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/household") return json(household);
      if (path === "/api/tax/lodged-results/worksheet") return json(sheet);
      if (path === "/api/tax/lodged-results" && request.method === "POST") {
        return writeError ? json(writeError, 422) : json(notice(9, "2026-10-09", true, "1702.07"), 201);
      }
      if (path.startsWith("/api/tax/lodged-results/") && request.method === "PUT") {
        return writeError ? json(writeError, 422) : json(notice(9, "2026-11-01", true, "1700.00"));
      }
      if (path === "/api/library/links") return json([]);
      return json({ message: `unexpected ${request.method} ${path}` }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the worksheet shows the estimate beside the notice with each difference and the unmatched line", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  const card = await screen.findByTestId("worksheet");
  expect(within(card).getByText("Jordan Sample, FY 2025-26")).toBeInTheDocument();
  expect(screen.getByTestId("summary")).toHaveTextContent("2 of 3 items matched, 1 differs, 1 line the engine cannot match");
  const row = within(card).getByText("Medicare levy").closest("tr") as HTMLElement;
  expect(within(row).getByText("1,702.12")).toBeInTheDocument(); // the estimate
  expect(within(row).getByText("1,702.07")).toBeInTheDocument(); // the notice
  expect(within(row).getByText("0.05")).toBeInTheDocument(); // engine less lodged
  expect(within(row).getByText("Differs")).toBeInTheDocument();
  // the notice's rounding: the difference stays visible beside the badge
  const net = within(card).getByText("Net outcome").closest("tr") as HTMLElement;
  expect(within(net).getByText("Within rounding")).toBeInTheDocument();
  expect(within(net).getByText("-0.05")).toBeInTheDocument();
  // an item not on the notice is shown as not entered, never as a zero
  expect(within(card).getAllByText("Not entered").length).toBe(6);
  const unmatched = screen.getByTestId("unmatched");
  expect(within(unmatched).getByText("Sample processing adjustment")).toBeInTheDocument();
  expect(within(unmatched).getByText("25.00")).toBeInTheDocument();
  const asked = calls.find((c) => c.path === "/api/tax/lodged-results/worksheet");
  expect(asked?.search).toContain("member_id=2");
  expect(asked?.search).toContain("financial_year=2026");
});

test("an earlier, superseded notice is in the history and opens as entered", async () => {
  fakeBackend();
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  const notices = await screen.findAllByTestId("notice");
  expect(notices).toHaveLength(2);
  expect(within(notices[0]).getByText("Current")).toBeInTheDocument();
  expect(within(notices[1]).getByText("Superseded")).toBeInTheDocument();
  expect(within(notices[1]).getByText("Assessed 2026-08-14")).toBeInTheDocument();
  expect(within(notices[1]).getByText("85,606.00")).toBeInTheDocument();
  expect(within(notices[1]).getByText("1,712.12")).toBeInTheDocument();
});

test("an amended notice is entered from the current one and saved as a replacement", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  fireEvent.click(await screen.findByRole("button", { name: "Enter an amended notice" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Medicare levy")).toHaveValue("1702.07"); // the current notice's
  expect(within(dialog).getByLabelText("Other line 1 name")).toHaveValue("Sample processing adjustment");
  fireEvent.change(within(dialog).getByLabelText("Date on the notice"), { target: { value: "2026-11-01" } });
  fireEvent.change(within(dialog).getByLabelText("Medicare levy"), { target: { value: "1700.00" } });
  fireEvent.change(within(dialog).getByLabelText("Credits"), { target: { value: "19850.00" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Add a line" }));
  fireEvent.change(within(dialog).getByLabelText("Other line 2 name"), { target: { value: "Sample refund interest" } });
  fireEvent.change(within(dialog).getByLabelText("Other line 2 amount"), { target: { value: "1.20" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save amended notice" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT");
  expect(put?.path).toBe("/api/tax/lodged-results/8");
  expect(put?.body).toMatchObject({
    member_id: 2,
    financial_year: 2026,
    assessed_on: "2026-11-01",
    items: { medicare_levy: "1700.00", credits: "19850.00", taxable_income: "85106.00" },
    other: [
      { name: "Sample processing adjustment", amount: "25.00" },
      { name: "Sample refund interest", amount: "1.20" },
    ],
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("with no notice the estimate stands alone and the first notice is a create", async () => {
  const calls = fakeBackend(
    worksheet({
      current: null,
      history: [],
      unmatched: [],
      matched_count: 0,
      differs_count: 0,
      lines: KEYS.map((key) => line(key, "1.00", null, null, "not_lodged")),
    }),
  );
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  expect(await screen.findByTestId("no-notice")).toBeInTheDocument();
  expect(screen.queryByTestId("unmatched")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Enter Lodged result" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Date on the notice"), { target: { value: "2026-10-09" } });
  fireEvent.change(within(dialog).getByLabelText("Net outcome"), { target: { value: "-120.50" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save notice" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    member_id: 2,
    financial_year: 2026,
    assessed_on: "2026-10-09",
    items: { net_outcome: "-120.50" },
    other: [],
  });
});

test("a refused notice shows the server's message against the field and stays open", async () => {
  fakeBackend(worksheet(), { errors: [{ field: "items.credits", message: "Enter an amount in dollars and cents, such as 1234.56." }] });
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  fireEvent.click(await screen.findByRole("button", { name: "Enter an amended notice" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Credits"), { target: { value: "12.345" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save amended notice" }));
  expect(await within(dialog).findByText(/Enter an amount in dollars and cents/)).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("a year the estimate cannot be calculated for still shows the notice, and why", async () => {
  fakeBackend(
    worksheet({
      problems: ["The Rule pack holds no tax figures for FY 2039-40."],
      lines: KEYS.map((key) => line(key, "", null, null, "no_estimate")).map((l) => ({ ...l, engine: null })),
    }),
  );
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  const notice = await screen.findByTestId("no-estimate");
  expect(notice).toHaveTextContent("The estimate could not be calculated");
  expect(notice).toHaveTextContent("holds no tax figures for FY 2039-40");
  expect(within(notice).getByRole("link", { name: /Open Rule figures for FY 2025-26/ })).toBeInTheDocument();
  expect(screen.getByTestId("unmatched")).toBeInTheDocument();
});

test("the Tax group of the sidebar links to the worksheet", async () => {
  fakeBackend();
  await renderAppAt("/tax/lodged?member=2&fy=2026");
  const link = await screen.findByRole("link", { name: "Return worksheet" });
  expect(link).toHaveAttribute("href", "/tax/lodged");
});

test("the Member and year are URL search params, ignoring nonsense", () => {
  expect(parseLodgedSearch({ member: "2", fy: "2026" })).toEqual({ member: 2, fy: 2026 });
  expect(parseLodgedSearch({ member: "x", fy: "1" })).toEqual({});
});

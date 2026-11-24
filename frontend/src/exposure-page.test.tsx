import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { everyday, fakeBackend as accountsBackend, json } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

vi.mock("@/components/echart", () => ({
  default: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
}));

afterEach(() => vi.unstubAllGlobals());

let prefs: Record<string, string> = {};
const puts: Record<string, string | null>[] = [];
beforeEach(() => {
  prefs = {};
  puts.length = 0;
});

const aud = (amount: string, minor: number) => ({ amount, amount_minor: minor, currency: "AUD" });
const line = (kind: string, key: string | null, name: string, amount: string, minor: number, percent: string) => ({
  kind,
  key,
  name,
  amount: aud(amount, minor),
  percent,
});
const money = (minor: number) => aud((minor / 100).toFixed(2), minor);
const figures = (lines: ReturnType<typeof line>[], analysed: number, not: number) => ({
  lines,
  analysed: money(analysed),
  not_analysed: money(not),
  total: money(analysed + not),
});

// Invented: a joint Account of 100.01 (50.01 / 50.00) and Alex's sole 40.00, 140.01 analysed in all;
// Jordan also has 900.00 of savings and Alex a 300.00 loan, which are Not analysed.
const HOUSEHOLD_LINES = [
  line("label", "australian_shares", "Australian shares", "85.00", 8500, "60.71"),
  line("label", "cash", "Cash", "30.01", 3001, "21.43"),
  line("no_profile", null, "No profile", "15.00", 1500, "10.71"),
  line("unallocated", null, "Unallocated", "10.00", 1000, "7.14"),
];
const ROLLUP = {
  as_at: "2026-09-30",
  dimension_key: "asset_class",
  dimension_name: "Asset class",
  household: figures(HOUSEHOLD_LINES, 14001, 60000),
  position_net: money(74001),
  members: [
    {
      member_id: 1,
      name: "Alex Sample",
      figures: figures(
        [
          line("label", "australian_shares", "Australian shares", "55.00", 5500, "61.11"),
          line("label", "cash", "Cash", "15.01", 1501, "16.68"),
          line("no_profile", null, "No profile", "15.00", 1500, "16.67"),
          line("unallocated", null, "Unallocated", "5.00", 500, "5.55"),
        ],
        9001,
        -30000,
      ),
    },
    {
      member_id: 2,
      name: "Jordan Sample",
      figures: figures(
        [
          line("label", "australian_shares", "Australian shares", "30.00", 3000, "60.00"),
          line("label", "cash", "Cash", "15.00", 1500, "30.00"),
          line("unallocated", null, "Unallocated", "5.00", 500, "10.00"),
        ],
        5000,
        90000,
      ),
    },
  ],
  categories: [
    { key: "investments", name: "Investments", figures: figures(HOUSEHOLD_LINES, 14001, 0) },
    { key: "cash", name: "Cash", figures: figures([], 0, 90000) },
    { key: "loans_credit", name: "Loans", figures: figures([], 0, -30000) },
  ],
  accounts: [
    {
      account_id: 7,
      name: "Joint investments",
      category_key: "investments",
      ownership: "joint",
      flagged: false,
      figures: figures(
        [
          line("label", "australian_shares", "Australian shares", "60.00", 6000, "59.99"),
          line("label", "cash", "Cash", "30.01", 3001, "30.00"),
          line("unallocated", null, "Unallocated", "10.00", 1000, "10.00"),
        ],
        10001,
        0,
      ),
    },
    {
      account_id: 8,
      name: "Alex's shares",
      category_key: "investments",
      ownership: "sole",
      flagged: false,
      figures: figures(
        [
          line("label", "australian_shares", "Australian shares", "25.00", 2500, "62.50"),
          line("no_profile", null, "No profile", "15.00", 1500, "37.50"),
        ],
        4000,
        0,
      ),
    },
  ],
  not_analysed: [
    { account_id: 9, name: "Savings", category_key: "cash", ownership: "sole", value: money(90000), reason: null },
    { account_id: 10, name: "Loan", category_key: "loans_credit", ownership: "sole", value: money(-30000), reason: null },
    { account_id: 11, name: "Unvalued", category_key: "cash", ownership: "sole", value: null, reason: "The Account has no value in the Position, so it is left out." },
  ],
};

const DIMENSIONS = [
  { key: "asset_class", name: "Asset class", labels: [{ key: "other", name: "Other" }] },
  { key: "region", name: "Region", labels: [{ key: "other", name: "Other" }] },
];

function backend(rollup: unknown = ROLLUP) {
  accountsBackend([{ ...everyday, id: 7, name: "Joint investments" }]);
  const inner = globalThis.fetch;
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/preferences") {
        if (request.method === "PUT") {
          const sent = ((await request.json()) as { preferences: Record<string, string | null> }).preferences;
          puts.push(sent);
          for (const [k, v] of Object.entries(sent)) {
            if (v === null) delete prefs[k];
            else prefs[k] = v;
          }
        }
        return json({ preferences: { ...prefs } });
      }
      if (url.pathname === "/api/look-through") {
        calls.push(url);
        return json(rollup);
      }
      if (url.pathname === "/api/exposure/vocabulary") return json(DIMENSIONS);
      return inner(request);
    }),
  );
  return calls;
}

test("the Exposure screen is in the sidebar and leads with a headline and a Household chart", async () => {
  const calls = backend();
  await renderAppAt("/exposure");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Exposure" })).toHaveAttribute("href", "/exposure");
  const content = within(screen.getByTestId("content"));
  expect(await content.findByRole("heading", { level: 1, name: "Exposure" })).toBeInTheDocument();
  expect(await content.findByRole("heading", { name: "Household exposure by asset class" })).toBeInTheDocument();
  expect(
    content.getByText("19% of the Position (140.01) is analysed. Mostly Australian shares and Cash; 10.71% has no profile yet."),
  ).toBeInTheDocument();
  expect(
    await content.findByRole("img", { name: /Donut chart\..*The analysed part of the Household's Position by asset class\./ }),
  ).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.has("on")).toBe(false);
  // the figures are behind a disclosure and end in the Household total, the Position's figure
  expect(content.queryByRole("table", { name: "Exposure figures by Household" })).not.toBeInTheDocument();
  fireEvent.click(content.getByRole("button", { name: "Show figures" }));
  const table = within(await content.findByRole("table", { name: "Exposure figures by Household" }));
  expect(table.getByText("85.00")).toBeInTheDocument();
  expect(table.getByText("Not analysed").closest("tr")).toHaveTextContent("600.00");
  expect(table.getByText("Household total").closest("tr")).toHaveTextContent("740.01");
});

test("what is Not analysed is listed with its values, so the figures reconcile to the Position", async () => {
  backend();
  await renderAppAt("/exposure?asat=2026-09-30");
  const cash = within(await screen.findByRole("region", { name: "Cash" }));
  expect(cash.getByText("Held as money, so there is nothing inside to look through.")).toBeInTheDocument();
  const list = within(cash.getByRole("list", { name: "Cash not analysed" }));
  const savings = list.getByText("Savings").closest("li")!;
  expect(savings).toHaveTextContent("900.00");
  const link = within(savings).getByRole("link", { name: "Savings" });
  expect(link).toHaveAttribute("href", expect.stringContaining("/accounts/9"));
  expect(link).toHaveAttribute("href", expect.stringContaining("asat=2026-09-30"));
  const loans = within(screen.getByRole("region", { name: "Loans" }));
  expect(loans.getByText("Loan").closest("li")).toHaveTextContent("-300.00");
  expect(loans.getByText("Debts: they reduce the Position but hold no investments.")).toBeInTheDocument();
  expect(list.getByText("Unvalued").closest("li")).toHaveTextContent("Not counted");
  const note = screen.getByText(/Accounts with no look-through/, { selector: "p" });
  expect(note).toHaveTextContent("Analysed 140.01 plus Not analysed 600.00 is 740.01, the Position as at 2026-09-30.");
  expect(within(note).getByRole("link", { name: "as at 2026-09-30" })).toHaveAttribute("href", "/position?on=2026-09-30");
});

test("the level switcher shows one small card per Member, with its own chart, in the one chart form", async () => {
  backend();
  const { router } = await renderAppAt("/exposure");
  await screen.findByRole("heading", { name: "Household exposure by asset class" });
  fireEvent.click(screen.getByRole("tab", { name: "Member" }));
  expect(await screen.findByRole("heading", { name: "Exposure by Member, by asset class" })).toBeInTheDocument();
  expect(router.state.location.search).toMatchObject({ level: "member" });
  expect(screen.getByRole("tab", { name: "Member" })).toHaveAttribute("aria-selected", "true");
  const alex = within(screen.getByRole("region", { name: "Alex Sample by asset class" }));
  // the chart is the analysed 90.01; the card says what else makes up the Position share
  expect(alex.getByText("90.01")).toBeInTheDocument();
  expect(
    alex.getByText((_, el) => el?.tagName === "P" && el.textContent === "Not analysed -300.00, so the total is -209.99"),
  ).toBeInTheDocument();
  expect(alex.getByRole("img", { name: /Alex Sample by asset class\./ })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Jordan Sample by asset class" })).toBeInTheDocument();
  expect(screen.getByText("Alex Sample holds the most analysed value, 64% of it; Jordan Sample holds 36%.")).toBeInTheDocument();
  // one switch serves every card
  expect(screen.getAllByTestId("share-chart").map((c) => c.getAttribute("data-form"))).toEqual(["donut", "donut"]);
  fireEvent.click(screen.getByRole("radio", { name: "Bars" }));
  await waitFor(() => expect(puts).toEqual([{ "exposure.member": "bars" }]));
  expect(screen.getAllByTestId("share-chart").map((c) => c.getAttribute("data-form"))).toEqual(["bars", "bars"]);
});

test("Category cards leave out a Category with nothing analysed, and say so", async () => {
  backend();
  await renderAppAt("/exposure?level=category");
  expect(await screen.findByRole("region", { name: "Investments by asset class" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Cash by asset class" })).not.toBeInTheDocument();
  expect(screen.getByText(/Nothing analysed in Cash, Loans: all of it is Not analysed/)).toBeInTheDocument();
});

test("the Account level links each card to that Account's own look-through, for the same date and Dimension", async () => {
  backend();
  await renderAppAt("/exposure?level=account&dimension=region");
  const card = within(await screen.findByRole("region", { name: "Joint investments by asset class" }));
  expect(card.getByRole("link", { name: "Joint investments" })).toHaveAttribute(
    "href",
    expect.stringMatching(/\/accounts\/7\?.*dimension=region/),
  );
  // the table behind it reconciles to the Household total
  fireEvent.click(screen.getByRole("button", { name: "Show figures" }));
  const table = within(await screen.findByRole("table", { name: "Exposure figures by Account" }));
  expect(table.getByText("Household total").closest("tr")).toHaveTextContent("740.01");
});

test("the Dimension switcher asks the server for it and keeps the level in the URL", async () => {
  const calls = backend();
  const { router } = await renderAppAt("/exposure?level=member");
  await screen.findByRole("heading", { name: "Exposure by Member, by asset class" });
  fireEvent.click(screen.getByRole("tab", { name: "Region" }));
  await waitFor(() => expect(calls.at(-1)?.searchParams.get("dimension")).toBe("region"));
  expect(router.state.location.search).toMatchObject({ level: "member", dimension: "region" });
});

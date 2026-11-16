import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import { formatMoney } from "@/lib/money";
import type { Position, PositionHistory } from "@/lib/position";

// jsdom has no canvas: stand in for the ECharts host and keep the option it was given.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const options: { current: any }[] = [];
vi.mock("@/components/echart", () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  default: ({ option, label }: { option: any; label: string }) => {
    options.push({ current: option });
    return <div data-testid="mock-echart" role="img" aria-label={label} />;
  },
}));
const lastOption = () => options[options.length - 1].current;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// Invented: Alex holds a 100.01 account solely and half of a 100.01 joint account (50.01 / 50.00);
// Jordan holds the other half. A GBP account has no FX rate, so it is flagged and not in the totals.
const alexFigures = { member_id: 1, sole: "100.01", joint_share: "50.01", total: "150.02" };
const jordanFigures = { member_id: 2, sole: "0.00", joint_share: "50.00", total: "50.00" };
const figures = {
  household: "200.02",
  joint: "100.01",
  members: [alexFigures, jordanFigures],
};
const none = {
  household: "0.00",
  joint: "0.00",
  members: [
    { member_id: 1, sole: "0.00", joint_share: "0.00", total: "0.00" },
    { member_id: 2, sole: "0.00", joint_share: "0.00", total: "0.00" },
  ],
};
const account = (over: Record<string, unknown>) => ({
  account_id: 1,
  name: "Everyday",
  ownership: "sole",
  currency: "AUD",
  tags: [],
  status: "ok",
  valuation_as_at: "2026-09-30",
  source: "statement",
  original: "100.01",
  rate: null,
  rate_as_at: null,
  rate_source: null,
  value: "100.01",
  shares: { "1": "100.01" },
  ...over,
});

const position = (over: Partial<Record<string, unknown>> = {}): Position =>
  ({
    as_at: "2026-10-17",
    tag: null,
    members: [
      { id: 1, name: "Alex Sample" },
      { id: 2, name: "Jordan Sample" },
    ],
    tags: ["everyday", "retirement"],
    classes: [
      {
        class: "asset",
        figures,
        flagged: 1,
        categories: [
          {
            key: "cash",
            name: "Cash",
            figures,
            flagged: 0,
            accounts: [
              account({}),
              account({
                account_id: 2,
                name: "Joint savings",
                ownership: "joint",
                original: "100.01",
                value: "100.01",
                shares: { "1": "50.01", "2": "50.00" },
              }),
            ],
          },
          {
            key: "overseas_pensions",
            name: "Overseas pensions",
            figures: none,
            flagged: 1,
            accounts: [
              account({
                account_id: 3,
                name: "UK Pension",
                currency: "GBP",
                status: "no_rate",
                original: "500.00",
                value: null,
                shares: {},
              }),
            ],
          },
        ],
      },
      { class: "liability", figures: none, flagged: 0, categories: [] },
    ],
    net: figures,
    flagged: 1,
    ...over,
  }) as Position;

// Invented history: the middle month-end is incomplete (the loan had no Valuation yet).
const fig = (household: string, joint = "0.00") => ({ household, joint, members: [
  { member_id: 1, sole: household, joint_share: "0.00", total: household },
  { member_id: 2, sole: "0.00", joint_share: "0.00", total: "0.00" },
] });
const history = (): PositionHistory => ({
  tag: null,
  members: [
    { id: 1, name: "Alex Sample" },
    { id: 2, name: "Jordan Sample" },
  ],
  tags: ["everyday"],
  points: [
    { on: "2026-07-31", assets: fig("1000.01", "10.00"), liabilities: fig("-400.00"), net: fig("600.01", "10.00"), flagged: 0 },
    { on: "2026-08-31", assets: fig("1100.01", "11.00"), liabilities: fig("0.00"), net: fig("1100.01", "11.00"), flagged: 1 },
    { on: "2026-09-30", assets: fig("1200.01", "12.00"), liabilities: fig("-390.00"), net: fig("810.01", "12.00"), flagged: 0 },
  ],
});

function stub(body: Position = position()) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/position/history") {
        historyUrls.push(url.pathname + url.search);
        return json(history());
      }
      if (url.pathname !== "/api/position") return json({}, 404); // shell status calls
      urls.push(url.pathname + url.search);
      return json(body);
    }),
  );
  return urls;
}
const historyUrls: string[] = [];

/** Renders the routed app at /position (real router, so search params and links work). */
async function renderPage(path = "/position") {
  return renderAppAt(path);
}

afterEach(() => vi.unstubAllGlobals());

test("money is formatted in one place, from the exact string, never via a number", () => {
  expect(formatMoney("1294742.56")).toBe("1,294,742.56");
  expect(formatMoney("-497000.00")).toBe("-497,000.00");
  expect(formatMoney("12345678901234567.89")).toBe("12,345,678,901,234,567.89"); // beyond float
  expect(formatMoney(null)).toBe("—"); // not valued: a dash, never 0.00
  expect(formatMoney("5.00", "GBP")).toBe("GBP 5.00");
});

test("asks for today's Position by default; only the first level (Class, Category) is open", async () => {
  const urls = stub();
  await renderPage();
  const cash = await screen.findByRole("button", { name: "Cash" });
  expect(urls).toEqual(["/api/position"]); // no date: the server uses the Household's today
  expect(screen.getByLabelText("As at")).toHaveValue("2026-10-17");
  const tree = screen.getByTestId("position-tree");
  expect(within(tree).getByText("Assets")).toBeInTheDocument();
  expect(within(tree).getByText("Liabilities")).toBeInTheDocument();
  expect(cash).toHaveAttribute("aria-expanded", "false");
  // Accounts sit behind a deliberate expand.
  expect(within(tree).queryByText("Everyday")).not.toBeInTheDocument();
  expect(within(tree).queryByText("UK Pension")).not.toBeInTheDocument();
});

test("a category can be expanded and collapsed again", async () => {
  stub();
  await renderPage();
  const cash = await screen.findByRole("button", { name: "Cash" });
  fireEvent.click(cash);
  expect(cash).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Everyday")).toBeInTheDocument();
  fireEvent.click(cash);
  expect(cash).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Everyday")).not.toBeInTheDocument();
});

test("an Account row links through to its detail page instead of expanding", async () => {
  stub();
  const { router } = await renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Cash" }));
  const link = screen.getByRole("link", { name: "Joint savings" });
  expect(link).toHaveAttribute("href", "/accounts/2");
  fireEvent.click(link);
  await waitFor(() => expect(router.state.location.pathname).toBe("/accounts/2"));
});

test("the totals bar is sticky and shows Assets, Liabilities, Net and the not-counted count", async () => {
  stub();
  await renderPage();
  const bar = await screen.findByTestId("totals-bar");
  expect(bar).toHaveClass("sticky");
  expect(bar).toHaveTextContent("Assets");
  expect(bar).toHaveTextContent("200.02");
  expect(bar).toHaveTextContent("Net");
  expect(within(bar).getByRole("status")).toHaveTextContent("1 not counted");
});

test("negative amounts carry a sign and the money-negative colour", async () => {
  const p = position();
  p.classes[1] = {
    class: "liability",
    figures: { ...none, household: "-400.00" },
    flagged: 0,
    categories: [],
  } as Position["classes"][number];
  stub(p);
  await renderPage();
  await screen.findByTestId("totals-bar");
  const liabilities = within(screen.getByTestId("totals-bar")).getByText("-400.00");
  expect(liabilities).toHaveClass("text-money-negative");
  expect(liabilities).toHaveAttribute("data-negative", "true");
  const positive = within(screen.getByTestId("totals-bar")).getAllByText("200.02")[0];
  expect(positive).not.toHaveClass("text-money-negative");
});

test("an Account with no FX rate is flagged with a badge and words, shown as a dash, not zeroed", async () => {
  stub();
  await renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Overseas pensions" }));
  expect(await screen.findByText("No FX rate for GBP on or before 2026-10-17")).toBeInTheDocument();
  const row = screen.getByText("UK Pension").closest("li")!;
  expect(within(row).getByText("—")).toBeInTheDocument();
  expect(within(row).getByText("Not counted")).toBeInTheDocument();
  expect(screen.getByText(/1 Account is not in these totals/)).toHaveTextContent(
    "1 Account is not in these totals: no Valuation or FX rate on or before 2026-10-17.",
  );
});

test("a converted line names the date and the source of the FX rate it used", async () => {
  const base = position();
  const pension = account({
    account_id: 3,
    name: "UK Pension",
    currency: "GBP",
    original: "500.00",
    rate: "2.0399836801",
    rate_as_at: "2026-10-19",
    rate_source: "rba",
    value: "1019.99",
    shares: { "1": "1019.99" },
  });
  stub({
    ...base,
    classes: base.classes.map((c) => ({
      ...c,
      categories: c.categories.map((cat) =>
        cat.key === "overseas_pensions" ? { ...cat, flagged: 0, accounts: [pension] } : cat,
      ),
    })),
  } as Position);
  await renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Overseas pensions" }));
  expect(await screen.findByText(/GBP 500.00 @ 2.0399836801 \(rate of 2026-10-19, rba\)/)).toBeInTheDocument();
});

test("the By Member view splits joint Accounts by share and Joint shows them whole", async () => {
  stub();
  await renderPage();
  fireEvent.click(await screen.findByLabelText("By Member"));
  fireEvent.click(await screen.findByRole("button", { name: "Cash" }));
  const tree = screen.getByTestId("position-tree");
  const joint = within(tree).getByText("Joint savings").closest("li")!;
  expect(joint).toHaveTextContent("50.01");
  expect(joint).toHaveTextContent("50.00");
  expect(within(tree).getByText("Alex Sample")).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("Joint"));
  // sole Accounts drop out (the view change goes through the URL, so it settles asynchronously)
  await waitFor(() =>
    expect(within(screen.getByTestId("position-tree")).queryByText("Everyday")).not.toBeInTheDocument(),
  );
  expect(
    within(screen.getByTestId("position-tree")).getByText("Joint savings").closest("li"),
  ).toHaveTextContent("100.01");
});

test("the totals show that Members (held solely) plus Joint equal the Household", async () => {
  stub();
  await renderPage();
  expect(await screen.findByTestId("reconciliation")).toHaveTextContent(
    "Alex Sample (held solely) 100.01 + Jordan Sample (held solely) 0.00 + Joint 100.01 = Household 200.02",
  );
});

test("the date, view, Tag and layout live in the URL, so a deep link restores them", async () => {
  const urls = stub();
  const { router } = await renderPage(
    "/position?on=2025-10-17&tag=retirement&view=member&layout=table",
  );
  await waitFor(() => expect(urls).toContain("/api/position?on=2025-10-17&tag=retirement"));
  expect(screen.getByLabelText("As at")).toHaveValue("2025-10-17");
  expect(screen.getByLabelText("By Member")).toBeChecked();
  expect(screen.getByLabelText("Table")).toBeChecked();
  expect(screen.getByTestId("position-table")).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("Joint"));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ view: "joint" }));
  fireEvent.click(screen.getByLabelText("Household"));
  await waitFor(() => expect(router.state.location.search).not.toHaveProperty("view"));
  fireEvent.change(screen.getByLabelText("As at"), { target: { value: "2024-01-31" } });
  await waitFor(() => expect(urls).toContain("/api/position?on=2024-01-31&tag=retirement"));
  expect(router.state.location.search).toMatchObject({ on: "2024-01-31", tag: "retirement" });
});

test("the table view is a real table with a row header and the Account link", async () => {
  stub();
  await renderPage("/position?layout=table");
  const table = await screen.findByRole("table", { name: "Position by Class, Category and Account" });
  expect(within(table).getByRole("columnheader", { name: "Household" })).toBeInTheDocument();
  expect(within(table).getByRole("rowheader", { name: /Everyday/ })).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "Everyday" })).toHaveAttribute("href", "/accounts/1");
});

test("the history card starts collapsed (no fetch); opened, its table shows exact figures and feeds the chart", async () => {
  historyUrls.length = 0;
  stub();
  await renderPage();
  const toggle = await screen.findByRole("button", { name: "History" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("region", { name: "Net position history table" })).not.toBeInTheDocument();
  expect(historyUrls).toEqual([]);

  fireEvent.click(toggle);
  const table = await screen.findByRole("region", { name: "Net position history table" });
  expect(historyUrls).toEqual(["/api/position/history"]);
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(4); // header + three month-ends
  expect(rows[1]).toHaveTextContent("2026-07-31");
  expect(rows[1]).toHaveTextContent("600.01"); // Net, as the exact string
  expect(rows[1]).toHaveTextContent("-400.00");
  expect(rows[2]).toHaveTextContent("1 (incomplete)");
  expect(rows[2]).toHaveAttribute("data-flagged", "true");
  expect(screen.getByText(/Hollow markers are incomplete/)).toBeInTheDocument();

  // The (mocked) chart gets an option built from the same points.
  const chart = await screen.findByTestId("mock-echart");
  expect(chart).toHaveAccessibleName(/Assets, Liabilities, Net over time/);
  const option = lastOption();
  expect(option.xAxis.data).toEqual(["2026-07-31", "2026-08-31", "2026-09-30"]);
  expect(option.series.map((s: { name: string }) => s.name)).toEqual([
    "Assets",
    "Liabilities",
    "Net",
  ]);
  expect(option.series[2].data[1].flagged).toBe(true);
});

test("the history follows the Tag filter and the view", async () => {
  historyUrls.length = 0;
  stub();
  await renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "History" }));
  await screen.findByRole("region", { name: "Net position history table" });
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "retirement" } });
  await waitFor(() => expect(historyUrls).toContain("/api/position/history?tag=retirement"));

  fireEvent.click(screen.getByLabelText("Joint"));
  const table = await screen.findByRole("region", { name: "Net position history table" });
  await waitFor(() => expect(within(table).getAllByRole("row")[3]).toHaveTextContent("12.00")); // joint
  fireEvent.click(screen.getByLabelText("By Member"));
  expect(await within(table).findByRole("columnheader", { name: "Alex Sample Net" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Household Net" })).toBeInTheDocument();
});

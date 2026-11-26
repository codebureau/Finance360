import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import { formatMoney } from "@/lib/money";
import type { Position, PositionHistory } from "@/lib/position";

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
/** The latest option given to the chart whose accessible label matches. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const optionFor = (label: RegExp): any => options.filter((o) => label.test(o.label)).at(-1)!.current;

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

// A richer household for the cards: three asset Categories in the invented amounts below.
const money = (household: string, alex: string, jordan: string, joint = "0.00") => ({
  household,
  joint,
  members: [
    { member_id: 1, sole: alex, joint_share: "0.00", total: alex },
    { member_id: 2, sole: jordan, joint_share: "0.00", total: jordan },
  ],
});
const category = (key: string, name: string, figs: ReturnType<typeof money>, accounts: unknown[] = []) => ({
  key,
  name,
  figures: figs,
  flagged: 0,
  accounts,
});
const rich = (extra: unknown[] = []): Position => {
  const categories = [
    category("investments", "Investments", money("600.00", "400.00", "200.00")),
    category("property", "Property", money("300.00", "300.00", "0.00")),
    category("cash", "Cash", money("100.00", "50.00", "50.00", "20.00")),
    ...extra,
  ];
  return {
    as_at: "2026-10-17",
    tag: null,
    members: [
      { id: 1, name: "Alex Sample" },
      { id: 2, name: "Jordan Sample" },
    ],
    tags: ["everyday"],
    classes: [
      { class: "asset", figures: money("1000.00", "750.00", "250.00", "20.00"), flagged: 0, categories },
      { class: "liability", figures: money("-400.00", "-400.00", "0.00"), flagged: 0, categories: [] },
    ],
    net: money("600.00", "350.00", "250.00", "20.00"),
    flagged: 0,
  } as Position;
};

const prefs: Record<string, string> = {};
const puts: Record<string, string | null>[] = [];

function stub(body: Position = position(), tagged?: (tag: string | null) => Position) {
  const urls: string[] = [];
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
      if (url.pathname === "/api/position/history") {
        historyUrls.push(url.pathname + url.search);
        return json(history());
      }
      if (url.pathname !== "/api/position") return json({}, 404); // shell status calls
      urls.push(url.pathname + url.search);
      return json(tagged ? tagged(url.searchParams.get("tag")) : body);
    }),
  );
  return urls;
}
const historyUrls: string[] = [];

/** Renders the routed app at /position (real router, so search params and links work). */
async function renderPage(path = "/position") {
  return renderAppAt(path);
}

/** The composition card with its chart mounted (the preferences are read, the lazy chart loaded);
 * returns the legend. */
async function chartReady() {
  const chart = await screen.findByTestId("share-chart");
  await within(chart).findByTestId("mock-echart");
  return screen.getByTestId("share-legend");
}

/** Opens the Position's figures (the tree or table), which sit behind "Show all accounts". */
async function openFigures() {
  const section = await screen.findByRole("region", { name: "Position figures" });
  fireEvent.click(within(section).getByRole("button", { name: "Show all accounts" }));
}

/** The "Change view" row opens to As at, Tag, View and Layout. */
async function openChangeView() {
  fireEvent.click(await screen.findByRole("button", { name: "Change view" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  for (const k of Object.keys(prefs)) delete prefs[k];
  puts.length = 0;
  historyUrls.length = 0;
  options.length = 0;
});

test("money is formatted in one place, from the exact string, never via a number", () => {
  expect(formatMoney("1294742.56")).toBe("1,294,742.56");
  expect(formatMoney("-497000.00")).toBe("-497,000.00");
  expect(formatMoney("12345678901234567.89")).toBe("12,345,678,901,234,567.89"); // beyond float
  expect(formatMoney(null)).toBe("—"); // not valued: a dash, never 0.00
  expect(formatMoney("5.00", "GBP")).toBe("GBP 5.00");
});

test("the first screen leads with the stat tile and the composition card, with no table on it", async () => {
  const urls = stub(rich());
  await renderPage();
  const tile = await screen.findByRole("region", { name: "Net position" });
  expect(urls).toEqual(["/api/position"]); // no date: the server uses the Household's today
  expect(within(tile).getByTestId("stat-hero")).toHaveTextContent("600.00");
  expect(within(tile).getByText("Assets").nextSibling).toHaveTextContent("1,000.00");
  expect(within(tile).getByText("Liabilities").nextSibling).toHaveTextContent("-400.00");
  expect(tile).toHaveTextContent("Net position is 600.00; Investments is the largest share of assets.");
  expect(await screen.findByRole("heading", { name: "What it is made of" })).toBeInTheDocument();
  // no tree and no table until the figures are asked for
  expect(screen.queryByTestId("position-tree")).not.toBeInTheDocument();
  expect(screen.queryByTestId("position-table")).not.toBeInTheDocument();
});

test("liabilities are in the stat tile, never a slice of the composition", async () => {
  stub(rich());
  await renderPage();
  const legend = await chartReady();
  expect(within(legend).queryByText(/Liabilit/)).not.toBeInTheDocument();
  const option = optionFor(/Donut chart/);
  expect(option.series[0].type).toBe("pie");
  expect(option.series[0].data.map((d: { name: string }) => d.name)).toEqual(["Investments", "Property", "Cash"]);
  expect(option.series[0].data.reduce((s: number, d: { value: number }) => s + d.value, 0)).toBeLessThanOrEqual(1_000_000);
});

test("the legend names every Category with its percent and exact Money value beside the chart", async () => {
  stub(rich());
  await renderPage();
  const legend = await chartReady();
  const items = within(legend).getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("Investments");
  expect(items[0]).toHaveTextContent("60%");
  expect(items[0]).toHaveTextContent("600.00");
  expect(items[1]).toHaveTextContent("Property");
  expect(items[1]).toHaveTextContent("30%");
  expect(items[2]).toHaveTextContent("Cash");
  expect(items[2]).toHaveTextContent("10%");
  expect(screen.getByRole("heading", { name: "What it is made of" }).closest("section")).toHaveTextContent(
    "Investments is the largest share, 60%; Property is 30%.",
  );
});

test("a Category keeps its colour whatever else is shown, and Cash is teal, never red", async () => {
  const urls = stub(rich(), (tag) =>
    tag === "everyday"
      ? { ...rich(), classes: rich().classes.map((c) => ({ ...c, categories: c.categories.filter((k) => k.key === "cash") })) }
      : rich(),
  );
  await renderPage();
  await chartReady();
  const colourOfCash = () => {
    const o = optionFor(/Donut chart/);
    return o.series[0].data.find((d: { name: string }) => d.name === "Cash").itemStyle.color;
  };
  const before = colourOfCash();
  await openChangeView();
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "everyday" } });
  await waitFor(() => expect(urls).toContain("/api/position?tag=everyday"));
  await waitFor(() => expect(screen.getByTestId("share-legend")).not.toHaveTextContent("Investments"));
  expect(colourOfCash()).toBe(before);
  // fixed table: Cash is the third token, a teal in the new palette (no red in the categorical set)
  expect(before).toBe("#04a19b");
});

test("more than six Categories fold into Other for the picture; the legend keeps every one", async () => {
  const extra = ["Boat", "Art", "Wine", "Coins"].map((name, i) =>
    category(`user-${i}`, name, money(`${10 - i}.00`, `${10 - i}.00`, "0.00")),
  );
  const many = rich([
    category("superannuation", "Superannuation", money("80.00", "80.00", "0.00")),
    category("overseas_pensions", "Overseas pensions", money("50.00", "50.00", "0.00")),
    ...extra,
  ]);
  stub(many);
  await renderPage();
  const legend = await chartReady();
  const names = (o: { name: string }) => o.name;
  const drawn = optionFor(/Donut chart/).series[0].data.map(names);
  expect(drawn).toHaveLength(6);
  expect(drawn.at(-1)).toBe("Other");
  // every Category is still named in the legend, the folded ones under Other
  for (const name of ["Investments", "Property", "Cash", "Superannuation", "Overseas pensions", "Boat", "Art", "Wine", "Coins", "Other"]) {
    expect(within(legend).getByText(name)).toBeInTheDocument();
  }
});

test("the switch changes the chart, saves the view's choice only, and offers Use as my default", async () => {
  stub(rich());
  await renderPage();
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "donut");
  expect(screen.queryByRole("button", { name: "Use as my default" })).not.toBeInTheDocument(); // donut is the default

  fireEvent.click(screen.getByRole("radio", { name: "Bars" }));
  await waitFor(() => expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "bars"));
  expect(optionFor(/Bar chart/).series[0].type).toBe("bar");
  await waitFor(() => expect(puts).toEqual([{ "position.composition": "bars" }])); // not default.share
  expect(prefs).toEqual({ "position.composition": "bars" });

  fireEvent.click(screen.getByRole("radio", { name: "Stacked bar" }));
  await waitFor(() => expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "stacked-bar"));
  const stacked = optionFor(/Stacked bar chart/);
  expect(stacked.series.map((s: { name: string }) => s.name)).toEqual(["Investments", "Property", "Cash"]);
  expect(stacked.series.every((s: { stack: string }) => s.stack === "share")).toBe(true);
});

test("Use as my default stores the shape's default, and a view with no choice of its own follows it", async () => {
  stub(rich());
  await renderPage();
  await chartReady();
  fireEvent.click(screen.getByRole("radio", { name: "Bars" }));
  fireEvent.click(await screen.findByRole("button", { name: "Use as my default" }));
  await waitFor(() => expect(puts.at(-1)).toEqual({ "default.share": "bars" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Use as my default" })).not.toBeInTheDocument());

  // Drop the view's own choice: the stored default applies where there is no area choice.
  delete prefs["position.composition"];
  cleanup();
  stub(rich());
  const { QueryClient } = await import("@tanstack/react-query");
  await renderAppAt("/position", new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "bars");
  expect(screen.getByRole("radio", { name: "Bars" })).toBeChecked();
});

test("a stored choice reloads as set, and one the view no longer offers is ignored", async () => {
  prefs["position.composition"] = "stacked-bar";
  stub(rich());
  await renderPage();
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "stacked-bar");

  cleanup();
  prefs["position.composition"] = "hexagon";
  stub(rich());
  await renderPage();
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "donut");
});

test("if the preferences cannot be read the chart still draws in the kit default and the switch works", async () => {
  stub(rich());
  const inner = globalThis.fetch as unknown as (r: Request) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (new URL(request.url).pathname === "/api/preferences") return json({ errors: [] }, 500);
    return inner(request);
  }));
  await renderPage();
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "donut");
  fireEvent.click(screen.getByRole("radio", { name: "Bars" }));
  await waitFor(() => expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "bars"));
});

test("a negative part draws bars only and hides the switch", async () => {
  const owing = rich([category("vehicles", "Vehicles", money("-50.00", "-50.00", "0.00"))]);
  stub(owing);
  await renderPage();
  await chartReady();
  expect(screen.getByTestId("share-chart")).toHaveAttribute("data-form", "bars");
  expect(screen.queryByRole("radio", { name: "Donut" })).not.toBeInTheDocument();
});

test("the composition can be split by Member instead of Category", async () => {
  stub(rich());
  await renderPage();
  await chartReady();
  fireEvent.click(screen.getByRole("radio", { name: "Member" }));
  const legend = await chartReady();
  await waitFor(() => expect(legend).toHaveTextContent("Alex Sample"));
  expect(legend).toHaveTextContent("750.00");
  expect(legend).toHaveTextContent("75%");
  expect(legend).toHaveTextContent("Jordan Sample");
  expect(legend).toHaveTextContent("25%");
  expect(legend).not.toHaveTextContent("Investments");
});

test("As at, Tag, View and Layout are collapsed into one Change view row that opens on demand", async () => {
  stub();
  await renderPage();
  const row = await screen.findByTestId("change-view");
  expect(within(row).getByText(/As at 2026-10-17 · All Accounts · Household · Tree/)).toBeInTheDocument();
  expect(screen.queryByLabelText("As at")).not.toBeInTheDocument();
  await openChangeView();
  expect(screen.getByLabelText("As at")).toHaveValue("2026-10-17");
  expect(screen.getByLabelText("Tag")).toBeInTheDocument();
});

test("the tree is behind Show all accounts; only the first level (Class, Category) is open", async () => {
  stub();
  await renderPage();
  await openFigures();
  const cash = await screen.findByRole("button", { name: "Cash" });
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
  await openFigures();
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
  await openFigures();
  fireEvent.click(await screen.findByRole("button", { name: "Cash" }));
  const link = screen.getByRole("link", { name: "Joint savings" });
  expect(link).toHaveAttribute("href", "/accounts/2");
  fireEvent.click(link);
  await waitFor(() => expect(router.state.location.pathname).toBe("/accounts/2"));
});

test("the stat tile shows Assets, Liabilities, Net and the not-counted note", async () => {
  stub();
  await renderPage();
  const tile = await screen.findByRole("region", { name: "Net position" });
  expect(tile).toHaveTextContent("Assets");
  expect(tile).toHaveTextContent("200.02");
  expect(tile).toHaveTextContent("Liabilities");
  expect(within(tile).getByRole("status")).toHaveTextContent("1 not counted");
  expect(within(tile).getByText(/1 Account is not in these totals/)).toHaveTextContent(
    "1 Account is not in these totals: no Valuation or FX rate on or before 2026-10-17.",
  );
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
  const tile = await screen.findByRole("region", { name: "Net position" });
  const liabilities = within(tile).getByText("-400.00");
  expect(liabilities).toHaveClass("text-money-negative");
  expect(liabilities).toHaveAttribute("data-negative", "true");
  const positive = within(tile).getAllByText("200.02")[0];
  expect(positive).not.toHaveClass("text-money-negative");
});

test("an Account with no FX rate is flagged with a badge and words, shown as a dash, not zeroed", async () => {
  stub();
  await renderPage();
  await openFigures();
  fireEvent.click(await screen.findByRole("button", { name: "Overseas pensions" }));
  expect(await screen.findByText("No FX rate for GBP on or before 2026-10-17")).toBeInTheDocument();
  const row = screen.getByText("UK Pension").closest("li")!;
  expect(within(row).getByText("—")).toBeInTheDocument();
  expect(within(row).getByText("Not counted")).toBeInTheDocument();
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
  await openFigures();
  fireEvent.click(await screen.findByRole("button", { name: "Overseas pensions" }));
  expect(await screen.findByText(/GBP 500.00 @ 2.0399836801 \(rate of 2026-10-19, rba\)/)).toBeInTheDocument();
});

test("the By Member view splits joint Accounts by share and Joint shows them whole", async () => {
  stub();
  await renderPage();
  await openChangeView();
  fireEvent.click(await screen.findByLabelText("By Member"));
  await openFigures();
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

test("the figures show that Members (held solely) plus Joint equal the Household", async () => {
  stub();
  await renderPage();
  await openFigures();
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
  await openChangeView();
  expect(screen.getByLabelText("As at")).toHaveValue("2025-10-17");
  expect(screen.getByLabelText("By Member")).toBeChecked();
  expect(screen.getByLabelText("Table")).toBeChecked();
  // the Table layout opens the figures by itself
  expect(await screen.findByTestId("position-table")).toBeInTheDocument();

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

test("Over time is open by default: the chart and its headline lead, the table is behind Show figures", async () => {
  stub();
  await renderPage();
  const card = (await screen.findByRole("heading", { name: "Over time" })).closest("section")!;
  await waitFor(() => expect(historyUrls).toEqual(["/api/position/history"]));
  expect(await within(card).findByTestId("mock-echart")).toHaveAccessibleName(/Assets, Liabilities and Net over time/);
  expect(card).toHaveTextContent("Net position moved from 600.01 on 2026-07-31 to 810.01 on 2026-09-30.");
  expect(screen.getByText(/Hollow markers are incomplete/)).toBeInTheDocument();
  expect(within(card).queryByRole("region", { name: "Net position history table" })).not.toBeInTheDocument();

  fireEvent.click(within(card).getByRole("button", { name: "Show figures" }));
  const table = await within(card).findByRole("region", { name: "Net position history table" });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(4); // header + three month-ends
  expect(rows[1]).toHaveTextContent("2026-07-31");
  expect(rows[1]).toHaveTextContent("600.01"); // Net, as the exact string
  expect(rows[1]).toHaveTextContent("-400.00");
  expect(rows[2]).toHaveTextContent("1 (incomplete)");
  expect(rows[2]).toHaveAttribute("data-flagged", "true");

  // The (mocked) chart gets an option built from the same points.
  const option = optionFor(/Assets, Liabilities and Net over time/);
  expect(option.xAxis.data).toEqual(["2026-07-31", "2026-08-31", "2026-09-30"]);
  expect(option.series.map((s: { name: string }) => s.name)).toEqual(["Assets", "Liabilities", "Net"]);
  expect(option.series[2].data[1].incomplete).toBe(true);
  // The lines carry the history's own values (Net, then Liabilities), as numbers for geometry only.
  expect(option.series[2].data.map((d: { value: number }) => d.value)).toEqual([600.01, 1100.01, 810.01]);
  expect(option.series[1].data[0].value).toBe(-400);
  // Net is the bold line and Liabilities keeps its dash.
  expect(option.series[2].lineStyle.width).toBeGreaterThan(option.series[0].lineStyle.width);
  expect(option.series[1].lineStyle.type).toBe("dashed");
});

test("the cards follow the Tag filter and the view", async () => {
  stub();
  await renderPage();
  await waitFor(() => expect(historyUrls).toEqual(["/api/position/history"]));
  await openChangeView();
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "retirement" } });
  await waitFor(() => expect(historyUrls).toContain("/api/position/history?tag=retirement"));

  fireEvent.click(screen.getByLabelText("Joint"));
  const card = screen.getByRole("heading", { name: "Over time" }).closest("section")!;
  await waitFor(() => expect(card).toHaveTextContent("to 12.00 on 2026-09-30"));
  fireEvent.click(screen.getByLabelText("By Member"));
  fireEvent.click(within(card).getByRole("button", { name: "Show figures" }));
  const table = await within(card).findByRole("region", { name: "Net position history table" });
  expect(await within(table).findByRole("columnheader", { name: "Alex Sample Net" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Household Net" })).toBeInTheDocument();
});

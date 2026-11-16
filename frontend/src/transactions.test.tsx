import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import { everyday, fakeBackend as accountsBackend, json } from "@/test-accounts-backend";

const PI = { ...everyday, id: 20, name: "Sample Brokerage account" };

const base = {
  instrument_id: null,
  instrument_name: null,
  quantity: null,
  unit_price: null,
  unit_price_ccy: null,
  fee: null,
  amount: null,
  import_id: 4,
  file_name: "sample_pi_cash.csv",
  row_no: 1,
  superseded_at: null,
};
const CASH = [
  {
    ...base,
    id: 3,
    stream: "cash",
    occurred_on: "2026-09-30",
    kind: "trade_settlement",
    instrument_name: "Sample Broad Market ETF",
    amount: { amount: "-1922.15", amount_minor: -192215, currency: "AUD" },
  },
  {
    ...base,
    id: 2,
    stream: "cash",
    occurred_on: "2026-09-10",
    kind: "distribution",
    amount: { amount: "86.40", amount_minor: 8640, currency: "AUD" },
    superseded_at: "2026-10-20T03:00:00Z",
  },
];
const TRADE = [
  {
    ...base,
    id: 1,
    stream: "trade",
    occurred_on: "2026-09-30",
    kind: "buy",
    instrument_name: "Sample Broad Market ETF",
    quantity: "20",
    unit_price: "96.1075",
    unit_price_ccy: "AUD",
    fee: { amount: "9.50", amount_minor: 950, currency: "AUD" },
    file_name: "sample_pi_trades.csv",
  },
];

/** The Accounts backend, with the Transactions endpoint added in front of it. */
function backend(world: { trade: typeof TRADE; cash: typeof CASH; total?: number }) {
  const seen = accountsBackend([PI]);
  const inner = globalThis.fetch;
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname !== "/api/accounts/20/transactions") return inner(request);
      calls.push(url);
      const stream = url.searchParams.get("stream") === "cash" ? world.cash : world.trade;
      const shown = url.searchParams.get("include_superseded") === "true" ? stream : stream.filter((t) => !t.superseded_at);
      return json({
        total: world.total ?? shown.length,
        items: shown,
        current_trade: world.trade.length,
        current_cash: world.cash.filter((t) => !t.superseded_at).length,
      });
    }),
  );
  return { seen, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the Transactions section lists one Stream at a time, trades first, with units, price and an informational fee", async () => {
  backend({ trade: TRADE, cash: CASH });
  await renderAppAt("/accounts/20");
  const section = within(await screen.findByLabelText("Transactions"));
  const table = await section.findByRole("table", { name: "Trade Transactions" });
  expect(within(table).getByText("Sample Broad Market ETF")).toBeInTheDocument();
  expect(within(table).getAllByText("96.1075").length).toBeGreaterThan(0);
  expect(within(table).getAllByText("9.50").length).toBeGreaterThan(0);
  expect(within(table).getByText("sample_pi_trades.csv")).toBeInTheDocument();
  expect(section.getByRole("button", { name: "Trades (1)" })).toHaveAttribute("aria-pressed", "true");
  expect(section.getByRole("button", { name: "Cash (1)" })).toBeInTheDocument();
});

test("the Stream is a URL search param: Cash opens the cash rows with signed amounts", async () => {
  const { calls } = backend({ trade: TRADE, cash: CASH });
  const { router } = await renderAppAt("/accounts/20");
  fireEvent.click(await screen.findByRole("button", { name: "Cash (1)" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ stream: "cash" }));
  const table = await screen.findByRole("table", { name: "Cash Transactions" });
  expect(within(table).getByText("-1,922.15")).toBeInTheDocument();
  expect(calls.some((u) => u.searchParams.get("stream") === "cash")).toBe(true);
});

test("a deep link to the cash Stream works, and superseded rows are hidden until asked for", async () => {
  backend({ trade: TRADE, cash: CASH });
  const { router } = await renderAppAt("/accounts/20?stream=cash");
  const table = await screen.findByRole("table", { name: "Cash Transactions" });
  expect(within(table).queryByText("86.40")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Show superseded rows"));
  await waitFor(() => expect(router.state.location.search).toEqual({ stream: "cash", superseded: true }));
  const shown = await screen.findByText("86.40");
  expect(shown).toBeInTheDocument();
  expect(screen.getAllByText("Superseded").length).toBeGreaterThan(0);
});

test("an Account with no Transactions shows no Transactions section", async () => {
  backend({ trade: [], cash: [] });
  await renderAppAt("/accounts/20");
  await screen.findByRole("heading", { name: "Sample Brokerage account" });
  await waitFor(() => expect(screen.queryByLabelText("Transactions")).not.toBeInTheDocument());
});

test("long lists paginate with the page in the URL", async () => {
  const { calls } = backend({ trade: TRADE, cash: CASH, total: 25 });
  const { router } = await renderAppAt("/accounts/20");
  expect(await screen.findByText("25 Transactions · page 1 of 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ txpage: 2 }));
  expect(await screen.findByText("25 Transactions · page 2 of 3")).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.get("offset")).toBe("10");
});

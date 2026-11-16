import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import {
  everyday,
  fakeBackend as accountsBackend,
  json,
} from "@/test-accounts-backend";

const PI = {
  ...everyday,
  id: 20,
  name: "Sample Brokerage account",
  category_key: "investments",
};
const aud = (amount: string) => ({
  amount,
  amount_minor: Math.round(Number(amount) * 100),
  currency: "AUD",
});
const OK_STREAMS = [
  { stream: "trade", coverage_to: "2026-09-30", status: "ok", message: null },
  { stream: "cash", coverage_to: "2026-09-30", status: "ok", message: null },
];
const DERIVED = {
  applies: true,
  as_at: "2026-09-30",
  total: aud("5810.31"),
  reason: null,
  stale: true,
  stale_after_days: 7,
  lines: [
    {
      kind: "holding",
      instrument_id: 1,
      instrument_name: "Sample Broad Market ETF",
      currency: "AUD",
      units: "50",
      price: "96.1075",
      price_as_at: "2026-09-30",
      price_source: "yfinance",
      stale: false,
      rate: null,
      rate_as_at: null,
      rate_source: null,
      value: aud("4805.38"),
    },
    {
      kind: "holding",
      instrument_id: 3,
      instrument_name: "Sample Unlisted Fund",
      currency: "GBP",
      units: "10",
      price: "1.9340",
      price_as_at: "2026-09-10",
      price_source: "manual",
      stale: true,
      rate: "2.0143",
      rate_as_at: "2026-08-31",
      rate_source: "rba",
      value: aud("38.95"),
    },
    {
      kind: "cash",
      instrument_id: null,
      instrument_name: null,
      currency: "AUD",
      units: null,
      price: null,
      price_as_at: null,
      price_source: null,
      stale: false,
      rate: null,
      rate_as_at: null,
      rate_source: null,
      value: aud("966.00"),
    },
  ],
  streams: OK_STREAMS,
  observed: {
    as_at: "2026-09-04",
    source: "manual",
    amount: aud("59689.22"),
  },
  difference: aud("53878.91"),
};
const NONE = {
  ...DERIVED,
  total: null,
  reason: "The cash Stream has no committed Import.",
  stale: false,
  lines: [],
  observed: null,
  difference: null,
  streams: [
    OK_STREAMS[0],
    { stream: "cash", coverage_to: null, status: "missing", message: "x" },
  ],
};
const NOT_APPLICABLE = {
  ...NONE,
  applies: false,
  reason: null,
  streams: [],
};

/** The Accounts backend plus the derived-value, Holdings and Transactions endpoints. */
function backend(derived: unknown) {
  accountsBackend([PI]);
  const inner = globalThis.fetch;
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/accounts/20/derived-value") {
        calls.push(url);
        return json(derived);
      }
      if (url.pathname === "/api/accounts/20/holdings")
        return json({ as_at: null, holdings: [], cash: [], invalid: [] });
      if (url.pathname === "/api/accounts/20/transactions")
        return json({ total: 0, items: [], current_trade: 0, current_cash: 0 });
      return inner(request);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the derived value shows its lines, inputs, stale flags and reconciliation", async () => {
  const calls = backend(DERIVED);
  await renderAppAt("/accounts/20");
  const panel = within(
    await screen.findByRole("region", { name: "Derived value" }),
  );
  const table = await panel.findByRole("table", {
    name: "Derived value lines",
  });
  expect(panel.getByText("5,810.31")).toBeInTheDocument();
  expect(panel.getByText(/Stale Price/)).toBeInTheDocument();
  expect(
    within(table).getByText("Sample Broad Market ETF"),
  ).toBeInTheDocument();
  expect(within(table).getByText(/96\.1075 AUD/)).toBeInTheDocument();
  expect(
    within(table).getByText(/as at 2026-08-31 \(rba\)/),
  ).toBeInTheDocument();
  expect(within(table).getByText("2.0143")).toBeInTheDocument();
  expect(within(table).getByText("Cash (AUD)")).toBeInTheDocument();
  // only the line whose Price is old carries the Stale badge
  expect(within(table).getAllByText("Stale")).toHaveLength(1);
  expect(panel.getByLabelText("Reconciliation")).toHaveTextContent(
    "Recorded manual Valuation as at 2026-09-04: 59,689.22. Difference (recorded minus derived): 53,878.91",
  );
  expect(panel.getByText("trade Stream")).toBeInTheDocument();
  expect(calls[0].searchParams.has("as_at")).toBe(false);
});

test("with a Stream missing there is no value, and the page says which", async () => {
  backend(NONE);
  await renderAppAt("/accounts/20?asat=2026-09-30");
  const panel = within(
    await screen.findByRole("region", { name: "Derived value" }),
  );
  expect(
    await panel.findByText(/No derived value as at 2026-09-30/),
  ).toHaveTextContent("The cash Stream has no committed Import.");
  expect(panel.getByText("Missing: nothing imported")).toBeInTheDocument();
  expect(panel.queryByRole("table")).not.toBeInTheDocument();
});

test("an Account whose Behaviour does not derive shows no panel", async () => {
  backend(NOT_APPLICABLE);
  await renderAppAt("/accounts/20");
  await screen.findByRole("region", { name: "Holdings" });
  expect(
    screen.queryByRole("region", { name: "Derived value" }),
  ).not.toBeInTheDocument();
});

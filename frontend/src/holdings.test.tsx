import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
const HELD = {
  as_at: null,
  holdings: [
    {
      instrument_id: 1,
      instrument_name: "Sample Broad Market ETF",
      units: "50",
    },
  ],
  cash: [{ amount: "834.93", amount_minor: 83493, currency: "AUD" }],
  invalid: [],
};
const INVALID = {
  as_at: null,
  holdings: [],
  cash: [],
  invalid: [
    {
      instrument_id: 1,
      instrument_name: "Sample Broad Market ETF",
      currency: null,
      on: "2026-09-16",
      message:
        "Sample Broad Market ETF went negative on 2026-09-16: possible missing Opening position.",
    },
  ],
};

/** The Accounts backend with the Holdings, Opening-position, Instruments and Transactions endpoints. */
function backend(held: typeof HELD | typeof INVALID) {
  accountsBackend([PI]);
  const inner = globalThis.fetch;
  const holdingCalls: URL[] = [];
  const posted: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/accounts/20/holdings") {
        holdingCalls.push(url);
        const earlier = url.searchParams.get("as_at") === "2026-08-20";
        return json(
          earlier
            ? {
                ...HELD,
                as_at: "2026-08-20",
                holdings: [{ ...HELD.holdings[0], units: "30" }],
              }
            : held,
        );
      }
      if (url.pathname === "/api/accounts/20/opening-positions") {
        posted.push(await request.json());
        return json({}, 201);
      }
      if (url.pathname === "/api/instruments")
        return json([
          {
            id: 1,
            name: "Sample Broad Market ETF",
            kind: "etf",
            currency: "AUD",
            notes: null,
            identifiers: [],
          },
        ]);
      if (url.pathname === "/api/accounts/20/transactions")
        return json({ total: 0, items: [], current_trade: 0, current_cash: 0 });
      return inner(request);
    }),
  );
  return { holdingCalls, posted };
}

afterEach(() => vi.unstubAllGlobals());

test("Holdings list units and cash, and the as-at date lives in the URL", async () => {
  const { holdingCalls } = backend(HELD);
  const { router } = await renderAppAt("/accounts/20");
  const section = within(
    await screen.findByRole("region", { name: "Holdings" }),
  );
  const table = await section.findByRole("table", { name: "Holdings table" });
  expect(
    within(table).getByText("Sample Broad Market ETF"),
  ).toBeInTheDocument();
  expect(within(table).getByText("50")).toBeInTheDocument();
  expect(within(table).getByText("834.93")).toBeInTheDocument();

  fireEvent.change(section.getByLabelText("As at"), {
    target: { value: "2026-08-20" },
  });
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ asat: "2026-08-20" }),
  );
  expect(await section.findByText("30")).toBeInTheDocument();
  expect(holdingCalls.at(-1)?.searchParams.get("as_at")).toBe("2026-08-20");
});

test("a deep link with an as-at date shows that date", async () => {
  const { holdingCalls } = backend(HELD);
  await renderAppAt("/accounts/20?asat=2026-08-20");
  expect(await screen.findByText("30")).toBeInTheDocument();
  expect(holdingCalls.at(-1)?.searchParams.get("as_at")).toBe("2026-08-20");
});

test("a negative balance is explained, never shown as a value", async () => {
  backend(INVALID);
  await renderAppAt("/accounts/20");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(
    "went negative on 2026-09-16: possible missing Opening position",
  );
  expect(
    screen.queryByRole("table", { name: "Holdings table" }),
  ).not.toBeInTheDocument();
});

test("the Dialog records an Opening position of units", async () => {
  const { posted } = backend(HELD);
  await renderAppAt("/accounts/20");
  fireEvent.click(
    await screen.findByRole("button", { name: "Record Opening position" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Record Opening position for Sample Brokerage account",
    }),
  );
  fireEvent.change(form.getByLabelText("Date"), {
    target: { value: "2026-08-31" },
  });
  fireEvent.change(await form.findByLabelText("Instrument"), {
    target: { value: "1" },
  });
  fireEvent.change(form.getByLabelText("Units"), { target: { value: "50" } });
  fireEvent.click(
    form.getByRole("button", { name: "Record Opening position" }),
  );
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toEqual({
    kind: "holding",
    occurred_on: "2026-08-31",
    instrument_id: 1,
    quantity: "50",
    amount: "",
  });
  await waitFor(() =>
    expect(screen.queryByRole("form")).not.toBeInTheDocument(),
  );
});

test("an Account outside Investments has no Holdings section", async () => {
  accountsBackend([everyday]);
  await renderAppAt("/accounts/10");
  await screen.findByRole("heading", { name: "Everyday account" });
  expect(
    screen.queryByRole("region", { name: "Holdings" }),
  ).not.toBeInTheDocument();
});

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString().replace("Z", "");

type Run = Record<string, unknown>;
const run = (over: Run): Run => ({
  id: 1,
  dataset: "prices",
  provider: "yfinance",
  trigger: "scheduled",
  started_at: hoursAgo(2),
  finished_at: hoursAgo(2),
  status: "success",
  added: 12,
  revised: 1,
  error: null,
  ...over,
});

const dataset = (over: Run) => ({
  dataset: "prices",
  label: "Prices",
  provider: "yfinance",
  configured: true,
  latest: null,
  last_success: null,
  last_error: null,
  ...over,
});

function backend(opts: { datasets: Run[]; runs?: Run[]; stale?: Run[]; sample?: boolean; detail?: Run }) {
  const calls: { method: string; path: string }[] = [];
  let pressed = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      calls.push({ method: request.method, path: url.pathname });
      if (url.pathname === "/api/fetch/status")
        return json({
          // after "Fetch now" the first dataset's latest run is live, as the server would say
          datasets: pressed
            ? [{ ...opts.datasets[0], latest: run({ status: "running", finished_at: null }) }, ...opts.datasets.slice(1)]
            : opts.datasets,
          fetch_time: "18:30",
          sample_data: opts.sample ?? false,
          stale_after_days: 7,
          stale_instruments: opts.stale ?? [],
        });
      if (url.pathname === "/api/fetch/runs") return json(opts.runs ?? []);
      if (url.pathname.startsWith("/api/fetch/runs/")) return json(opts.detail ?? {});
      if (url.pathname === "/api/fetch/run" && request.method === "POST") {
        pressed = true;
        return json([run({ status: "running", finished_at: null })], 202);
      }
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      return json({ message: `unexpected ${url.pathname}` }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const ok = run({ id: 7 });
const failed = run({
  id: 8,
  status: "failed",
  added: 0,
  revised: 0,
  error: "1 of 1 Instruments failed. Sample Broad Market ETF: Yahoo Finance is rate limiting requests.",
});

test("shows the last success and the last error for each dataset", async () => {
  backend({
    datasets: [
      dataset({ latest: failed, last_success: ok, last_error: failed }),
      dataset({ dataset: "fx", label: "FX rates", provider: "rba", latest: run({ id: 9, dataset: "fx", added: 3, revised: 0 }), last_success: run({ id: 9, dataset: "fx", added: 3, revised: 0 }) }),
    ],
    runs: [failed, ok],
  });
  await renderAppAt("/reference-data");

  const prices = within(await screen.findByLabelText("Prices"));
  expect(prices.getByText("12 new Prices, 1 revised")).toBeInTheDocument();
  expect(prices.getByRole("status")).toHaveTextContent("Yahoo Finance is rate limiting requests.");
  expect(prices.getByText("Fetched daily at 18:30 (Household time) from yfinance.")).toBeInTheDocument();
  const fx = within(screen.getByLabelText("FX rates"));
  expect(fx.getByText("3 new FX rates")).toBeInTheDocument();
  expect(fx.getByText("None yet")).toBeInTheDocument(); // no error recorded for FX
});

test("an unconfigured dataset says how to set it up, and Fetch now is disabled", async () => {
  backend({
    datasets: [
      dataset({ configured: false, provider: null }),
      dataset({ dataset: "fx", label: "FX rates", configured: false, provider: null }),
    ],
  });
  await renderAppAt("/reference-data");
  expect(await screen.findByText(/set FINANCE360_PRICE_PROVIDER to fetch Prices/)).toBeInTheDocument();
  expect(screen.getByText(/set FINANCE360_FX_PROVIDER to fetch FX rates/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Fetch now" })).toBeDisabled();
  expect(screen.getByText(/Nothing has been fetched yet/)).toBeInTheDocument();
});

test("Fetch now starts a run and the button shows it is working", async () => {
  const calls = backend({ datasets: [dataset({ latest: ok, last_success: ok })], runs: [ok] });
  await renderAppAt("/reference-data");
  const button = await screen.findByRole("button", { name: "Fetch now" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/fetch/run" }));
  await screen.findByRole("button", { name: "Fetching…" });
});

test("stale Instruments are listed with links to them; sample data is labelled", async () => {
  backend({
    datasets: [dataset({ latest: ok, last_success: ok })],
    runs: [ok],
    sample: true,
    stale: [
      { instrument_id: 3, name: "Sample Mining Ltd", latest_as_at: "2026-09-30" },
      { instrument_id: 4, name: "Sample Balanced Growth Fund", latest_as_at: null },
    ],
  });
  await renderAppAt("/reference-data");
  expect(await screen.findByText("2 Instruments have no recent Price")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sample Mining Ltd" })).toHaveAttribute("href", "/instruments/3");
  expect(screen.getByText("Last Price 2026-09-30")).toBeInTheDocument();
  expect(screen.getByText("No Price yet")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("Sample data");
});

test("a run row opens its detail page, which lists each Instrument's result and error", async () => {
  backend({
    datasets: [dataset({ latest: failed, last_success: ok, last_error: failed })],
    runs: [failed, ok],
    detail: {
      ...failed,
      results: [
        { target: "Sample Broad Market ETF", symbol: "SBME.AX", outcome: "error", added: 0, revised: 0, message: "Yahoo Finance is rate limiting requests." },
        { target: "Sample Mining Ltd", symbol: "SMPM.AX", outcome: "ok", added: 4, revised: 1, message: null },
      ],
    },
  });
  const { router } = await renderAppAt("/reference-data");
  fireEvent.click(await screen.findByRole("row", { name: "Prices run 8" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/reference-data/runs/8"));
  const table = within(await screen.findByRole("table", { name: "Results" }));
  expect(table.getByText("Sample Broad Market ETF")).toBeInTheDocument();
  expect(table.getByText("Yahoo Finance is rate limiting requests.")).toBeInTheDocument();
  expect(table.getByText("Error")).toBeInTheDocument();
  expect(table.getByText("Fetched")).toBeInTheDocument();
  const content = within(screen.getByTestId("content"));
  expect(content.getByRole("link", { name: /Reference data/ })).toHaveAttribute("href", "/reference-data");
});

test("an unknown run says so at once: a 404 is not retried", async () => {
  const calls = backend({ datasets: [dataset({})] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push({ method: request.method, path: new URL(request.url).pathname });
      return json({ message: "Fetch run 999 does not exist." }, 404);
    }),
  );
  // The app's real QueryClient defaults (which retry), not the test helper's retry-free client.
  await renderAppAt("/reference-data/runs/999", new QueryClient());
  expect(await screen.findByText(/Fetch run 999 does not exist/)).toBeInTheDocument();
  expect(calls.filter((c) => c.path === "/api/fetch/runs/999")).toHaveLength(1);
});

test("Reference data is in the sidebar", async () => {
  backend({ datasets: [dataset({})] });
  await renderAppAt("/reference-data");
  const nav = within(await screen.findByRole("navigation", { name: "Sections" }));
  expect(nav.getByRole("link", { name: "Reference data" })).toHaveAttribute("aria-current", "page");
});

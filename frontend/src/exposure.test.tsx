import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { judgeTotal, percentToPpm, ppmToPercent } from "@/lib/exposure";
import { everyday, fakeBackend as accountsBackend, json } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());

const aud = (amount: string, minor: number) => ({ amount, amount_minor: minor, currency: "AUD" });
const PI = { ...everyday, id: 20, name: "Sample Brokerage account", category_key: "investments" };

const LOOK = {
  applies: true,
  as_at: "2026-09-30",
  status: "ok",
  reason: null,
  dimension_key: "asset_class",
  dimension_name: "Asset class",
  total: aud("6645.13", 664513),
  lines: [
    { kind: "label", key: "australian_shares", name: "Australian shares", amount: aud("3263.05", 326305), percent: "49.10", instrument_id: null },
    { kind: "label", key: "international_shares", name: "International shares", amount: aud("1898.12", 189812), percent: "28.56", instrument_id: null },
    { kind: "label", key: "cash", name: "Cash", amount: aud("834.93", 83493), percent: "12.56", instrument_id: null },
    { kind: "no_profile", key: null, name: "No profile: Sample New Horizons ETF", amount: aud("625.00", 62500), percent: "9.41", instrument_id: 3 },
    { kind: "not_classified", key: null, name: "Not classified", amount: aud("24.03", 2403), percent: "0.36", instrument_id: null },
    { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null },
  ],
  parts: [
    { instrument_id: 1, name: "Sample Broad Market ETF", amount: aud("4805.38", 480538), profile_as_at: "2026-06-30", dated_after: false, shortfall_percent: "0.5", trimmed: [], omitted: false },
    { instrument_id: 3, name: "Sample New Horizons ETF", amount: aud("625.00", 62500), profile_as_at: "2026-12-31", dated_after: true, shortfall_percent: "0", trimmed: [], omitted: false },
  ],
  flagged: false,
  tolerance: aud("66.45", 6645),
};

const DIMENSIONS = [
  { key: "asset_class", name: "Asset class", labels: [{ key: "other", name: "Other" }] },
  { key: "region", name: "Region", labels: [{ key: "other", name: "Other" }] },
  { key: "sector", name: "Sector", labels: [{ key: "other", name: "Other" }] },
  { key: "exposure_currency", name: "Exposure currency", labels: [{ key: "usd", name: "USD" }] },
];

function accountBackend(look: unknown | ((dimension: string | null) => unknown)) {
  accountsBackend([PI]);
  const inner = globalThis.fetch;
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/accounts/20/look-through") {
        calls.push(url);
        return json(typeof look === "function" ? look(url.searchParams.get("dimension")) : look);
      }
      if (url.pathname === "/api/exposure/vocabulary") return json(DIMENSIONS);
      if (url.pathname === "/api/accounts/20/opening-positions") return json({}, 201);
      if (url.pathname === "/api/accounts/20/derived-value")
        return json({ applies: false, as_at: "2026-09-30", total: null, reason: null, stale: false, stale_after_days: 7, lines: [], streams: [], observed: null, difference: null });
      if (url.pathname === "/api/accounts/20/holdings") return json({ as_at: null, holdings: [], cash: [], invalid: [] });
      if (url.pathname === "/api/instruments") return json([]);
      if (url.pathname === "/api/accounts/20/transactions")
        return json({ total: 0, items: [], current_trade: 0, current_cash: 0 });
      return inner(request);
    }),
  );
  return calls;
}

test("the investment Account shows its asset-class split, with No profile linking to entry", async () => {
  const calls = accountBackend(LOOK);
  await renderAppAt("/accounts/20");
  const panel = within(await screen.findByRole("region", { name: "Look-through" }));
  expect(panel.getByRole("heading", { name: "Look-through by asset class" })).toBeInTheDocument();
  const table = within(await panel.findByRole("table", { name: "Look-through lines" }));
  expect(table.getByText("Australian shares")).toBeInTheDocument();
  expect(table.getByText("49.10%")).toBeInTheDocument();
  expect(table.getByText("3,263.05")).toBeInTheDocument();
  const noProfile = table.getByText(/No profile: Sample New Horizons ETF/).closest("tr")!;
  const link = within(noProfile).getByRole("link", { name: "Enter a profile" });
  expect(link).toHaveAttribute("href", "/instruments/3");
  expect(table.getByText("Not classified")).toBeInTheDocument();
  expect(table.getByText("Unallocated")).toBeInTheDocument();
  expect(panel.queryByText("Gap beyond tolerance")).not.toBeInTheDocument();
  // the notes name a shortfall and a profile dated after the date
  expect(panel.getByText(/Sample Broad Market ETF: its profile is 0.5 points short of 100%/)).toBeInTheDocument();
  expect(panel.getByText(/the profile is dated after 2026-09-30 \(2026-12-31\)/)).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.has("on")).toBe(false);
});

const PART_OMITTING = {
  instrument_id: 1,
  name: "Sample Balanced Growth Fund",
  amount: aud("500.00", 50000),
  profile_as_at: "2026-06-30",
  dated_after: false,
  shortfall_percent: "0",
  trimmed: [],
  omitted: true,
};

test("the Dimension switcher lists every Dimension, asks the server for the chosen one and keeps it in the URL", async () => {
  const calls = accountBackend((dimension: string | null) =>
    dimension === "sector"
      ? {
          ...LOOK,
          dimension_key: "sector",
          dimension_name: "Sector",
          lines: [
            { kind: "label", key: "materials", name: "Materials", amount: aud("379.82", 37982), percent: "5.72", instrument_id: null },
            { kind: "not_classified", key: null, name: "Not classified", amount: aud("6265.31", 626531), percent: "94.28", instrument_id: null },
            { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null },
          ],
          parts: [PART_OMITTING],
        }
      : LOOK,
  );
  const { router } = await renderAppAt("/accounts/20");
  const panel = within(await screen.findByRole("region", { name: "Look-through" }));
  const tabs = within(await panel.findByRole("tablist", { name: "Dimension" }));
  expect(tabs.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Asset class",
    "Region",
    "Sector",
    "Exposure currency",
  ]);
  expect(tabs.getByRole("tab", { name: "Asset class" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(tabs.getByRole("tab", { name: "Sector" }));
  expect(await panel.findByRole("heading", { name: "Look-through by sector" })).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.get("dimension")).toBe("sector");
  expect(router.state.location.search).toMatchObject({ dimension: "sector" });
  // a profile that leaves the Dimension out is said to, and its value is in Not classified
  expect(panel.getByText(/Sample Balanced Growth Fund: its profile gives no sector weights/)).toBeInTheDocument();
  expect(panel.getByText("94.28%")).toBeInTheDocument();
  expect(panel.getByText(/Not classified is value with no sector weights/)).toBeInTheDocument();
});

test("a Dimension in the URL is the one shown, and exposure currency is set apart from Holding currency", async () => {
  const calls = accountBackend({
    ...LOOK,
    dimension_key: "exposure_currency",
    dimension_name: "Exposure currency",
    lines: [
      { kind: "label", key: "usd", name: "USD", amount: aud("6645.13", 664513), percent: "100.00", instrument_id: null },
      { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null },
    ],
  });
  await renderAppAt("/accounts/20?dimension=exposure_currency");
  const panel = within(await screen.findByRole("region", { name: "Look-through" }));
  expect(await panel.findByRole("heading", { name: "Look-through by exposure currency" })).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.get("dimension")).toBe("exposure_currency");
  expect(panel.getByText("Holding currency AUD")).toBeInTheDocument();
  expect(panel.getByText(/the currencies the underlying assets are actually exposed to/)).toBeInTheDocument();
  expect(panel.getByRole("tab", { name: "Exposure currency" })).toHaveAttribute("aria-selected", "true");
});

test("recording an Opening position refreshes the look-through, which is built from the Holdings", async () => {
  const calls = accountBackend(LOOK);
  await renderAppAt("/accounts/20");
  await screen.findByRole("region", { name: "Look-through" });
  const before = calls.length;
  fireEvent.click(await screen.findByRole("button", { name: "Record Opening position" }));
  const form = within(await screen.findByRole("form", { name: "Record Opening position for Sample Brokerage account" }));
  fireEvent.change(form.getByLabelText("What it is"), { target: { value: "cash" } });
  fireEvent.change(form.getByLabelText("Date"), { target: { value: "2026-08-01" } });
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "100" } });
  fireEvent.click(form.getByRole("button", { name: "Record Opening position" }));
  await waitFor(() => expect(calls.length).toBeGreaterThan(before));
});

test("the as-at date in the URL reaches the look-through, and a gap beyond tolerance is flagged", async () => {
  const calls = accountBackend({ ...LOOK, flagged: true });
  await renderAppAt("/accounts/20?asat=2026-08-20");
  expect(await screen.findByText("Gap beyond tolerance")).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.get("on")).toBe("2026-08-20");
});

test("an Account with no value from Holdings says why", async () => {
  accountBackend({
    ...LOOK,
    status: "not_analysed",
    reason: "No value can be built from Holdings on 2026-09-30. The trade Stream has no committed Import.",
    total: null,
    lines: [],
    parts: [],
  });
  await renderAppAt("/accounts/20");
  expect(await screen.findByText(/Not analysed\. No value can be built from Holdings/)).toBeInTheDocument();
});

test("an Account whose value is not built from Holdings shows no look-through", async () => {
  accountBackend({ ...LOOK, applies: false, status: "not_analysed", total: null, lines: [], parts: [] });
  await renderAppAt("/accounts/20");
  await screen.findByRole("heading", { name: "Sample Brokerage account" });
  await waitFor(() => expect(screen.queryByRole("region", { name: "Look-through" })).not.toBeInTheDocument());
});

// --- the Instrument page ---------------------------------------------------------------------

const VOCAB = [
  {
    key: "asset_class",
    name: "Asset class",
    labels: [
      { key: "australian_shares", name: "Australian shares" },
      { key: "international_shares", name: "International shares" },
      { key: "cash", name: "Cash" },
      { key: "other", name: "Other" },
    ],
  },
  {
    key: "region",
    name: "Region",
    labels: [
      { key: "australia", name: "Australia" },
      { key: "north_america", name: "North America" },
      { key: "other", name: "Other" },
    ],
  },
  {
    key: "exposure_currency",
    name: "Exposure currency",
    labels: [
      { key: "aud", name: "AUD" },
      { key: "usd", name: "USD" },
      { key: "eur", name: "EUR" },
      { key: "gbp", name: "GBP" },
      { key: "other", name: "Other" },
    ],
  },
];
const INSTRUMENT = { id: 1, name: "Sample Broad Market ETF", kind: "etf", currency: "AUD", notes: null, identifiers: [] };
const profile = (id: number, over: Record<string, unknown>) => ({
  id,
  instrument_id: 1,
  as_at: "2026-06-30",
  source: "manual",
  retrieved_on: "2026-11-22",
  provenance_note: "Invented factsheet",
  recorded_at: "2026-11-22T00:00:00",
  status: "current",
  superseded_at: null,
  superseded_by_id: null,
  weights: [
    { dimension_key: "asset_class", label_key: "australian_shares", percent: "60" },
    { dimension_key: "asset_class", label_key: "international_shares", percent: "39.5" },
  ],
  resolutions: [
    { dimension_key: "asset_class", total_percent: "99.5", difference_percent: "-0.5", outcome: "short", not_classified_percent: "0.5", trimmed: [] },
  ],
  ...over,
});

function instrumentBackend(initial: unknown[], reject = false) {
  let rows = [...initial];
  const posted: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/instruments") return json([INSTRUMENT]);
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (url.pathname === "/api/exposure/vocabulary") return json(VOCAB);
      if (url.pathname === "/api/instruments/1/prices") return json([]);
      if (url.pathname === "/api/instruments/1/exposure-profiles" && request.method === "GET") return json(rows);
      if (url.pathname === "/api/instruments/1/exposure-profiles" && request.method === "POST") {
        const body = (await request.json()) as Record<string, unknown>;
        posted.push(body);
        if (reject)
          return json({ errors: [{ field: "weights", message: "Asset class weights total 50% (50 points short of 100%)." }] }, 422);
        const fresh = profile(9, { as_at: body.as_at, provenance_note: body.provenance_note });
        rows = [fresh, ...rows.map((r) => ({ ...(r as object), status: "corrected", superseded_at: "2026-11-22T01:00:00", superseded_by_id: 9 }))];
        return json(fresh, 201);
      }
      return new Response("?", { status: 500 });
    }),
  );
  return posted;
}

test("the Instrument page shows the profile, how a shortfall resolves, and earlier versions", async () => {
  instrumentBackend([
    profile(2, {}),
    profile(1, { weights: [{ dimension_key: "asset_class", label_key: "cash", percent: "100" }], status: "corrected", superseded_at: "2026-11-21T00:00:00", superseded_by_id: 2 }),
  ]);
  await renderAppAt("/instruments/1");
  const current = within(await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" }));
  expect(current.getByText("39.5%")).toBeInTheDocument();
  expect(current.getByText("Asset class totals 99.5%: the 0.5% shortfall shows as Not classified.")).toBeInTheDocument();
  const earlier = within(await screen.findByRole("table", { name: "Earlier exposure profiles of Sample Broad Market ETF" }));
  expect(earlier.getByText("Asset class: Cash 100%")).toBeInTheDocument();
  expect(earlier.getByText(/Superseded 2026-11-21/)).toBeInTheDocument();
});

test("the form keeps a running total and previews how it will be judged", async () => {
  instrumentBackend([]);
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  const total = () => form.getByRole("status", { name: "Asset class total" });
  expect(total()).toHaveTextContent("Nothing entered");
  fireEvent.change(form.getByLabelText("Australian shares"), { target: { value: "60" } });
  fireEvent.change(form.getByLabelText("International shares"), { target: { value: "39.5" } });
  expect(total()).toHaveTextContent("Total 99.5%: 0.5 points short of 100%. The shortfall shows as Not classified.");
  fireEvent.change(form.getByLabelText("International shares"), { target: { value: "40.8" } });
  expect(total()).toHaveTextContent("Total 100.8%: 0.8 points over 100%. The excess is trimmed from the largest weight.");
  fireEvent.change(form.getByLabelText("International shares"), { target: { value: "40" } });
  expect(total()).toHaveTextContent("Total 100%. Adds to 100%.");
  fireEvent.change(form.getByLabelText("International shares"), { target: { value: "20" } });
  expect(total()).toHaveTextContent("Total 80%: 20 points short of 100%. More than 1 point either way is rejected.");
});

test("saving sends the weights as percent strings, and a rejection shows the difference", async () => {
  const posted = instrumentBackend([], true);
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  fireEvent.change(form.getByLabelText("As at"), { target: { value: "2026-06-30" } });
  fireEvent.change(form.getByLabelText("Source note"), { target: { value: "Invented factsheet" } });
  fireEvent.change(form.getByLabelText("Australian shares"), { target: { value: "50" } });
  fireEvent.click(form.getByRole("button", { name: "Save profile" }));
  expect(await form.findByText("Asset class weights total 50% (50 points short of 100%).")).toBeInTheDocument();
  expect(posted[0]).toEqual({
    as_at: "2026-06-30",
    provenance_note: "Invented factsheet",
    weights: [{ dimension_key: "asset_class", label_key: "australian_shares", percent: "50" }],
  });
  expect(screen.getByRole("form", { name: /exposure profile/ })).toBeInTheDocument(); // stays open on failure
});

test("correcting a profile supersedes it and the earlier version stays visible", async () => {
  const posted = instrumentBackend([profile(2, {})]);
  await renderAppAt("/instruments/1");
  fireEvent.click(await screen.findByRole("button", { name: "Correct the profile as at 2026-06-30" }));
  const form = within(await screen.findByRole("form", { name: "Correct exposure profile of Sample Broad Market ETF" }));
  expect(form.getByLabelText("International shares")).toHaveValue("39.5");
  fireEvent.change(form.getByLabelText("International shares"), { target: { value: "40" } });
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(screen.queryByRole("form", { name: /exposure profile/ })).not.toBeInTheDocument());
  expect(posted).toHaveLength(1);
  expect(await screen.findByRole("table", { name: "Earlier exposure profiles of Sample Broad Market ETF" })).toBeInTheDocument();
});

// --- integer arithmetic, no floats -----------------------------------------------------------

test("weights convert to parts per million by integer arithmetic", () => {
  expect(percentToPpm("60")).toBe(600_000);
  expect(percentToPpm("39.5")).toBe(395_000);
  expect(percentToPpm("0.0001")).toBe(1);
  expect(percentToPpm("100")).toBe(1_000_000);
  expect(percentToPpm("12.34567")).toBeNull();
  expect(percentToPpm("-5")).toBeNull();
  expect(percentToPpm("abc")).toBeNull();
  expect(ppmToPercent(995_000)).toBe("99.5");
  expect(ppmToPercent(-5_000)).toBe("-0.5");
  expect(ppmToPercent(0)).toBe("0");
  // 0.1 + 0.2 is the float trap: in ppm it is exact
  expect(judgeTotal(["0.1", "0.2"]).totalPpm).toBe(3_000);
});

test("a total is accepted exactly at the boundary and rejected one ppm beyond", () => {
  expect(judgeTotal(["99"]).state).toBe("short");
  expect(judgeTotal(["98.9999"]).state).toBe("rejected");
  expect(judgeTotal(["60", "41"]).state).toBe("over");
  expect(judgeTotal(["60", "41.0001"]).state).toBe("rejected");
  expect(judgeTotal(["100"]).state).toBe("exact");
  expect(judgeTotal([""]).state).toBe("empty");
});

// --- all Dimensions on the Instrument page (#84) ----------------------------------------------

test("the header sets Exposure currency beside Holding currency, from the current profile", async () => {
  instrumentBackend([
    profile(2, {
      weights: [
        { dimension_key: "asset_class", label_key: "international_shares", percent: "100" },
        { dimension_key: "exposure_currency", label_key: "aud", percent: "8" },
        { dimension_key: "exposure_currency", label_key: "usd", percent: "62" },
        { dimension_key: "exposure_currency", label_key: "eur", percent: "13" },
        { dimension_key: "exposure_currency", label_key: "gbp", percent: "4" },
      ],
      resolutions: [],
    }),
  ]);
  await renderAppAt("/instruments/1");
  expect(await screen.findByText("Holding currency AUD")).toBeInTheDocument();
  expect(await screen.findByText("USD 62%, EUR 13%, AUD 8% and 1 more")).toBeInTheDocument();
});

test("the profile card groups weights under their Dimension, so a repeated label is not ambiguous", async () => {
  instrumentBackend([
    profile(2, {
      weights: [
        { dimension_key: "asset_class", label_key: "other", percent: "1" },
        { dimension_key: "region", label_key: "other", percent: "7" },
      ],
      resolutions: [],
    }),
  ]);
  await renderAppAt("/instruments/1");
  const card = within(await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" }));
  const region = within(await card.findByRole("rowgroup", { name: "Region" }));
  expect(region.getByText("7%")).toBeInTheDocument();
  expect(region.queryByText("1%")).not.toBeInTheDocument();
  expect(within(card.getByRole("rowgroup", { name: "Asset class" })).getByText("1%")).toBeInTheDocument();
});

test("the header says so when the profile leaves Exposure currency out", async () => {
  instrumentBackend([profile(2, {})]);
  await renderAppAt("/instruments/1");
  expect(await screen.findByText("not in its profile")).toBeInTheDocument();
});

test("the header says so when there is no profile", async () => {
  instrumentBackend([]);
  await renderAppAt("/instruments/1");
  expect(await screen.findByText("no profile yet")).toBeInTheDocument();
});

test("the form has every Dimension with its own total, and a Dimension may be left out", async () => {
  const posted = instrumentBackend([]);
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  const region = within(form.getByRole("group", { name: "Region weights (%)" }));
  const currency = within(form.getByRole("group", { name: "Exposure currency weights (%)" }));
  expect(form.getByRole("status", { name: "Region total" })).toHaveTextContent("Nothing entered: this Dimension is left out.");
  fireEvent.change(region.getByLabelText("Australia"), { target: { value: "70" } });
  fireEvent.change(region.getByLabelText("North America"), { target: { value: "29.5" } });
  fireEvent.change(currency.getByLabelText("USD"), { target: { value: "50" } });
  // each Dimension is judged on its own
  expect(form.getByRole("status", { name: "Region total" })).toHaveTextContent("Total 99.5%: 0.5 points short of 100%.");
  expect(form.getByRole("status", { name: "Exposure currency total" })).toHaveTextContent("Total 50%: 50 points short of 100%. More than 1 point");
  expect(form.getByRole("status", { name: "Asset class total" })).toHaveTextContent("Nothing entered");
  fireEvent.change(currency.getByLabelText("USD"), { target: { value: "100" } });
  fireEvent.change(form.getByLabelText("Source note"), { target: { value: "Invented factsheet" } });
  fireEvent.click(form.getByRole("button", { name: "Save profile" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  // only the Dimensions with figures are sent: asset class is omitted
  expect(posted[0].weights).toEqual([
    { dimension_key: "region", label_key: "australia", percent: "70" },
    { dimension_key: "region", label_key: "north_america", percent: "29.5" },
    { dimension_key: "exposure_currency", label_key: "usd", percent: "100" },
  ]);
});

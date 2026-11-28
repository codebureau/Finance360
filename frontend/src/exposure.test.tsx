import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { judgeTotal, percentToPpm, ppmToPercent } from "@/lib/exposure";
import { everyday, fakeBackend as accountsBackend, json } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

vi.mock("@/components/echart", () => ({
  default: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
}));

afterEach(() => vi.unstubAllGlobals());

// The Login's Display preferences, as the server keeps them.
let prefs: Record<string, string> = {};
const puts: Record<string, string | null>[] = [];
beforeEach(() => {
  prefs = {};
  puts.length = 0;
});

async function preferences(request: Request): Promise<Response | null> {
  if (new URL(request.url).pathname !== "/api/preferences") return null;
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

const aud = (amount: string, minor: number) => ({ amount, amount_minor: minor, currency: "AUD" });
const PI = { ...everyday, id: 20, name: "Sample Brokerage account", category_key: "investments" };

const LOOK = {
  applies: true,
  source: "holdings",
  statement_id: null,
  statement_date: null,
  change_since: null,
  as_at: "2026-09-30",
  status: "ok",
  reason: null,
  issues: [],
  dimension_key: "asset_class",
  dimension_name: "Asset class",
  total: aud("6645.13", 664513),
  lines: [
    { kind: "label", key: "australian_shares", name: "Australian shares", amount: aud("3263.05", 326305), percent: "49.10", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "label", key: "international_shares", name: "International shares", amount: aud("1898.12", 189812), percent: "28.56", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "label", key: "cash", name: "Cash", amount: aud("834.93", 83493), percent: "12.56", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "no_profile", key: null, name: "No profile: Sample New Horizons ETF", amount: aud("625.00", 62500), percent: "9.41", instrument_id: 3, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "not_classified", key: null, name: "Not classified", amount: aud("24.03", 2403), percent: "0.36", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
  ],
  parts: [
    { instrument_id: 1, name: "Sample Broad Market ETF", amount: aud("4805.38", 480538), option_name: null, instrument_name: null, profile_as_at: "2026-06-30", dated_after: false, shortfall_percent: "0.5", trimmed: [], omitted: false, underlying: [] },
    { instrument_id: 3, name: "Sample New Horizons ETF", amount: aud("625.00", 62500), option_name: null, instrument_name: null, profile_as_at: "2026-12-31", dated_after: true, shortfall_percent: "0", trimmed: [], omitted: false, underlying: [] },
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
      const pref = await preferences(request);
      if (pref) return pref;
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
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect(panel.getByRole("heading", { name: "Look-through by asset class" })).toBeInTheDocument();
  // the designed view leads: a headline and a chart, with the figures behind a disclosure
  expect(
    await panel.findByText("Mostly Australian shares and International shares; 9.41% has no profile yet"),
  ).toBeInTheDocument();
  expect(await panel.findByRole("img", { name: /Donut chart\..*The Account's value by asset class\./ })).toBeInTheDocument();
  expect(panel.queryByRole("table", { name: "Look-through lines" })).not.toBeInTheDocument();
  fireEvent.click(panel.getByRole("button", { name: "Show figures" }));
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
  option_name: null,
  instrument_name: null,
  instrument_id: 1,
  name: "Sample Balanced Growth Fund",
  amount: aud("500.00", 50000),
  profile_as_at: "2026-06-30",
  dated_after: false,
  shortfall_percent: "0",
  trimmed: [],
  omitted: true,
  underlying: [],
};

test("the Dimension switcher lists every Dimension, asks the server for the chosen one and keeps it in the URL", async () => {
  const calls = accountBackend((dimension: string | null) =>
    dimension === "sector"
      ? {
          ...LOOK,
          dimension_key: "sector",
          dimension_name: "Sector",
          lines: [
            { kind: "label", key: "materials", name: "Materials", amount: aud("379.82", 37982), percent: "5.72", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
            { kind: "not_classified", key: null, name: "Not classified", amount: aud("6265.31", 626531), percent: "94.28", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
            { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
          ],
          parts: [PART_OMITTING],
        }
      : LOOK,
  );
  const { router } = await renderAppAt("/accounts/20");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
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
  fireEvent.click(panel.getByRole("button", { name: "Show figures" }));
  expect(await within(await panel.findByRole("table", { name: "Look-through lines" })).findByText("94.28%")).toBeInTheDocument();
  expect(panel.getByText(/Not classified is value with no sector weights/)).toBeInTheDocument();
});

test("a Dimension in the URL is the one shown, and exposure currency is set apart from Holding currency", async () => {
  const calls = accountBackend({
    ...LOOK,
    dimension_key: "exposure_currency",
    dimension_name: "Exposure currency",
    lines: [
      { kind: "label", key: "usd", name: "USD", amount: aud("6645.13", 664513), percent: "100.00", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
      { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    ],
  });
  await renderAppAt("/accounts/20?dimension=exposure_currency");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect(await panel.findByRole("heading", { name: "Look-through by exposure currency" })).toBeInTheDocument();
  expect(calls.at(-1)?.searchParams.get("dimension")).toBe("exposure_currency");
  expect(panel.getByText("Holding currency AUD")).toBeInTheDocument();
  expect(panel.getByText(/the currencies the underlying assets are actually exposed to/)).toBeInTheDocument();
  expect(panel.getByRole("tab", { name: "Exposure currency" })).toHaveAttribute("aria-selected", "true");
});

test("recording an Opening position refreshes the look-through, which is built from the Holdings", async () => {
  const calls = accountBackend(LOOK);
  await renderAppAt("/accounts/20");
  await screen.findByRole("region", { name: /^Look-through/ });
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
  fireEvent.click(await screen.findByRole("button", { name: "Show figures" }));
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

test("a Not analysed Account lists what is missing, nothing imported first, each with a link to Imports", async () => {
  accountBackend({
    ...LOOK,
    status: "not_analysed",
    reason: "No value can be built from Holdings on 2026-09-30. The cash Stream has no committed Import.",
    issues: [
      { stream: "cash", kind: "missing", message: "The cash Stream has no committed Import.", link: "/imports" },
      { stream: "trade", kind: "stale", message: "The trade Stream's Coverage ends 2026-03-31, before 2026-09-30: it is stale.", link: "/imports" },
    ],
    total: null,
    lines: [],
    parts: [],
  });
  await renderAppAt("/accounts/20");
  const list = within(await screen.findByRole("list", { name: "What is missing" }));
  const items = list.getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("The cash Stream has no committed Import.");
  expect(items[1]).toHaveTextContent("The trade Stream's Coverage ends 2026-03-31");
  expect(within(items[0]).getByRole("link", { name: "Add an Import" })).toHaveAttribute("href", "/imports");
});

test("an Account whose value is not built from Holdings shows no look-through", async () => {
  accountBackend({ ...LOOK, applies: false, status: "not_analysed", total: null, lines: [], parts: [] });
  await renderAppAt("/accounts/20");
  await screen.findByRole("heading", { name: "Sample Brokerage account" });
  await waitFor(() => expect(screen.queryByRole("region", { name: /^Look-through/ })).not.toBeInTheDocument());
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
const UNDERLYING = { id: 2, name: "Sample Global Equity ETF", kind: "etf", currency: "AUD", notes: null, identifiers: [] };
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
  references: [],
  weights: [
    { dimension_key: "asset_class", label_key: "australian_shares", percent: "60" },
    { dimension_key: "asset_class", label_key: "international_shares", percent: "39.5" },
  ],
  resolutions: [
    { dimension_key: "asset_class", total_percent: "99.5", difference_percent: "-0.5", outcome: "short", not_classified_percent: "0.5", trimmed: [], trimmed_references: [] },
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
      const pref = await preferences(request);
      if (pref) return pref;
      if (url.pathname === "/api/instruments") return json([INSTRUMENT, UNDERLYING]);
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
  fireEvent.click(current.getByRole("button", { name: "Show figures" }));
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
    references: [],
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
  fireEvent.click(card.getByRole("button", { name: "Show figures" }));
  const region = within(await card.findByRole("rowgroup", { name: "Region" }));
  expect(region.getByText("7%")).toBeInTheDocument();
  expect(region.queryByText("1%")).not.toBeInTheDocument();
  expect(within(card.getByRole("rowgroup", { name: "Asset class" })).getByText("1%")).toBeInTheDocument();
});

test("for a fund-of-funds the header says its Exposure currency weights are only what it holds directly", async () => {
  instrumentBackend([
    profile(2, {
      weights: [{ dimension_key: "exposure_currency", label_key: "aud", percent: "10" }],
      references: [{ instrument_id: 2, instrument_name: "Sample Global Equity ETF", percent: "90" }],
      resolutions: [],
    }),
  ]);
  await renderAppAt("/instruments/1");
  expect(await screen.findByText("AUD 10% directly, 90% through underlying funds")).toBeInTheDocument();
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

// --- designed views (understanding first) ----------------------------------------------------

test("each Dimension of a profile gets its own chart, and one the profile leaves out says so", async () => {
  instrumentBackend([profile(2, {})]);
  await renderAppAt("/instruments/1");
  const card = within(await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" }));
  expect(
    await card.findByRole("img", { name: /^Donut chart\. Mostly Australian shares and International shares.*Sample Broad Market ETF by asset class\./ }),
  ).toBeInTheDocument();
  expect(card.getAllByRole("img")).toHaveLength(1);
  for (const name of ["Region split", "Exposure currency split"])
    expect(within(card.getByRole("region", { name })).getByText("Not published")).toBeInTheDocument();
  // the exact figures are behind the disclosure, closed by default
  expect(card.queryByRole("table", { name: "Weights as at 2026-06-30" })).not.toBeInTheDocument();
  fireEvent.click(card.getByRole("button", { name: "Show figures" }));
  expect(card.getByRole("table", { name: "Weights as at 2026-06-30" })).toBeInTheDocument();
  fireEvent.click(card.getByRole("button", { name: "Hide figures" }));
  expect(card.queryByRole("table", { name: "Weights as at 2026-06-30" })).not.toBeInTheDocument();
});

test("the form's total bar fills towards 100% and marks the accepted band", async () => {
  instrumentBackend([]);
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  const bar = () => form.getAllByTestId("total-bar")[0];
  expect(bar()).toHaveAttribute("data-state", "empty");
  fireEvent.change(form.getByLabelText("Australian shares"), { target: { value: "55" } });
  expect(bar()).toHaveAttribute("data-state", "rejected");
  expect(bar().firstElementChild).toHaveStyle({ width: "50%" });
  fireEvent.change(form.getByLabelText("Australian shares"), { target: { value: "100" } });
  expect(bar()).toHaveAttribute("data-state", "exact");
});

// --- fund-of-funds (#85) ---------------------------------------------------------------------

const FUND_LOOK = {
  ...LOOK,
  lines: [
    {
      kind: "label",
      key: "australian_shares",
      name: "Australian shares",
      amount: aud("3764.65", 376465),
      percent: "46.11",
      instrument_id: null,
      option_name: null, through_fund: true,
      reason: null,
      contributions: [
        { path: ["Sample Broad Market ETF"], amount: aud("2883.23", 288323) },
        { path: ["Sample Diversified Fund", "Sample Broad Market ETF"], amount: aud("501.60", 50160) },
      ],
    },
    { kind: "label", key: "cash", name: "Cash", amount: aud("992.25", 99225), percent: "12.15", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
    { kind: "unallocated", key: null, name: "Unallocated", amount: aud("0.00", 0), percent: "0.00", instrument_id: null, option_name: null, through_fund: false, contributions: [], reason: null },
  ],
  parts: [
    {
      ...LOOK.parts[0],
      underlying: [
        {
          path: ["Sample Diversified Fund", "Sample Broad Market ETF"],
          amount: aud("836.00", 83600),
          profile_as_at: "2026-12-31",
          dated_after: true,
          shortfall_percent: "0.5",
          trimmed: [],
          omitted: false,
        },
      ],
    },
  ],
};

test("a line an underlying fund gave to is marked, and Show underlying says which fund gave what", async () => {
  accountBackend(FUND_LOOK);
  await renderAppAt("/accounts/20");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  fireEvent.click(await panel.findByRole("button", { name: "Show figures" }));
  const table = within(await panel.findByRole("table", { name: "Look-through lines" }));
  const row = table.getByText("Australian shares").closest("tr")!;
  expect(within(row).getByText("Through a fund")).toBeInTheDocument();
  expect(within(table.getByText("Cash").closest("tr")!).queryByText("Through a fund")).not.toBeInTheDocument();
  expect(table.queryByText("Sample Diversified Fund › Sample Broad Market ETF")).not.toBeInTheDocument();
  fireEvent.click(within(row).getByRole("button", { name: "Show underlying: Australian shares" }));
  const routes = table.getAllByRole("row").filter((r) => r.getAttribute("data-kind") === "contribution");
  expect(routes.map((r) => r.textContent)).toEqual([
    "Sample Broad Market ETF2,883.23",
    "Sample Diversified Fund › Sample Broad Market ETF501.60",
  ]);
  fireEvent.click(within(row).getByRole("button", { name: "Hide underlying: Australian shares" }));
  expect(table.queryByText("Sample Diversified Fund › Sample Broad Market ETF")).not.toBeInTheDocument();
  // an underlying fund's own profile notes are named by their route
  expect(
    panel.getByText(
      /Sample Diversified Fund › Sample Broad Market ETF: the profile is dated after 2026-09-30 \(2026-12-31\)/,
    ),
  ).toBeInTheDocument();
  expect(panel.getByText(/Sample Diversified Fund › Sample Broad Market ETF: its profile is 0.5 points short/)).toBeInTheDocument();
});

test("the look-through chart is a donut that switches form, remembered for this Dimension only", async () => {
  accountBackend(LOOK);
  await renderAppAt("/accounts/20");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect((await panel.findByTestId("share-chart")).getAttribute("data-form")).toBe("donut");
  fireEvent.click(panel.getByRole("radio", { name: "Stacked bar" }));
  await waitFor(() => expect(puts).toEqual([{ "lookthrough.asset-class": "stacked-bar" }]));
  expect(panel.getByTestId("share-chart").getAttribute("data-form")).toBe("stacked-bar");
  expect(panel.getByRole("button", { name: "Use as my default" })).toBeInTheDocument();
  // the Dimension switcher is untouched by the chart switch
  expect(panel.getByRole("tab", { name: "Asset class" })).toHaveAttribute("aria-selected", "true");
});

const FUND_PROFILE = (over: Record<string, unknown> = {}) =>
  profile(5, {
    weights: [{ dimension_key: "asset_class", label_key: "cash", percent: "10" }],
    references: [
      { instrument_id: 2, instrument_name: "Sample Global Equity ETF", percent: "35" },
      { instrument_id: 3, instrument_name: "Sample Mining Ltd", percent: "55" },
    ],
    resolutions: [
      { dimension_key: "asset_class", total_percent: "100", difference_percent: "0", outcome: "exact", not_classified_percent: "0", trimmed: [], trimmed_references: [] },
    ],
    ...over,
  });

test("a fund-of-funds profile leads with what it holds, lists its underlying funds and says what its charts show", async () => {
  instrumentBackend([FUND_PROFILE()]);
  await renderAppAt("/instruments/1");
  const card = within(await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" }));
  expect(
    await card.findByText("A fund of funds: 90% is held in Sample Mining Ltd (55%) and Sample Global Equity ETF (35%)"),
  ).toBeInTheDocument();
  expect(await card.findByRole("img", { name: /Share of Sample Broad Market ETF held in each underlying fund\./ })).toBeInTheDocument();
  expect(card.getByRole("region", { name: "Underlying funds split" })).toBeInTheDocument();
  // the Dimensions it gives no weights of its own come only through the underlying funds
  expect(within(card.getByRole("region", { name: "Region split" })).getByText("Only through its underlying funds")).toBeInTheDocument();
  expect(card.getAllByText("Its own holdings, beside the share held through its underlying funds.")).toHaveLength(1);
  fireEvent.click(card.getByRole("button", { name: "Show figures" }));
  const funds = within(card.getByRole("rowgroup", { name: "Underlying funds" }));
  expect(funds.getByRole("link", { name: "Sample Global Equity ETF" })).toHaveAttribute("href", "/instruments/2");
  expect(funds.getByText("55%")).toBeInTheDocument();
});

test("one switch serves all of a profile card's charts and stores the card's choice", async () => {
  instrumentBackend([FUND_PROFILE()]);
  await renderAppAt("/instruments/1");
  const card = within(await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" }));
  await card.findAllByTestId("share-chart");
  expect(card.getAllByRole("group", { name: "Chart type" })).toHaveLength(1);
  const charts = () => card.getAllByTestId("share-chart").map((c) => c.getAttribute("data-form"));
  expect(charts().every((f) => f === "donut")).toBe(true);
  fireEvent.click(card.getByRole("radio", { name: "Bars" }));
  await waitFor(() => expect(puts).toEqual([{ "exposure-profile": "bars" }]));
  expect(charts().every((f) => f === "bars")).toBe(true);
  expect(charts().length).toBeGreaterThan(1);
});

test("a profile of underlying funds alone still has its headline, even beyond 100% (the server trims it)", async () => {
  instrumentBackend([
    FUND_PROFILE({
      weights: [],
      references: [{ instrument_id: 2, instrument_name: "Sample Global Equity ETF", percent: "101" }],
      resolutions: [],
    }),
  ]);
  await renderAppAt("/instruments/1");
  await screen.findByRole("region", { name: "Exposure profile as at 2026-06-30" });
  expect(await screen.findByText(/A fund of funds: 101% is held in Sample Global Equity ETF \(101%\)/)).toBeInTheDocument();
});

test("the form takes underlying funds, counts them towards each Dimension and sends them as percent strings", async () => {
  const posted = instrumentBackend([]);
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  fireEvent.click(form.getByRole("button", { name: "Add underlying fund" }));
  const chooser = form.getByLabelText("Underlying fund 1");
  // the Instrument itself cannot be its own underlying fund
  expect(within(chooser).queryByRole("option", { name: "Sample Broad Market ETF" })).not.toBeInTheDocument();
  await within(chooser).findByRole("option", { name: "Sample Global Equity ETF" });
  fireEvent.change(chooser, { target: { value: "2" } });
  fireEvent.change(form.getByLabelText("Underlying fund 1 weight (%)"), { target: { value: "60" } });
  expect(form.getByRole("status", { name: "Underlying funds total" })).toHaveTextContent("Underlying funds total 60%.");
  // a Dimension left out says what the funds do not cover
  expect(form.getByRole("status", { name: "Asset class total" })).toHaveTextContent(
    "Nothing entered: this Dimension is left out, and what the underlying funds do not cover (40%) shows as Not classified.",
  );
  fireEvent.change(form.getByLabelText("Cash"), { target: { value: "40" } });
  expect(form.getByRole("status", { name: "Asset class total" })).toHaveTextContent(
    "Total 100% (with 60% in underlying funds). Adds to 100%.",
  );
  fireEvent.change(form.getByLabelText("Cash"), { target: { value: "20" } });
  expect(form.getByRole("status", { name: "Asset class total" })).toHaveTextContent("Total 80% (with 60% in underlying funds): 20 points short of 100%. More than 1 point");
  fireEvent.change(form.getByLabelText("Cash"), { target: { value: "40" } });
  fireEvent.change(form.getByLabelText("Source note"), { target: { value: "Invented fact sheet" } });
  fireEvent.click(form.getByRole("button", { name: "Save profile" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].references).toEqual([{ instrument_id: 2, percent: "60" }]);
  expect(posted[0].weights).toEqual([{ dimension_key: "asset_class", label_key: "cash", percent: "40" }]);
});

test("a loop of funds is rejected with the server's message under the underlying funds, and a row can be removed", async () => {
  instrumentBackend([], false);
  const inner = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "POST" && new URL(request.url).pathname === "/api/instruments/1/exposure-profiles")
        return json(
          { errors: [{ field: "references", message: "This would make a loop of funds: A > B > A. A fund cannot hold itself, directly or through other funds." }] },
          422,
        );
      return inner(request);
    }),
  );
  await renderAppAt("/instruments/1");
  const enter = await screen.findByRole("button", { name: "Enter exposure profile" });
  await waitFor(() => expect(enter).toBeEnabled());
  fireEvent.click(enter);
  const form = within(await screen.findByRole("form", { name: "Enter exposure profile of Sample Broad Market ETF" }));
  fireEvent.click(form.getByRole("button", { name: "Add underlying fund" }));
  fireEvent.click(form.getByRole("button", { name: "Add underlying fund" }));
  fireEvent.click(form.getByRole("button", { name: "Remove underlying fund 2" }));
  expect(form.queryByLabelText("Underlying fund 2")).not.toBeInTheDocument();
  await within(form.getByLabelText("Underlying fund 1")).findByRole("option", { name: "Sample Global Equity ETF" });
  fireEvent.change(form.getByLabelText("Underlying fund 1"), { target: { value: "2" } });
  fireEvent.change(form.getByLabelText("Underlying fund 1 weight (%)"), { target: { value: "100" } });
  fireEvent.change(form.getByLabelText("Source note"), { target: { value: "Invented fact sheet" } });
  fireEvent.click(form.getByRole("button", { name: "Save profile" }));
  expect(await form.findByText(/This would make a loop of funds: A > B > A\./)).toBeInTheDocument();
});

test("underlying funds count towards a Dimension's running total, and only where it has weights", () => {
  expect(judgeTotal(["40"], 600_000)).toMatchObject({ totalPpm: 1_000_000, state: "exact" });
  expect(judgeTotal(["30"], 600_000)).toMatchObject({ state: "rejected", differencePpm: -100_000 });
  expect(judgeTotal([""], 600_000).state).toBe("empty"); // left out, however much the funds cover
});

// --- super Investment options (#86) ----------------------------------------------------------

const SUPER = {
  ...everyday,
  id: 21,
  name: "Sample Super Fund",
  category_key: "superannuation",
  account_type_key: "super_fund_account",
};

const optionLine = (over: Record<string, unknown>) => ({
  key: null,
  instrument_id: null,
  option_name: null,
  through_fund: false,
  contributions: [],
  reason: null,
  ...over,
});
const optionPart = (over: Record<string, unknown>) => ({
  profile_as_at: "2026-06-30",
  dated_after: false,
  shortfall_percent: "0",
  trimmed: [],
  omitted: false,
  underlying: [],
  ...over,
});

const SUPER_LOOK = {
  applies: true,
  issues: [],
  source: "super_statement",
  statement_id: 7,
  statement_date: "2026-06-30",
  change_since: "2026-06-30",
  as_at: "2026-09-30",
  status: "ok",
  reason: null,
  dimension_key: "asset_class",
  dimension_name: "Asset class",
  total: aud("215228.07", 21522807),
  lines: [
    optionLine({ kind: "label", key: "australian_shares", name: "Australian shares", amount: aud("48435.00", 4843500), percent: "22.50" }),
    optionLine({ kind: "label", key: "international_shares", name: "International shares", amount: aud("51600.00", 5160000), percent: "23.98" }),
    optionLine({ kind: "no_profile", name: "No profile: High Growth (not mapped to a fund)", amount: aud("53250.01", 5325001), percent: "24.74", option_name: "High Growth" }),
    optionLine({ kind: "unallocated", name: "Unallocated", amount: aud("3728.06", 372806), percent: "1.73" }),
  ],
  parts: [
    optionPart({ option_name: "Growth", instrument_id: 31, instrument_name: "Sample Super Growth Option", name: "Growth", amount: aud("68250.00", 6825000) }),
    optionPart({ option_name: "High Growth", instrument_id: null, instrument_name: null, name: "High Growth", amount: aud("53250.01", 5325001), profile_as_at: null }),
  ],
  flagged: false,
  tolerance: aud("32284.21", 3228421),
};

function superBackend(look: unknown) {
  accountsBackend([SUPER]);
  const inner = globalThis.fetch;
  const puts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const pref = await preferences(request);
      if (pref) return pref;
      if (url.pathname === "/api/accounts/21/look-through") return json(look);
      if (url.pathname === "/api/accounts/21/investment-option-links") {
        puts.push(await request.json());
        return json({ option_name: "High Growth", instrument_id: 40, instrument_name: "High Growth (Sample Super Fund)" });
      }
      if (url.pathname === "/api/exposure/vocabulary") return json(DIMENSIONS);
      if (url.pathname === "/api/accounts/21/super-statements") return json([]);
      if (url.pathname === "/api/instruments")
        return json([{ id: 31, name: "Sample Super Growth Option", kind: "fund", currency: "AUD", notes: null, identifiers: [] }]);
      return inner(request);
    }),
  );
  return puts;
}

test("a super Account shows the same look-through panel, with Unallocated as the change since the statement", async () => {
  superBackend(SUPER_LOOK);
  await renderAppAt("/accounts/21");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect(await panel.findByText(/Largest: International shares and Australian shares; 24.74% has no profile yet/)).toBeInTheDocument();
  fireEvent.click(panel.getByRole("button", { name: "Show figures" }));
  const lines = within(await panel.findByRole("table", { name: "Look-through lines" }));
  // quiet: the change since the statement is muted, with no warning
  const row = lines.getByText(/Unallocated: change since 30 June 2026/).closest("tr")!;
  expect(row).toHaveClass("text-muted-foreground");
  expect(panel.queryByText("The option mix may have changed")).not.toBeInTheDocument();
  // each option's mapping is in the figures; an unmapped one offers the mapping
  const options = within(panel.getByRole("table", { name: "Investment options" }));
  expect(options.getByRole("link", { name: "Sample Super Growth Option" })).toHaveAttribute("href", "/instruments/31");
  expect(options.getByText("Not mapped")).toBeInTheDocument();
  expect(panel.getByText(/never scaled to the Account's value/)).toBeInTheDocument();
});

test("a stale option mix beyond tolerance is flagged with a warning icon and words", async () => {
  superBackend({ ...SUPER_LOOK, flagged: true });
  await renderAppAt("/accounts/21");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect(await panel.findByText(/24.74% has no profile yet; 1.73% is unallocated: the option mix may have changed/)).toBeInTheDocument();
  fireEvent.click(panel.getByRole("button", { name: "Show figures" }));
  const badge = await panel.findByText("The option mix may have changed");
  expect(badge.querySelector("svg")).not.toBeNull();
});

test("an unmapped option opens the mapping dialog and creates a fund from its name", async () => {
  const puts = superBackend(SUPER_LOOK);
  await renderAppAt("/accounts/21");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  fireEvent.click(await panel.findByRole("button", { name: "Show figures" }));
  const lines = within(await panel.findByRole("table", { name: "Look-through lines" }));
  fireEvent.click(lines.getByRole("button", { name: "Map to a fund" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Map High Growth to a fund" }));
  expect(dialog.getByRole("option", { name: "Create a fund from “High Growth”" })).toBeInTheDocument();
  expect(await dialog.findByRole("option", { name: "Sample Super Growth Option" })).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "Map to a fund" }));
  await waitFor(() =>
    expect(puts).toEqual([{ option_name: "High Growth", create_instrument: true, instrument_id: null }]),
  );
});

test("a super Account with no Super statement asks for one", async () => {
  superBackend({
    ...SUPER_LOOK,
    status: "not_analysed",
    statement_id: null,
    statement_date: null,
    change_since: null,
    total: null,
    lines: [],
    parts: [],
    reason: "There is no Super statement on or before 2026-05-01. Provide a Super statement to see what this Account's Investment options hold.",
  });
  await renderAppAt("/accounts/21");
  const panel = within(await screen.findByRole("region", { name: /^Look-through/ }));
  expect(await panel.findByText(/Not analysed\. There is no Super statement/)).toBeInTheDocument();
  expect(panel.getByText(/Provide a Super statement/)).toBeInTheDocument();
});

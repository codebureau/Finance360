import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import { everyday, fakeBackend as accountsBackend, type Call } from "@/test-accounts-backend";

type Unresolved = { scheme: string; ref: string; name: string | null; kind_hint: string | null; row_count: number };
type Imp = {
  id: number;
  stream: string;
  file_name: string;
  status: string;
  coverage_from: string;
  coverage_to: string;
  added_at: string;
  account_id: number;
  account_name: string;
  importer_name: string;
  row_count: number;
  unresolved_count: number;
  commit_blocked: boolean;
  warnings: string[];
  unresolved: Unresolved[];
  unmapped_count: number;
  unmapped_types: { raw_type: string; row_count: number; choices: string[] }[];
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const PI = { ...everyday, id: 20, name: "Sample Brokerage account" };
const FUND = {
  id: 3,
  name: "Sample Balanced Growth Fund",
  kind: "fund",
  currency: "AUD",
  notes: null,
  identifiers: [],
};
const TRADE: Imp = {
  id: 5,
  stream: "trade",
  file_name: "sample_pi_trades.csv",
  status: "staged",
  coverage_from: "2026-08-03",
  coverage_to: "2026-09-30",
  added_at: "2026-10-20T01:00:00Z",
  account_id: 20,
  account_name: "Sample Brokerage account",
  importer_name: "Vanguard Personal Investor",
  row_count: 30,
  unresolved_count: 1,
  commit_blocked: true,
  warnings: ["The file states no period, so Coverage runs from its first to last row."],
  unresolved: [{ scheme: "ticker", ref: "SNHX", name: "Sample New Horizons ETF", kind_hint: "etf", row_count: 1 }],
  unmapped_count: 0,
  unmapped_types: [],
};

const row = (n: number, over: Record<string, unknown> = {}) => ({
  row_no: n,
  occurred_on: "2026-09-30",
  kind: "buy",
  instrument_ref: "SBME",
  instrument_name: "Sample Broad Market Index ETF",
  instrument_id: 1,
  resolved_name: "Sample Broad Market ETF",
  quantity: "20",
  unit_price: "96.1075",
  unit_price_ccy: "AUD",
  fee: { amount: "9.50", amount_minor: 950, currency: "AUD" },
  amount: null,
  description: null,
  change: "added" as string | null,
  ...over,
});

type Diff = {
  scope_from: string;
  scope_to: string;
  added: number;
  removed: number;
  unchanged: number;
  warnings: { code: string; message: string }[];
  commit_blocked: boolean;
};
type World = {
  imports: Imp[];
  detect?: unknown;
  detectError?: boolean;
  staged?: Imp;
  diff?: Diff;
  removed?: unknown;
  commitError?: boolean;
};

const CLEAN_DIFF: Diff = {
  scope_from: "2026-08-03",
  scope_to: "2026-09-30",
  added: 30,
  removed: 0,
  unchanged: 0,
  warnings: [],
  commit_blocked: false,
};
const calls: Call[] = [];

function backend(world: World) {
  calls.length = 0;
  world.imports = structuredClone(world.imports);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const raw = request.method === "POST" ? await request.clone().arrayBuffer() : undefined;
      let body: Record<string, unknown> | undefined;
      if (raw && raw.byteLength && request.headers.get("content-type")?.includes("json"))
        body = JSON.parse(new TextDecoder().decode(raw));
      calls.push({ method: request.method, path, search: url.search, body });
      const bytes = raw?.byteLength;
      if (path === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (path === "/api/accounts") return json([PI]);
      if (path === "/api/instruments") return json([FUND]);
      if (path === "/api/imports/detect") {
        if (world.detectError)
          return json({ errors: [{ field: "file", message: "Finance360 does not recognise this file." }] }, 422);
        return json(world.detect);
      }
      if (path === "/api/imports" && request.method === "POST") {
        expect(request.headers.get("content-type")).toContain("application/octet-stream");
        expect(bytes).toBeGreaterThan(0);
        world.imports = [world.staged!, ...world.imports];
        return json({ ...world.staged, created: true }, 201);
      }
      if (path === "/api/imports") return json(world.imports);
      const rows = /^\/api\/imports\/(\d+)\/rows$/.exec(path);
      if (rows) {
        const offset = Number(url.searchParams.get("offset"));
        const all = Array.from({ length: 30 }, (_, i) => row(i + 1));
        all[2] = row(3, { instrument_ref: "SNHX", instrument_id: null, resolved_name: null, instrument_name: "Sample New Horizons ETF" });
        all[3] = row(4, {
          kind: "trade_settlement",
          quantity: null,
          unit_price: null,
          fee: null,
          amount: { amount: "-1922.15", amount_minor: -192215, currency: "AUD" },
        });
        return json({ total: 30, items: all.slice(offset, offset + 25) });
      }
      const diffRoute = /^\/api\/imports\/(\d+)\/diff(\/removed)?$/.exec(path);
      if (diffRoute)
        return diffRoute[2] ? json(world.removed ?? { total: 0, items: [] }) : json(world.diff ?? CLEAN_DIFF);
      const act = /^\/api\/imports\/(\d+)(?:\/(discard|commit|references\/map|references\/create|types\/map))?$/.exec(path);
      if (act) {
        const imp = world.imports.find((i) => i.id === Number(act[1]));
        if (!imp) return json({ message: "Import 9 does not exist." }, 404);
        if (act[2] === "discard") Object.assign(imp, { status: "discarded", commit_blocked: false });
        if (act[2] === "commit") {
          if (world.commitError)
            return json({ errors: [{ field: "status", message: "This Import is committed." }] }, 422);
          Object.assign(imp, {
            status: "committed",
            commit_blocked: false,
            committed: { committed_at: "2026-10-20T03:00:00Z", added: 30, replaced: 2, unchanged: 0, instruments_without_price: 0, offer_fetch: false },
          });
        }
        if (act[2] === "types/map")
          Object.assign(imp, { unmapped_types: [], unmapped_count: 0, commit_blocked: false });
        if (act[2]?.startsWith("references"))
          Object.assign(imp, { unresolved: [], unresolved_count: 0, commit_blocked: false });
        return json(imp);
      }
      return new Response("?", { status: 500 });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const DETECTION = {
  importer_key: "vanguard_pi",
  importer_name: "Vanguard Personal Investor",
  stream: "trade",
  coverage_from: "2026-08-03",
  coverage_to: "2026-09-30",
  row_count: 5,
  warnings: [],
  reference_masked: "••• 0001",
  matches: [{ id: 20, name: "Sample Brokerage account" }],
  duplicate_import_id: null,
};

test("the Imports list is a table; its rows open the detail page and Add file is the one primary action", async () => {
  backend({ imports: [TRADE] });
  const { router } = await renderAppAt("/imports");
  const table = await screen.findByRole("table", { name: "Imports" });
  const line = within(within(table).getByRole("row", { name: "sample_pi_trades.csv" }));
  expect(line.getByText("Trade")).toBeInTheDocument();
  expect(line.getByText("Needs matching")).toBeInTheDocument();
  expect(line.getAllByText(/03 Aug 2026 to 30 Sep 2026/).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("button", { name: /Add file/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("row", { name: "sample_pi_trades.csv" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/imports/5"));
});

test("adding a file in a Dialog preselects the matching Account and stages the raw file", async () => {
  backend({ imports: [], detect: DETECTION, staged: TRADE });
  const { router } = await renderAppAt("/imports");
  fireEvent.click(await screen.findByRole("button", { name: /Add file/ }));
  const dialog = await screen.findByRole("dialog", { name: "Add a file" });
  const file = new File(["Date,Product ID\n"], "sample_pi_trades.csv", { type: "text/csv" });
  fireEvent.change(within(dialog).getByLabelText("File"), { target: { files: [file] } });
  const form = within(await screen.findByRole("form", { name: "Confirm file" }));
  expect(form.getByText("Vanguard Personal Investor")).toBeInTheDocument();
  expect(form.getByText("••• 0001")).toBeInTheDocument();
  expect(form.getByText("03 Aug 2026 to 30 Sep 2026")).toBeInTheDocument();
  await form.findByRole("option", { name: "Sample Brokerage account" });
  expect(form.getByLabelText("Account")).toHaveValue("20"); // preselected from the reference
  expect(form.queryByLabelText(/Remember this reference/)).not.toBeInTheDocument();
  fireEvent.click(form.getByRole("button", { name: "Stage file" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/imports/5"));
  const post = calls.find((c) => c.method === "POST" && c.path === "/api/imports");
  expect(post?.search).toContain("account_id=20");
  expect(post?.search).toContain("file_name=sample_pi_trades.csv");
  expect(post?.search).toContain("save_identifier=false");
});

test("an unknown reference leaves the Account for the owner and offers to remember it", async () => {
  backend({
    imports: [],
    detect: { ...DETECTION, reference_masked: "••• 9999", matches: [] },
    staged: TRADE,
  });
  await renderAppAt("/imports");
  fireEvent.click(await screen.findByRole("button", { name: /Add file/ }));
  const file = new File(["x"], "other.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("File"), { target: { files: [file] } });
  const form = within(await screen.findByRole("form", { name: "Confirm file" }));
  await form.findByRole("option", { name: "Sample Brokerage account" });
  expect(form.getByLabelText("Account")).toHaveValue("");
  expect(form.getByRole("button", { name: "Stage file" })).toBeDisabled();
  fireEvent.change(form.getByLabelText("Account"), { target: { value: "20" } });
  fireEvent.click(form.getByLabelText(/Remember this reference/));
  fireEvent.click(form.getByRole("button", { name: "Stage file" }));
  await waitFor(() =>
    expect(calls.find((c) => c.path === "/api/imports" && c.method === "POST")?.search).toContain(
      "save_identifier=true",
    ),
  );
});

test("an unrecognised file shows a clear error beside the field and the Dialog stays open", async () => {
  backend({ imports: [], detectError: true });
  await renderAppAt("/imports");
  fireEvent.click(await screen.findByRole("button", { name: /Add file/ }));
  fireEvent.change(screen.getByLabelText("File"), {
    target: { files: [new File(["x"], "bank.csv", { type: "text/csv" })] },
  });
  expect(await screen.findByText("Finance360 does not recognise this file.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Add a file" })).toBeInTheDocument();
});

test("a file already imported says so and offers the existing Import", async () => {
  backend({ imports: [TRADE], detect: { ...DETECTION, duplicate_import_id: 5 }, staged: TRADE });
  await renderAppAt("/imports");
  fireEvent.click(await screen.findByRole("button", { name: /Add file/ }));
  fireEvent.change(screen.getByLabelText("File"), {
    target: { files: [new File(["x"], "again.csv", { type: "text/csv" })] },
  });
  const form = within(await screen.findByRole("form", { name: "Confirm file" }));
  expect(form.getByText(/already imported for this Account/)).toBeInTheDocument();
  expect(form.getByRole("button", { name: "Open existing import" })).toBeInTheDocument();
});

test("the detail page shows Stream, Coverage, the unmatched product and staged rows; a deep link to page 2 works", async () => {
  backend({ imports: [TRADE] });
  await renderAppAt("/imports/5");
  expect(await screen.findByRole("heading", { name: "sample_pi_trades.csv" })).toBeInTheDocument();
  expect(screen.getAllByText("Trade").length).toBeGreaterThan(0);
  expect(screen.getByText(/03 Aug 2026 to 30 Sep 2026/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sample Brokerage account" })).toHaveAttribute("href", "/accounts/20");
  expect(screen.getByText("Commit blocked")).toBeInTheDocument();
  const unmatched = within(screen.getByRole("table", { name: "Unmatched references" }));
  expect(unmatched.getByText("SNHX")).toBeInTheDocument();
  expect(unmatched.getByText("Sample New Horizons ETF")).toBeInTheDocument();
  const rows = await screen.findByRole("table", { name: "Staged rows" });
  const settlement = within(within(rows).getAllByRole("row")[4]);
  expect(settlement.getAllByText("Trade settlement").length).toBeGreaterThan(0);
  expect(settlement.getByText("-1,922.15")).toBeInTheDocument();
  expect(within(rows).getAllByText("Unmatched")).toHaveLength(1);
  expect(screen.getByText("30 rows · page 1 of 2")).toBeInTheDocument();
  expect(screen.getByText(/states no period/)).toBeInTheDocument();
});

test("page 2 of the rows is a URL, not component state", async () => {
  backend({ imports: [TRADE] });
  const { router } = await renderAppAt("/imports/5?page=2");
  expect(await screen.findByText("30 rows · page 2 of 2")).toBeInTheDocument();
  expect(calls.find((c) => c.path.endsWith("/rows"))?.search).toContain("offset=25");
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  await waitFor(() => expect(router.state.location.search).toEqual({}));
  expect(await screen.findByText("30 rows · page 1 of 2")).toBeInTheDocument();
});

test("matching the unresolved product to an existing Instrument clears the block", async () => {
  backend({ imports: [TRADE] });
  await renderAppAt("/imports/5");
  fireEvent.click(await screen.findByRole("row", { name: "Match SNHX" }));
  const form = within(await screen.findByRole("form", { name: "Match SNHX" }));
  expect(form.getByRole("button", { name: "Map reference" })).toBeDisabled();
  await form.findByRole("option", { name: "Sample Balanced Growth Fund" });
  fireEvent.change(form.getByLabelText("Instrument"), { target: { value: "3" } });
  fireEvent.click(form.getByRole("button", { name: "Map reference" }));
  await waitFor(() => expect(screen.queryByText("Commit blocked")).not.toBeInTheDocument());
  const post = calls.find((c) => c.path === "/api/imports/5/references/map");
  expect(post?.body).toEqual({ scheme: "ticker", ref: "SNHX", instrument_id: 3 });
  expect(screen.queryByRole("table", { name: "Unmatched references" })).not.toBeInTheDocument();
});

test("a new Instrument can be created for the reference, prefilled from the file", async () => {
  backend({ imports: [TRADE] });
  await renderAppAt("/imports/5");
  fireEvent.click(await screen.findByRole("row", { name: "Match SNHX" }));
  fireEvent.click(await screen.findByRole("button", { name: "New Instrument" }));
  const form = within(screen.getByRole("form", { name: "Match SNHX" }));
  expect(form.getByLabelText("Name")).toHaveValue("Sample New Horizons ETF");
  expect(form.getByLabelText("Kind")).toHaveValue("etf");
  fireEvent.click(form.getByRole("button", { name: "Create Instrument" }));
  await waitFor(() => expect(screen.queryByText("Commit blocked")).not.toBeInTheDocument());
  expect(calls.find((c) => c.path === "/api/imports/5/references/create")?.body).toEqual({
    scheme: "ticker",
    ref: "SNHX",
    name: "Sample New Horizons ETF",
    kind: "etf",
    currency: "AUD",
  });
});

test("discarding is in the overflow menu, confirms in an AlertDialog and Cancel discards nothing", async () => {
  backend({ imports: [TRADE] });
  await renderAppAt("/imports/5");
  expect(screen.queryByRole("button", { name: "Discard import" })).not.toBeInTheDocument(); // not in the page
  const openDialog = async () => {
    fireEvent.keyDown(await screen.findByRole("button", { name: "More actions" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Discard import" }));
    return screen.findByRole("alertdialog", { name: "Discard sample_pi_trades.csv?" });
  };
  fireEvent.click(within(await openDialog()).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.path.endsWith("/discard"))).toBe(false);
  fireEvent.click(within(await openDialog()).getByRole("button", { name: "Discard import" }));
  expect(await screen.findByText("Discarded")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/imports/5/discard")).toBe(true);
  expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();
});

test("an unknown Import id says so", async () => {
  backend({ imports: [TRADE] });
  await renderAppAt("/imports/9");
  expect(await screen.findByText("Import not found")).toBeInTheDocument();
});

test("the sidebar links to Imports", async () => {
  backend({ imports: [] });
  await renderAppAt("/imports");
  expect(screen.getByRole("link", { name: "Imports" })).toHaveAttribute("aria-current", "page");
});

// --- the Identifiers section on the Account detail page (issue #92) ---------------------------

const IDENT = {
  id: 7,
  kind: "vendor_account_id",
  masked_value: "••• 0001",
  institution: "Vanguard PI",
  secret: "SAMPLE-PI-0001",
};

test("identifiers are masked by default and an explicit reveal shows the value, hide hides it again", async () => {
  const seen = accountsBackend([everyday], { identifiers: [IDENT] });
  await renderAppAt("/accounts/10");
  const table = await screen.findByRole("table", { name: "Identifiers" });
  expect(within(table).getByText("••• 0001")).toBeInTheDocument();
  expect(within(table).getByText("Provider account number")).toBeInTheDocument();
  expect(screen.queryByText("SAMPLE-PI-0001")).not.toBeInTheDocument();
  expect(seen.some((c) => c.path.endsWith("/reveal"))).toBe(false);

  fireEvent.click(within(table).getByRole("button", { name: /^Reveal/ }));
  expect(await screen.findByText("SAMPLE-PI-0001")).toBeInTheDocument();
  expect(seen.filter((c) => c.method === "POST" && c.path === "/api/accounts/10/identifiers/7/reveal")).toHaveLength(1);
  fireEvent.click(within(table).getByRole("button", { name: /^Hide/ }));
  expect(screen.queryByText("SAMPLE-PI-0001")).not.toBeInTheDocument();
});

test("adding an identifier in a Dialog lists it masked; a rejected one stays open with the message", async () => {
  const seen = accountsBackend([everyday], { identifiers: [] });
  await renderAppAt("/accounts/10");
  expect(await screen.findByText(/No identifiers/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add identifier" }));
  const form = within(await screen.findByRole("form", { name: "Add identifier to Everyday account" }));
  fireEvent.click(form.getByRole("button", { name: "Add identifier" }));
  expect(await form.findByText("Enter the identifier.")).toBeInTheDocument();
  fireEvent.change(form.getByLabelText("Kind"), { target: { value: "bsb_account" } });
  fireEvent.change(form.getByLabelText(/^Identifier/), { target: { value: "062000 12345678" } });
  fireEvent.change(form.getByLabelText("Institution (optional)"), { target: { value: "Sample Bank" } });
  fireEvent.click(form.getByRole("button", { name: "Add identifier" }));
  const table = await screen.findByRole("table", { name: "Identifiers" });
  expect(within(table).getByText("••• 5678")).toBeInTheDocument();
  expect(screen.queryByText("062000 12345678")).not.toBeInTheDocument();
  expect(seen.filter((c) => c.method === "POST" && c.path === "/api/accounts/10/identifiers").at(-1)?.body).toEqual({
    kind: "bsb_account",
    value: "062000 12345678",
    institution: "Sample Bank",
  });
});

test("removing an identifier is destructive: overflow menu, then an AlertDialog naming it", async () => {
  const seen = accountsBackend([everyday], { identifiers: [IDENT] });
  await renderAppAt("/accounts/10");
  const trigger = await screen.findByRole("button", {
    name: "Actions for Provider account number ••• 0001",
  });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove identifier" }));
  const alert = await screen.findByRole("alertdialog");
  expect(within(alert).getByText(/no longer match Everyday account automatically/)).toBeInTheDocument();
  fireEvent.click(within(alert).getByRole("button", { name: "Remove identifier" }));
  await waitFor(() => expect(screen.queryByRole("table", { name: "Identifiers" })).not.toBeInTheDocument());
  expect(seen.some((c) => c.method === "DELETE" && c.path === "/api/accounts/10/identifiers/7")).toBe(true);
});

// --- commit, the staged diff and reconciliation (issues #94, #95 and #96) -----------------------

const READY: Imp = { ...TRADE, unresolved: [], unresolved_count: 0, commit_blocked: false };
const txn = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  stream: "trade",
  occurred_on: "2026-09-30",
  kind: "buy",
  instrument_id: 1,
  instrument_name: "Sample Broad Market ETF",
  quantity: "20",
  unit_price: "96.1075",
  unit_price_ccy: "AUD",
  fee: { amount: "9.50", amount_minor: 950, currency: "AUD" },
  amount: null,
  import_id: 4,
  file_name: "older.csv",
  row_no: 1,
  superseded_at: null,
  ...over,
});

test("a staged Import with every reference matched commits after a confirmation that names the consequence", async () => {
  backend({
    imports: [READY],
    diff: {
      ...CLEAN_DIFF,
      added: 28,
      removed: 2,
      unchanged: 0,
      warnings: [
        { code: "no_matching_cash", message: "2026-09-01 Buy of 50 Sample New Horizons ETF: no matching cash fee of -9.50 AUD." },
      ],
    },
    removed: { total: 2, items: [txn(11), txn(12)] },
  });
  await renderAppAt("/imports/5");
  const changes = within(await screen.findByLabelText("Changes"));
  expect(changes.getByText("28 added")).toBeInTheDocument();
  expect(changes.getByText("2 removed")).toBeInTheDocument();
  expect(changes.getByText("1 warning")).toBeInTheDocument();
  expect(changes.getByText(/Sample New Horizons ETF: no matching cash fee of -9.50 AUD/)).toBeInTheDocument();
  expect(changes.getByText(/do not block commit/)).toBeInTheDocument();
  expect(await changes.findByRole("table", { name: "Removed Transactions" })).toBeInTheDocument();

  // one primary action on the page, and committing always asks first
  expect(screen.getAllByRole("button", { name: "Commit import" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Commit import" }));
  const alert = await screen.findByRole("alertdialog", { name: "Commit sample_pi_trades.csv?" });
  expect(within(alert).getByText(/28 rows become Transactions on Sample Brokerage account/)).toBeInTheDocument();
  expect(within(alert).getByText(/2 current Transactions are superseded; they stay retrievable/)).toBeInTheDocument();
  expect(within(alert).getByText(/1 warning is still open/)).toBeInTheDocument();
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.path.endsWith("/commit"))).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Commit import" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Commit import" }));
  const result = within(await screen.findByLabelText("Commit result"));
  expect(result.getByText(/30 Transactions added, 2 superseded, 0 already there/)).toBeInTheDocument();
  expect(result.getByRole("link", { name: "View Sample Brokerage account Transactions" })).toHaveAttribute(
    "href",
    "/accounts/20?stream=trade",
  );
  expect(calls.filter((c) => c.method === "POST" && c.path === "/api/imports/5/commit")).toHaveLength(1);
  expect(screen.getByText("Committed", { selector: "span" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Commit import" })).not.toBeInTheDocument();
});

test("commit is disabled while a reference is unmatched, so nothing can be sent", async () => {
  backend({ imports: [TRADE], diff: { ...CLEAN_DIFF, commit_blocked: true } });
  await renderAppAt("/imports/5");
  expect(await screen.findByRole("button", { name: "Commit import" })).toBeDisabled();
});

test("a refused commit keeps the dialog open and says why", async () => {
  backend({ imports: [READY], commitError: true });
  await renderAppAt("/imports/5");
  const button = await screen.findByRole("button", { name: "Commit import" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Commit import" }));
  expect(await screen.findByText("This Import is committed.")).toBeInTheDocument();
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
});

test("staged rows say which would be added and which already exist", async () => {
  backend({ imports: [READY] });
  await renderAppAt("/imports/5");
  const rows = await screen.findByRole("table", { name: "Staged rows" });
  expect((await within(rows).findAllByText("Added")).length).toBeGreaterThan(0);
});

test("a clean diff shows no warnings panel and no removed table", async () => {
  backend({ imports: [READY] });
  await renderAppAt("/imports/5");
  const changes = within(await screen.findByLabelText("Changes"));
  expect(changes.getByText("0 removed")).toBeInTheDocument();
  expect(changes.queryByRole("status", { name: "Warnings" })).not.toBeInTheDocument();
  expect(changes.queryByRole("table")).not.toBeInTheDocument();
});

test("a commit that brought in an Instrument with no Price offers Fetch now, as a secondary action", async () => {
  backend({
    imports: [
      {
        ...READY,
        status: "committed",
        committed: {
          committed_at: "2026-10-20T03:00:00Z",
          added: 14,
          replaced: 0,
          unchanged: 0,
          instruments_without_price: 1,
          offer_fetch: true,
        },
      } as Imp,
    ],
  });
  await renderAppAt("/imports/5");
  const card = within(await screen.findByLabelText("Commit result"));
  expect(card.getByText(/1 Instrument in this Import has no Price yet/)).toBeInTheDocument();
  expect(card.getByRole("button", { name: /Fetch now|Fetching/ })).toBeInTheDocument();
});

test("no Fetch now is offered when nothing needs a Price or no provider is configured", async () => {
  backend({
    imports: [
      {
        ...READY,
        status: "committed",
        committed: {
          committed_at: "2026-10-20T03:00:00Z",
          added: 14,
          replaced: 0,
          unchanged: 0,
          instruments_without_price: 1,
          offer_fetch: false, // a Price is missing, but no provider is configured
        },
      } as Imp,
    ],
  });
  await renderAppAt("/imports/5");
  const card = within(await screen.findByLabelText("Commit result"));
  expect(card.queryByRole("button", { name: /Fetch/ })).not.toBeInTheDocument();
  expect(card.queryByText(/no Price yet/)).not.toBeInTheDocument();
});

test("a committed Import has no Changes panel and no commit action", async () => {
  backend({
    imports: [
      {
        ...READY,
        status: "committed",
        committed: { committed_at: "2026-10-20T03:00:00Z", added: 14, replaced: 0, unchanged: 0, instruments_without_price: 0, offer_fetch: false },
      } as Imp,
    ],
  });
  await renderAppAt("/imports/5");
  expect(await screen.findByLabelText("Commit result")).toBeInTheDocument();
  expect(screen.queryByLabelText("Changes")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Commit import" })).not.toBeInTheDocument();
  expect(calls.some((c) => c.path.endsWith("/diff"))).toBe(false);
});

const UNMAPPED: Imp = {
  ...TRADE,
  unresolved: [],
  unresolved_count: 0,
  unmapped_count: 3,
  unmapped_types: [{ raw_type: "Managed Fund Transaction", row_count: 3, choices: ["buy", "sell", "ignore"] }],
};

test("a New transaction types panel blocks commit; choosing a meaning and saving unblocks it", async () => {
  backend({ imports: [UNMAPPED] });
  await renderAppAt("/imports/5");
  await screen.findByRole("table", { name: "New transaction types" });
  expect(screen.getByText("Commit blocked")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Commit import" })).toBeDisabled();
  const line = within(screen.getByRole("row", { name: "Map Managed Fund Transaction" }));
  expect(line.getByText("3")).toBeInTheDocument();
  expect(line.getByRole("button", { name: "Save" })).toBeDisabled(); // nothing chosen, nothing guessed
  const choices = within(line.getByLabelText("Meaning of Managed Fund Transaction"))
    .getAllByRole("option")
    .map((o) => o.textContent);
  expect(choices).toEqual(["Choose�", "Buy", "Sell", "Ignore"]);
  fireEvent.change(line.getByLabelText("Meaning of Managed Fund Transaction"), { target: { value: "buy" } });
  fireEvent.click(line.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByText("Commit blocked")).not.toBeInTheDocument());
  expect(calls.find((c) => c.path === "/api/imports/5/types/map")?.body).toEqual({
    raw_type: "Managed Fund Transaction",
    kind: "buy",
  });
  expect(screen.queryByRole("table", { name: "New transaction types" })).not.toBeInTheDocument();
});

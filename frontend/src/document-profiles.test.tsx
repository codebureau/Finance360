import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const facts = {
  target: "tax.income_statement",
  target_title: "Tax income statement",
  fills: ["gross_payments", "tax_withheld"],
  sums: [{ total: "gross_payments", plus: ["gross_amount", "bonuses_commissions"] }],
  overrides_shipped: false,
  overridden: false,
  failure: null as string | null,
  created_at: null as string | null,
};
const shipped = {
  ...facts,
  id: null,
  key: "ato-income-statement",
  version: 1,
  title: "ATO income statement",
  source: "shipped",
  status: "shipped",
};
const proposed = {
  ...facts,
  id: 7,
  key: "sample-variant",
  version: 1,
  title: "Sample variant",
  source: "pasted",
  status: "proposed",
  created_at: "2026-11-08T01:00:00Z",
};
const broken = {
  ...facts,
  id: 9,
  key: "broken-one",
  version: 2,
  title: "broken-one",
  fills: [],
  sums: [],
  source: "pasted",
  status: "failed_to_load",
  failure: "The profile names an unknown target 'gone.form'.",
  created_at: "2026-11-07T01:00:00Z",
};
const detailOf = (summary: typeof proposed | typeof shipped, extra: object = {}) => ({
  ...summary,
  claim: ["Tax withheld"],
  sections: [],
  fields: [
    { key: "gross_payments", type: "money", labels: ["Total gross amount"], pair: "same_row", section: null, required: true, absent_means_zero: false, negative_ok: false, repeats: false, zero_in_sums: false },
    { key: "tax_withheld", type: "money", labels: ["PAYGW amount"], pair: "same_row", section: null, required: false, absent_means_zero: false, negative_ok: false, repeats: false, zero_in_sums: false },
  ],
  profile_json: '{\n  "key": "' + summary.key + '"\n}',
  test_snapshot: { outcome: "ok", fields: [{ key: "gross_payments", state: "found", page: 1 }], checks: [] },
  test_document: { id: 4, file_name: "sample_statement.pdf" },
  proposed_by: "login:1",
  activated_at: null,
  ended_at: null,
  ended_reason: null,
  ...extra,
});
const reading = {
  outcome: "ok",
  profile_key: "sample-variant",
  profile_version: 1,
  found: [{ key: "gross_payments", value: "84500.00", kind: "money", page: 1, how: "read" }],
  not_found: [{ key: "tax_withheld", reason: "label_not_found", required: false }],
  checks: [{ kind: "sum", status: "agree", fields: ["gross_payments"], message: "The parts add up." }],
};
const review = (also: string[] = ["ato-income-statement"]) => ({
  profile: proposed,
  reading,
  acceptable: true,
  also_claimed_by: also,
});

type Call = { method: string; path: string; body?: unknown };

function backend(opts: { review?: object; activateError?: object } = {}) {
  const calls: Call[] = [];
  let state = proposed.status;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const text = request.method === "POST" ? await request.text() : "";
      calls.push({ method: request.method, path, body: text ? JSON.parse(text) : undefined });
      if (path === "/api/household") return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/document-profiles") {
        if (request.method === "POST") return json(detailOf(proposed), 201);
        return json([shipped, { ...proposed, status: state }, broken]);
      }
      if (path === "/api/document-profiles/test") return json({ ...review(), profile: { ...proposed, status: "draft", id: null } });
      if (path === "/api/document-profiles/shipped/ato-income-statement") return json(detailOf(shipped));
      if (path === "/api/document-profiles/7") return json(detailOf(proposed, { status: state }));
      if (path === "/api/document-profiles/7/review") return json(opts.review ?? review());
      if (path === "/api/document-profiles/7/activate") {
        if (opts.activateError) return json(opts.activateError, 422);
        state = "active";
        return json({ profile: detailOf(proposed, { status: "active" }), also_claimed_by: ["ato-income-statement"] });
      }
      if (path === "/api/document-profiles/7/retire") {
        state = "retired";
        return json(detailOf(proposed, { status: "retired" }));
      }
      if (path === "/api/document-profiles/9") return json(detailOf(broken, { test_document: null, test_snapshot: null }));
      if (path === "/api/library/documents") {
        return json({
          items: [
            { id: 4, file_name: "sample_statement.pdf", media_type: "application/pdf", size_bytes: 100, sha256: "a".repeat(64), added_at: "2026-11-01T00:00:00Z", text_status: "extracted", link_count: 0 },
            { id: 5, file_name: "sample_scan.png", media_type: "image/png", size_bytes: 100, sha256: "b".repeat(64), added_at: "2026-11-01T00:00:00Z", text_status: "none", link_count: 0 },
          ],
          total: 2,
          limit: 200,
          offset: 0,
        });
      }
      return json({ message: `unexpected ${path}` }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("Settings links to Document profiles, which lists shipped and saved profiles with status", async () => {
  backend();
  await renderAppAt("/settings");
  const link = await screen.findByRole("link", { name: "Open Document profiles" });
  expect(link).toHaveAttribute("href", "/settings/document-profiles");

  await renderAppAt("/settings/document-profiles");
  const table = await screen.findByRole("table", { name: "Document profiles" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);
  expect(within(rows[0]).getByText("Shipped")).toBeInTheDocument();
  expect(within(rows[0]).getByRole("link", { name: /ATO income statement/ })).toHaveAttribute(
    "href",
    "/settings/document-profiles/shipped/ato-income-statement",
  );
  expect(within(rows[1]).getByText("Proposed")).toBeInTheDocument();
  expect(within(rows[1]).getByRole("link", { name: /Sample variant/ })).toHaveAttribute("href", "/settings/document-profiles/7");
  expect(within(rows[1]).getByText(/2 fields · 1 check/)).toBeInTheDocument();
  expect(within(rows[2]).getByText("Failed to load")).toBeInTheDocument();
  expect(within(rows[2]).getByRole("alert")).toHaveTextContent("unknown target 'gone.form'");
  expect(screen.getByRole("link", { name: "Paste a profile" })).toHaveAttribute("href", "/settings/document-profiles/new");
});

test("a shipped profile is read-only: its fields, checks and JSON, and no Activate or Retire", async () => {
  backend();
  await renderAppAt("/settings/document-profiles/shipped/ato-income-statement");
  expect(await screen.findByRole("heading", { name: "ATO income statement" })).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Fields it fills" })).toHaveTextContent("Total gross amount");
  expect(screen.getByRole("list", { name: "Checks" })).toBeInTheDocument();
  expect(screen.getByLabelText("Profile JSON")).toHaveTextContent("ato-income-statement");
  expect(screen.queryByRole("button", { name: "Activate profile" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();
});

test("a proposed profile is re-run live beside its document and activated, with the double claim shown", async () => {
  const calls = backend();
  await renderAppAt("/settings/document-profiles/7");
  const reading_ = await screen.findByTestId("reading");
  expect(within(reading_).getByRole("table", { name: "Found in the document" })).toHaveTextContent("84,500.00");
  expect(within(reading_).getByText("Reads")).toBeInTheDocument();
  expect(within(reading_).getByRole("list", { name: "Not found" })).toHaveTextContent("PAYGW amount not found");
  expect(screen.getByTestId("double-claim")).toHaveTextContent("ato-income-statement");
  expect(screen.getByTitle("sample_statement.pdf")).toHaveAttribute("src", "/api/library/documents/4/file");

  fireEvent.click(screen.getByRole("button", { name: "Activate profile" }));
  expect(await screen.findByText(/Activated\. It reads documents from now on/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/document-profiles/7/activate")).toBe(true);
});

test("Activate is off when the profile no longer reads its document", async () => {
  backend({ review: { ...review(), acceptable: false, reading: { ...reading, outcome: "unrecognised", found: [], not_found: [], checks: [] } } });
  await renderAppAt("/settings/document-profiles/7");
  expect(await screen.findByText("Not recognised")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Activate profile" })).toBeDisabled();
});

test("Retire asks first, naming the profile, and then retires it", async () => {
  const calls = backend();
  await renderAppAt("/settings/document-profiles/7");
  await screen.findByTestId("reading");
  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Retire profile" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Retire Sample variant v1?");
  expect(calls.some((c) => c.path.endsWith("/retire"))).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retire profile" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/document-profiles/7/retire")).toBe(true));
});

test("a profile that failed to load says why and offers no Activate", async () => {
  backend();
  await renderAppAt("/settings/document-profiles/9");
  expect(await screen.findByText(/Failed to load: The profile names an unknown target/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Activate profile" })).not.toBeInTheDocument();
});

test("paste-and-test shows the reading beside the document, and Save appears only once it reads", async () => {
  const calls = backend();
  const { router } = await renderAppAt("/settings/document-profiles/new");
  const select = await screen.findByLabelText("Library document to try it on");
  // The scan has no text, so it is not offered.
  expect(within(select).queryByRole("option", { name: "sample_scan.png" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Test profile" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save as proposed profile" })).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Profile JSON"), { target: { value: '{"key": "sample-variant"}' } });
  fireEvent.change(select, { target: { value: "4" } });
  expect(screen.getByTitle("sample_statement.pdf")).toHaveAttribute("src", "/api/library/documents/4/file");
  fireEvent.click(screen.getByRole("button", { name: "Test profile" }));
  const result = await screen.findByRole("region", { name: "Test result" });
  expect(within(result).getByTestId("reading")).toHaveTextContent("84,500.00");
  expect(calls.find((c) => c.path === "/api/document-profiles/test")?.body).toEqual({
    profile_json: '{"key": "sample-variant"}',
    document_id: 4,
  });
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/document-profiles")).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Save as proposed profile" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/settings/document-profiles/7"));
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/document-profiles")?.body).toEqual({
    profile_json: '{"key": "sample-variant"}',
    document_id: 4,
  });
});

test("editing the draft after a test clears the result, so an old test cannot be saved", async () => {
  backend();
  await renderAppAt("/settings/document-profiles/new");
  const select = await screen.findByLabelText("Library document to try it on");
  fireEvent.change(screen.getByLabelText("Profile JSON"), { target: { value: "{}" } });
  fireEvent.change(select, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Test profile" }));
  await screen.findByRole("region", { name: "Test result" });
  fireEvent.change(screen.getByLabelText("Profile JSON"), { target: { value: '{"key": "x"}' } });
  expect(screen.queryByRole("region", { name: "Test result" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save as proposed profile" })).not.toBeInTheDocument();
});

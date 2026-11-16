import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { parseLibrarySearch } from "@/lib/library";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const base = {
  sha256: "a".repeat(64),
  text_status: "extracted",
  added_at: "2026-11-03T01:02:03Z",
};
const receipt = {
  ...base,
  id: 3,
  file_name: "sample_donation_receipt.txt",
  media_type: "text/plain",
  size_bytes: 150,
  link_count: 1,
};
const savings = {
  ...base,
  id: 4,
  file_name: "sample_savings_statement.txt",
  media_type: "text/plain",
  size_bytes: 233,
  link_count: 0,
};
const scan = {
  ...base,
  id: 5,
  file_name: "sample_scan.png",
  media_type: "image/png",
  size_bytes: 2048,
  text_status: "none",
  link_count: 0,
};
const linkOf = (id: number, documentId: number, label: string, removed = false) => ({
  id,
  document_id: documentId,
  target_kind: "tax_record",
  kind_title: "Tax record",
  target_id: 5,
  label,
  route: "/tax/records/5",
  removed,
  added_at: "2026-11-04T00:00:00Z",
});

type Call = { method: string; path: string; params: URLSearchParams; body?: unknown };

/** A Library that filters like the server does, so the screen's request is what is under test. */
function fakeBackend(
  opts: { documents?: (typeof receipt)[]; links?: Record<number, ReturnType<typeof linkOf>[]>; deleteError?: Response } = {},
) {
  const calls: Call[] = [];
  let documents = opts.documents ?? [receipt, savings, scan];
  let links = opts.links ?? { 3: [linkOf(30, 3, "Donation to Sample Charity")] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const text = request.method === "POST" ? await request.text() : "";
      calls.push({ method: request.method, path, params: url.searchParams, body: text ? safe(text) : undefined });
      if (path === "/api/household") return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/library/check") return json({ checked: documents.length, problems: [] });
      if (path === "/api/library/kinds") return json([{ key: "tax_record", title: "Tax record" }]);
      if (path === "/api/library/documents" && request.method === "GET") {
        const p = url.searchParams;
        const q = (p.get("q") ?? "").toLowerCase();
        const items = documents
          .filter((d) => !q || d.file_name.toLowerCase().includes(q))
          .filter((d) => !p.get("media_type") || d.media_type === p.get("media_type"))
          .filter((d) => !p.get("linked_kind") || (links[d.id]?.length ?? 0) > 0)
          .filter((d) => p.get("unlinked") !== "true" || (links[d.id]?.length ?? 0) === 0)
          .map((d) => ({ ...d, link_count: links[d.id]?.length ?? 0 }));
        const limit = Number(p.get("limit") ?? 50);
        const offset = Number(p.get("offset") ?? 0);
        return json({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
      }
      if (path === "/api/library/documents" && request.method === "POST") {
        return json({ document: { ...scan, id: 9, file_name: "new.png" }, already_in_library: false }, 201);
      }
      const one = path.match(/^\/api\/library\/documents\/(\d+)$/);
      if (one && request.method === "GET") {
        const doc = documents.find((d) => d.id === Number(one[1]));
        if (!doc) return json({ message: "Document 99 is not in the Library." }, 404);
        const mine = links[doc.id] ?? [];
        return json({ ...doc, link_count: mine.length, links: mine });
      }
      if (one && request.method === "DELETE") {
        if (opts.deleteError) return opts.deleteError;
        documents = documents.filter((d) => d.id !== Number(one[1]));
        return new Response(null, { status: 204 });
      }
      const unlink = path.match(/^\/api\/library\/links\/(\d+)$/);
      if (unlink && request.method === "DELETE") {
        links = Object.fromEntries(
          Object.entries(links).map(([k, v]) => [k, v.filter((l) => l.id !== Number(unlink[1]))]),
        );
        return new Response(null, { status: 204 });
      }
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

const safe = (text: string) => {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
};

afterEach(() => vi.unstubAllGlobals());

test("the Library is in the sidebar under a heading that links to its Guide page", async () => {
  fakeBackend();
  await renderAppAt("/library");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Library" })).toHaveAttribute("href", "/library");
  expect(within(nav).getByRole("link", { name: "Guide: Your documents" })).toHaveAttribute("href", "/guide/library");
});

test("the list shows each document with its type, size, date and what it is linked to, and a count", async () => {
  fakeBackend();
  await renderAppAt("/library");
  const table = await screen.findByRole("table", { name: "Library documents" });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(4); // header + 3
  expect(within(table).getByRole("link", { name: "sample_donation_receipt.txt" })).toHaveAttribute("href", "/library/3");
  expect(rows[1]).toHaveTextContent("1 record");
  expect(within(rows[2]).getByText("Unlinked")).toBeInTheDocument();
  expect(rows[3]).toHaveTextContent("PNG image");
  expect(screen.getByTestId("library-count")).toHaveTextContent("3 documents");
});

test("an empty Library says how to fill it, and a filter with no match says so", async () => {
  fakeBackend({ documents: [] });
  await renderAppAt("/library");
  expect(await screen.findByTestId("library-count")).toHaveTextContent("The Library is empty");
  vi.unstubAllGlobals();
  fakeBackend();
  await renderAppAt("/library?q=nothing-like-this");
  await waitFor(() => expect(screen.getAllByTestId("library-count").at(-1)).toHaveTextContent("No document matches"));
});

test("search is sent to the server and kept in the URL", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/library");
  await screen.findByRole("table", { name: "Library documents" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: " savings " } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "savings" }));
  const table = await screen.findByRole("table", { name: "Library documents" });
  await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
  expect(within(table).getByText("sample_savings_statement.txt")).toBeInTheDocument();
  expect(screen.getByTestId("library-count")).toHaveTextContent("1 document match");
  expect(calls.some((c) => c.path === "/api/library/documents" && c.params.get("q") === "savings")).toBe(true);
});

test("the Type filter, the Linked to filter and Unlinked are in the URL and narrow the list", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/library");
  await screen.findByRole("table", { name: "Library documents" });

  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "image/png" } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ type: "image/png" }));
  const table = () => screen.getByRole("table", { name: "Library documents" });
  await waitFor(() => expect(within(table()).getAllByRole("row")).toHaveLength(2));
  expect(within(table()).getByText("sample_scan.png")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Linked to"), { target: { value: "unlinked" } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ unlinked: true }));
  await waitFor(() => expect(within(table()).getAllByRole("row")).toHaveLength(3));
  expect(within(table()).queryByText("sample_donation_receipt.txt")).not.toBeInTheDocument();
  expect(calls.some((c) => c.params.get("unlinked") === "true")).toBe(true);

  fireEvent.change(screen.getByLabelText("Linked to"), { target: { value: "tax_record" } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ kind: "tax_record" }));
  expect(router.state.location.search).not.toHaveProperty("unlinked", true);
  await waitFor(() => expect(within(table()).getAllByRole("row")).toHaveLength(2));
  expect(within(table()).getByText("sample_donation_receipt.txt")).toBeInTheDocument();
  expect(calls.some((c) => c.params.get("linked_kind") === "tax_record" && c.params.get("unlinked") === null)).toBe(true);
});

test("filters in the address are applied on arrival and shown in the controls", async () => {
  fakeBackend();
  await renderAppAt("/library?unlinked=1&type=text/plain");
  const table = await screen.findByRole("table", { name: "Library documents" });
  await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
  expect(within(table).getByText("sample_savings_statement.txt")).toBeInTheDocument();
  expect(screen.getByLabelText("Linked to")).toHaveValue("unlinked");
  expect(screen.getByLabelText("Type")).toHaveValue("text/plain");
});

test("malformed search params are dropped, and unlinked wins over a kind", () => {
  expect(parseLibrarySearch({ type: "text/html", page: "abc", q: "  ", unlinked: "yes" })).toEqual({
    q: undefined,
    type: undefined,
    kind: undefined,
    unlinked: undefined,
    page: undefined,
  });
  expect(parseLibrarySearch({ unlinked: "1", kind: "tax_record", page: "3" })).toMatchObject({
    unlinked: true,
    kind: undefined,
    page: 3,
  });
});

test("a long list pages, asking the server for the next page", async () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ ...savings, id: 100 + i, file_name: `doc_${String(i).padStart(2, "0")}.txt` }));
  const calls = fakeBackend({ documents: many, links: {} });
  const { router } = await renderAppAt("/library");
  expect(await screen.findByText("Page 1 of 2")).toBeInTheDocument();
  expect(screen.getByTestId("library-count")).toHaveTextContent("30 documents");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ page: 2 }));
  expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/library/documents" && c.params.get("offset") === "25")).toBe(true);
});

test("Add documents files each chosen file and reports what was added", async () => {
  const calls = fakeBackend();
  await renderAppAt("/library");
  await screen.findByRole("table", { name: "Library documents" });
  const file = new File([new Uint8Array([137, 80, 78, 71])], "new.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Choose files to add"), { target: { files: [file] } });
  expect(await screen.findByRole("status")).toHaveTextContent("Added 'new.png' to the Library.");
  expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/library/documents"]);
});

test("the document view shows its details and every record it is linked to, each a link through", async () => {
  fakeBackend({
    links: {
      3: [linkOf(30, 3, "Donation to Sample Charity"), { ...linkOf(31, 3, "Gift record", true), target_id: 6, route: null as never }],
    },
  });
  await renderAppAt("/library/3");
  expect(await screen.findByRole("heading", { name: "sample_donation_receipt.txt" })).toBeInTheDocument();
  expect(screen.getByText("Text read, so it can be found by search")).toBeInTheDocument();
  const open = screen.getByRole("link", { name: "Open" });
  expect(open).toHaveAttribute("href", "/api/library/documents/3/file");
  expect(open).toHaveAttribute("target", "_blank");
  const list = screen.getByRole("list", { name: "Records this document is linked to" });
  expect(within(list).getByRole("link", { name: "Donation to Sample Charity" })).toHaveAttribute("href", "/tax/records/5");
  expect(within(list).getAllByText("Tax record")).toHaveLength(2);
  expect(within(list).getByText("Gift record")).toBeInTheDocument(); // no route: shown, not linked
  expect(within(list).getByText("Removed")).toBeInTheDocument();
});

test("the document view embeds the stored file from the authenticated file route", async () => {
  fakeBackend();
  await renderAppAt("/library/3");
  const frame = await screen.findByTitle("sample_donation_receipt.txt");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame).toHaveAttribute("src", "/api/library/documents/3/file");
});

test("a linked document cannot be deleted, and the page says why", async () => {
  const calls = fakeBackend();
  await renderAppAt("/library/3");
  await screen.findByRole("heading", { name: "sample_donation_receipt.txt" });
  expect(screen.getByTestId("delete-hint")).toHaveTextContent("A document linked to a record cannot be deleted");
  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  const item = await screen.findByRole("menuitem", { name: "Delete document" });
  expect(item).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(item);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
});

test("unlinking from the document view is confirmed, keeps the document, and then Delete is allowed", async () => {
  const calls = fakeBackend();
  await renderAppAt("/library/3");
  fireEvent.keyDown(await screen.findByRole("button", { name: "Actions for Donation to Sample Charity" }), {
    key: "Enter",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Unlink document" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Unlink sample_donation_receipt.txt from Donation to Sample Charity?");
  expect(dialog).toHaveTextContent("The document stays in the Library");
  fireEvent.click(within(dialog).getByRole("button", { name: "Unlink document" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/library/links/30")).toBe(true));
  expect(await screen.findByText(/Not linked to any record/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "sample_donation_receipt.txt" })).toBeInTheDocument(); // still there
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/library/documents/3")).toBe(false);

  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  expect(await screen.findByRole("menuitem", { name: "Delete document" })).not.toHaveAttribute("aria-disabled", "true");
});

test("deleting an unlinked document is confirmed, deletes it and returns to the list", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/library/4");
  await screen.findByRole("heading", { name: "sample_savings_statement.txt" });
  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete document" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Delete sample_savings_statement.txt?");
  expect(dialog).toHaveTextContent("for good");
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete document" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/library"));
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/library/documents/4")).toBe(true);
  const table = await screen.findByRole("table", { name: "Library documents" });
  expect(within(table).queryByText("sample_savings_statement.txt")).not.toBeInTheDocument();
});

test("a delete the server refuses shows its message and stays open", async () => {
  fakeBackend({
    deleteError: json({ message: "'sample_savings_statement.txt' is linked to 1 record. Unlink it from them first." }, 409),
  });
  await renderAppAt("/library/4");
  await screen.findByRole("heading", { name: "sample_savings_statement.txt" });
  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete document" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete document" }));
  expect(await within(dialog).findByText(/is linked to 1 record/)).toBeInTheDocument();
});

test("an image's view says it has no text, and a document that is not there says so", async () => {
  fakeBackend();
  await renderAppAt("/library/5");
  expect(await screen.findByText("No text to read (an image or a scan)")).toBeInTheDocument();
  vi.unstubAllGlobals();
  fakeBackend();
  await renderAppAt("/library/99");
  expect(await screen.findByRole("alert")).toHaveTextContent("is not in the Library");
});

test("the Audit trail can be filtered to Library documents and Document links", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ items: [], total: 0, limit: 25, offset: 0 })));
  await renderAppAt("/audit");
  const select = await screen.findByLabelText(/^(Kind|Type|Entity)/);
  const options = within(select).getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(expect.arrayContaining(["Library document", "Document link"]));
});

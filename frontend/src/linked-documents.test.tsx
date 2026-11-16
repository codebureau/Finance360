import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { fileMediaType, fileSizeText } from "@/lib/library";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const donation = {
  id: 5,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "line",
  from_on: "2025-12-15",
  to_on: null,
  description: "Donation to Sample Charity",
  notes: null,
  label_key: "gifts_donations",
  label_name: "Gifts and donations",
  return_item: "D9",
  kind: "money",
  amount: "250.00",
  work_use_percent: null,
  counted: "250.00",
  value: null,
};
const receipt = {
  id: 3,
  file_name: "sample_donation_receipt.txt",
  media_type: "text/plain",
  size_bytes: 150,
  sha256: "a".repeat(64),
  added_at: "2026-11-03T01:02:03Z",
  text_status: "extracted",
  link_count: 1,
};
const photo = { ...receipt, id: 4, file_name: "sample_photo.png", media_type: "image/png", size_bytes: 2048 };
const statement = { ...receipt, id: 8, file_name: "sample_statement.pdf", media_type: "application/pdf", link_count: 0 };

const linkTo = (document: { id: number }, linkId: number) => ({
  id: linkId,
  document_id: document.id,
  target_kind: "tax_record",
  kind_title: "Tax record",
  target_id: 5,
  label: "Donation to Sample Charity",
  route: "/tax/records/5",
  removed: false,
  added_at: "2026-11-03T01:02:03Z",
});

type Doc = typeof receipt;
type Call = { method: string; path: string; search: string; contentType: string | null; bytes?: number; body?: unknown };

function fakeBackend(
  opts: {
    linked?: Doc[];
    library?: Doc[];
    problems?: unknown[];
    addError?: Response;
    /** The Library already holds the file being added, as this document. */
    existing?: Doc;
  } = {},
) {
  const calls: Call[] = [];
  let linked = opts.linked ?? [receipt];
  let library = opts.library ?? [receipt, photo, statement];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const isBody = request.method === "POST";
      const raw = isBody ? await request.arrayBuffer() : undefined;
      const text = raw && request.headers.get("content-type")?.includes("json") ? new TextDecoder().decode(raw) : "";
      calls.push({
        method: request.method,
        path,
        search: url.search,
        contentType: request.headers.get("content-type"),
        bytes: raw?.byteLength,
        body: text ? JSON.parse(text) : undefined,
      });
      if (path === "/api/household") return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/records/5") return json(donation);
      if (path === "/api/tax/records") return json([donation]);
      if (path === "/api/tax/setup") return json({ rule_pack_version: null, years: [], sections: [], labels: [] });
      if (path === "/api/library/check") return json({ checked: linked.length, problems: opts.problems ?? [] });
      if (path === "/api/library/documents" && request.method === "POST") {
        if (opts.addError) return opts.addError;
        if (opts.existing) return json({ document: opts.existing, already_in_library: true }, 200);
        library = [...library, { ...photo, id: 9, file_name: "sample_new.png" }];
        return json({ document: { ...photo, id: 9, file_name: "sample_new.png", link_count: 0 }, already_in_library: false }, 201);
      }
      if (path === "/api/library/documents") {
        const q = url.searchParams.get("q") ?? "";
        const items = library.filter((d) => d.file_name.includes(q));
        return json({ items, total: items.length, limit: 20, offset: 0 });
      }
      if (path === "/api/library/links" && request.method === "POST") {
        const body = JSON.parse(text) as { document_id: number };
        const doc = [...library, opts.existing].find((d) => d?.id === body.document_id) ?? photo;
        if (!linked.some((d) => d.id === doc.id)) linked = [...linked, doc];
        return json(linkTo(doc, 70 + doc.id), 201);
      }
      if (path === "/api/library/links") {
        return json(linked.map((d) => ({ link: linkTo(d, 70 + d.id), document: d })));
      }
      const unlink = path.match(/^\/api\/library\/links\/(\d+)$/);
      if (unlink && request.method === "DELETE") {
        const documentId = Number(unlink[1]) - 70;
        linked = linked.filter((d) => d.id !== documentId);
        return new Response(null, { status: 204 });
      }
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("the record's Documents list each document, linking through to its Library view, with Open and size", async () => {
  fakeBackend({ linked: [receipt, photo] });
  await renderAppAt("/tax/records/5");
  const list = await screen.findByRole("list", { name: "Linked documents" });
  const name = within(list).getByRole("link", { name: "sample_donation_receipt.txt" });
  expect(name).toHaveAttribute("href", "/library/3");
  const open = within(list).getByRole("link", { name: "Open sample_donation_receipt.txt" });
  expect(open).toHaveAttribute("href", "/api/library/documents/3/file");
  expect(open).toHaveAttribute("target", "_blank");
  expect(list).toHaveTextContent("Text · 150 bytes · added 2026-11-03");
  expect(list).toHaveTextContent("PNG image · 2.0 KB");
  expect(screen.getByRole("heading", { name: "Documents" })).toBeInTheDocument();
});

test("a record with no documents says what can be added or linked", async () => {
  fakeBackend({ linked: [] });
  await renderAppAt("/tax/records/5");
  const hint = await screen.findByText(/No documents linked/);
  expect(hint).toHaveTextContent("up to 10 MB");
  expect(hint).toHaveTextContent("already in the Library");
});

test("the menu offers a download from the file route", async () => {
  fakeBackend();
  await renderAppAt("/tax/records/5");
  fireEvent.keyDown(await screen.findByRole("button", { name: "Actions for sample_donation_receipt.txt" }), {
    key: "Enter",
  });
  expect(await screen.findByRole("menuitem", { name: "Download" })).toHaveAttribute(
    "href",
    "/api/library/documents/3/file?download=true",
  );
});

test("a missing or changed file is flagged and is no longer opened", async () => {
  fakeBackend({
    linked: [receipt, photo],
    problems: [
      { document_id: 3, file_name: receipt.file_name, problem: "missing", links: [{ target_kind: "tax_record", target_id: 5 }] },
      { document_id: 4, file_name: photo.file_name, problem: "changed", links: [{ target_kind: "tax_record", target_id: 5 }] },
    ],
  });
  await renderAppAt("/tax/records/5");
  const list = await screen.findByRole("list", { name: "Linked documents" });
  expect(await within(list).findByText("File missing")).toBeInTheDocument();
  expect(within(list).getByText("File changed")).toBeInTheDocument();
  expect(within(list).queryByRole("link", { name: /^Open / })).not.toBeInTheDocument();
});

test("the register warns of a bad file anywhere and links to its Library view", async () => {
  fakeBackend({
    problems: [{ document_id: 3, file_name: receipt.file_name, problem: "missing", links: [{ target_kind: "tax_record", target_id: 5 }] }],
  });
  await renderAppAt("/tax/records");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("A document needs attention");
  expect(alert).toHaveTextContent("File missing");
  expect(alert).toHaveTextContent("sample_donation_receipt.txt");
  expect(within(alert).getByRole("link", { name: "Open document 3" })).toHaveAttribute("href", "/library/3");
});

test("the register says nothing while every file is fine", async () => {
  fakeBackend();
  await renderAppAt("/tax/records");
  await screen.findByRole("table", { name: "Tax records" });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("adding files it in the Library as the raw body with its media type, then links it to the record", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  const file = new File([new Uint8Array([137, 80, 78, 71])], "sample_new.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Choose a file to add"), { target: { files: [file] } });
  expect(await screen.findByRole("link", { name: "sample_new.png" })).toBeInTheDocument();
  const posts = calls.filter((c) => c.method === "POST");
  expect(posts.map((c) => c.path)).toEqual(["/api/library/documents", "/api/library/links"]);
  expect(posts[0].search).toBe("?file_name=sample_new.png");
  expect(posts[0].contentType).toBe("image/png");
  expect(posts[0].bytes).toBe(4);
  expect(posts[1].body).toEqual({ document_id: 9, target_kind: "tax_record", target_id: 5 });
  expect(screen.queryByRole("status")).not.toBeInTheDocument(); // a new document needs no note
});

test("adding a file the Library already holds links the existing document and says so", async () => {
  const calls = fakeBackend({ existing: statement });
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  const file = new File(["%PDF-1.4"], "renamed_copy.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a file to add"), { target: { files: [file] } });
  const note = await screen.findByRole("status");
  expect(note).toHaveTextContent("'renamed_copy.pdf' was already in the Library as 'sample_statement.pdf'");
  expect(note).toHaveTextContent("that document is linked here");
  expect(await screen.findByRole("link", { name: "sample_statement.pdf" })).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/library/links" && c.method === "POST")?.body).toMatchObject({
    document_id: 8,
  });
});

test("a refused file shows the message and links nothing", async () => {
  const calls = fakeBackend({
    addError: json({ errors: [{ field: "file", message: "The file is larger than 10 MB." }] }, 422),
  });
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  const file = new File(["x"], "big.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("Choose a file to add"), { target: { files: [file] } });
  expect(await screen.findByText("The file is larger than 10 MB.")).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/api/library/links" && c.method === "POST")).toBe(false);
});

test("a file type we do not take is refused before anything is sent", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  const file = new File(["GIF89a"], "sample.gif", { type: "image/gif" });
  fireEvent.change(screen.getByLabelText("Choose a file to add"), { target: { files: [file] } });
  expect(await screen.findByText("Add a PDF, PNG image, JPEG image, text or CSV file.")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "POST")).toBe(false);
});

test("Link existing searches the Library by name, marks what is linked, and links the chosen document", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  fireEvent.click(screen.getByRole("button", { name: "Link existing" }));
  const dialog = await screen.findByRole("dialog");
  const picker = await within(dialog).findByRole("list", { name: "Library documents" });
  expect(within(picker).getAllByRole("listitem")).toHaveLength(3);
  expect(within(picker).getByText("Linked")).toBeInTheDocument(); // the receipt is already here
  expect(within(picker).queryByRole("button", { name: "Link sample_donation_receipt.txt" })).not.toBeInTheDocument();

  fireEvent.change(within(dialog).getByLabelText("Search the Library"), { target: { value: "statement" } });
  await waitFor(() => expect(within(dialog).getAllByRole("listitem")).toHaveLength(1));
  expect(calls.some((c) => c.path === "/api/library/documents" && c.search.includes("q=statement"))).toBe(true);

  fireEvent.click(within(dialog).getByRole("button", { name: "Link sample_statement.pdf" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(calls.find((c) => c.path === "/api/library/links" && c.method === "POST")?.body).toEqual({
    document_id: 8,
    target_kind: "tax_record",
    target_id: 5,
  });
  expect(await screen.findByRole("link", { name: "sample_statement.pdf" })).toBeInTheDocument();
});

test("unlinking is confirmed, names the document and says it stays in the Library", async () => {
  const calls = fakeBackend();
  await renderAppAt("/tax/records/5");
  fireEvent.keyDown(await screen.findByRole("button", { name: "Actions for sample_donation_receipt.txt" }), {
    key: "Enter",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Unlink document" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Unlink sample_donation_receipt.txt?");
  expect(dialog).toHaveTextContent("The document stays in the Library");
  expect(calls.some((c) => c.method === "DELETE")).toBe(false); // nothing happens until confirmed
  fireEvent.click(within(dialog).getByRole("button", { name: "Unlink document" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/library/links/73")).toBe(true));
  expect(await screen.findByText(/No documents linked/)).toBeInTheDocument();
  // only the link went: nothing asked for the document itself to be deleted
  expect(calls.some((c) => c.method === "DELETE" && c.path.startsWith("/api/library/documents"))).toBe(false);
});

test("deleting a record with documents says they are unlinked but stay in the Library", async () => {
  fakeBackend();
  await renderAppAt("/tax/records/5");
  await screen.findByRole("list", { name: "Linked documents" });
  fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Tax record" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Its document is unlinked and stays in the Library");
  expect(dialog).toHaveTextContent("with the documents that were linked");
});

test("small helpers", () => {
  expect(fileMediaType(new File([""], "a.PDF", { type: "" }))).toBe("application/pdf");
  expect(fileMediaType(new File([""], "a.jpg", { type: "image/jpeg" }))).toBe("image/jpeg");
  expect(fileMediaType(new File([""], "a.gif", { type: "image/gif" }))).toBeNull();
  expect(fileMediaType(new File([""], "noextension", { type: "" }))).toBeNull();
  expect([fileSizeText(999), fileSizeText(1500), fileSizeText(2_500_000)]).toEqual(["999 bytes", "1.5 KB", "2.5 MB"]);
});

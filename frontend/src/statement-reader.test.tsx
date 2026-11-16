import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};
const setup = {
  rule_pack_version: "x",
  years: [],
  sections: [],
  labels: [],
};

const found = (key: string, value: string, kind = "money", how = "read") => ({
  key,
  value,
  kind,
  page: 1,
  how,
  parts: [] as { page: number; value: string }[],
});

const READ_OK = {
  outcome: "ok",
  profile_key: "ato-income-statement",
  profile_version: 1,
  found: [
    found("period", "2097", "fy"),
    found("gross_amount", "80000.00"),
    found("bonuses_commissions", "5000.00"),
    found("leave_payment_type", "Annual leave", "text"),
    found("salary_sacrifice_amount", "-3000.00"),
    found("gross_payments", "82000.00"),
    found("tax_withheld", "18250.00"),
    found("lump_sum_a", "0.00", "money", "absent"),
  ],
  not_found: [],
  checks: [
    {
      kind: "sum",
      status: "agree",
      fields: ["gross_payments", "gross_amount"],
      message: "",
      assumed_zero: [] as string[],
    },
  ],
};
const READ_SUMS_DIFFER = {
  ...READ_OK,
  outcome: "warnings",
  checks: [
    {
      kind: "sum",
      status: "disagree",
      fields: [
        "gross_payments",
        "gross_amount",
        "bonuses_commissions",
        "salary_sacrifice_amount",
      ],
      message: "The parts do not add up to the total.",
      assumed_zero: [] as string[],
    },
  ],
  not_found: [{ key: "employer_super", reason: "label_not_found", required: false }],
};
const UNRECOGNISED = {
  outcome: "unrecognised",
  profile_key: null,
  profile_version: null,
  found: [],
  not_found: [],
  checks: [],
};
const NEEDS_REPAIR = {
  outcome: "needs_repair",
  profile_key: "ato-income-statement",
  profile_version: 2,
  found: [found("period", "2097", "fy"), found("gross_payments", "82000.00")],
  not_found: [{ key: "tax_withheld", reason: "label_not_found", required: true }],
  checks: [],
};

type Call = {
  method: string;
  path: string;
  search: string;
  contentType: string | null;
  bytes?: number;
  json?: Record<string, unknown>;
};

function fakeBackend(
  opts: {
    reading?: unknown;
    readError?: Response;
    attachFailures?: number;
  } = {},
) {
  const calls: Call[] = [];
  let attachFailures = opts.attachFailures ?? 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const contentType = request.headers.get("content-type");
      const isJson = contentType?.includes("json");
      const call: Call = {
        method: request.method,
        path,
        search: url.search,
        contentType,
      };
      if (request.method === "POST" || request.method === "PUT") {
        if (isJson) call.json = await request.json();
        else call.bytes = (await request.arrayBuffer()).byteLength;
      }
      calls.push(call);
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/income-reconciliation") {
        return json({
          financial_year: 2097,
          financial_year_label: "2096-97",
          members: [],
          household: null,
        });
      }
      if (path === "/api/library/check") return json({ checked: 0, problems: [] });
      if (path === "/api/documents/read") {
        return opts.readError ?? json(opts.reading ?? READ_OK);
      }
      if (path === "/api/tax/records" && request.method === "POST") {
        return json(
          {
            id: 99,
            member_id: 1,
            member_name: "Alex Sample",
            record_type: "income_statement",
            description: "d",
            from_on: "2096-07-01",
            to_on: "2097-06-30",
            statement: {},
          },
          201,
        );
      }
      if (path === "/api/library/documents" && request.method === "POST") {
        if (attachFailures > 0) {
          attachFailures -= 1;
          return json({ errors: [{ field: "file", message: "The disk is full." }] }, 422);
        }
        return json(
          {
            document: {
              id: 1,
              file_name: "statement.pdf",
              media_type: "application/pdf",
              size_bytes: 9,
              sha256: "a".repeat(64),
              added_at: "2026-11-07T00:00:00Z",
              text_status: "extracted",
              link_count: 0,
            },
            already_in_library: false,
          },
          201,
        );
      }
      if (path === "/api/library/links" && request.method === "POST") {
        return json(
          {
            id: 7,
            document_id: 1,
            target_kind: "tax_record",
            kind_title: "Tax record",
            target_id: 99,
            label: "d",
            route: "/tax/records/99",
            removed: false,
            added_at: "2026-11-07T00:00:00Z",
          },
          201,
        );
      }
      if (path === "/api/library/links") return json([]);
      if (path === "/api/tax/records") return json([]);
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

// jsdom has no object URLs: a numbered stand-in, so a test can tell which file is shown.
let urls = 0;
const createObjectURL = vi.fn(() => `blob:invented-${++urls}`);
const revokeObjectURL = vi.fn();
beforeEach(() => {
  urls = 0;
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
});

afterEach(() => vi.unstubAllGlobals());

const PDF = () =>
  new File(["%PDF-1.4 invented"], "statement.pdf", { type: "application/pdf" });

async function openNewStatement() {
  await renderAppAt("/tax/reconciliation?fy=2097");
  fireEvent.click(await screen.findByRole("button", { name: "Add income statement" }));
  return screen.findByRole("form", { name: "New income statement" });
}

async function readPdf(form: HTMLElement) {
  const input = within(form).getByLabelText("Choose the ATO income statement PDF");
  fireEvent.change(input, { target: { files: [PDF()] } });
  await within(form).findByText(/Read \d+ figures from the PDF/);
}

test("reading a PDF fills the figures it found, marks each, leaves the rest empty and saves nothing yet", async () => {
  const calls = fakeBackend();
  const form = await openNewStatement();
  await readPdf(form);

  const read = calls.find((c) => c.path === "/api/documents/read");
  expect(read?.method).toBe("POST");
  expect(read?.contentType).toBe("application/pdf");
  // the form's own dates give the year to check the statement's period against
  expect(read?.search).toContain("target=tax.income_statement");
  expect(read?.search).toContain("financial_year=2097");

  expect(within(form).getByLabelText(/^Total gross amount/)).toHaveValue("82000.00");
  expect(within(form).getByLabelText(/^PAYGW amount/)).toHaveValue("18250.00");
  expect(within(form).getByLabelText(/^Salary sacrifice amount/)).toHaveValue("-3000.00");
  expect(within(form).getByLabelText(/^Leave payment type/)).toHaveValue("Annual leave");
  // a figure the statement did not give stays empty for typing
  expect(within(form).getByLabelText(/^Employer superannuation contribution liability/)).toHaveValue("");
  const gross = within(form).getByLabelText(/^Total gross amount/).closest("label") as HTMLElement;
  expect(within(gross).getByText("Read from the PDF")).toBeInTheDocument();
  const lump = within(form).getByLabelText(/^Lump sum payment A/).closest("label") as HTMLElement;
  expect(within(lump).getByText("Not on the statement: taken as 0")).toBeInTheDocument();
  // reading stored nothing: no record, no document
  expect(calls.some((c) => c.path.startsWith("/api/tax/records") && c.method === "POST")).toBe(false);
});

test("typing over a figure that was read takes its mark away", async () => {
  fakeBackend();
  const form = await openNewStatement();
  await readPdf(form);
  const gross = within(form).getByLabelText(/^Total gross amount/);
  fireEvent.change(gross, { target: { value: "82001.00" } });
  expect(within(gross.closest("label") as HTMLElement).queryByText("Read from the PDF")).not.toBeInTheDocument();
});

test("saving creates the record and then files the PDF in the Library and links it to the record", async () => {
  const calls = fakeBackend();
  const form = await openNewStatement();
  await readPdf(form);
  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Income statement, Sample Employer" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(screen.queryByRole("form", { name: "New income statement" })).not.toBeInTheDocument());

  const order = calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(order).toEqual([
    "/api/documents/read",
    "/api/tax/records",
    "/api/library/documents",
    "/api/library/links",
  ]);
  const attach = calls.find((c) => c.path === "/api/library/documents");
  expect(attach?.contentType).toBe("application/pdf");
  expect(attach?.search).toContain("file_name=statement.pdf");
  expect(attach?.bytes).toBe("%PDF-1.4 invented".length);
  expect(calls.find((c) => c.path === "/api/tax/records" && c.method === "POST")?.json).toMatchObject({
    statement: { gross_payments: "82000.00", salary_sacrifice_amount: "-3000.00", lump_sum_a: "0.00" },
  });
});

test("a sum that disagrees is shown with the figures and needs a tick before it saves", async () => {
  fakeBackend({ reading: READ_SUMS_DIFFER });
  const form = await openNewStatement();
  await readPdf(form);
  expect(within(form).getByTestId("reading-warning")).toHaveTextContent(
    "The parts do not add up to total gross amount: Gross amount, Bonuses and commissions, Salary sacrifice amount.",
  );
  expect(within(form).getByText(/Not found on the statement, left blank: Employer superannuation/)).toBeInTheDocument();
  expect(within(form).getByLabelText(/^Total gross amount/)).toHaveValue("82000.00"); // still filled
  const save = within(form).getByRole("button", { name: "Create Tax record" });
  expect(save).toBeDisabled();
  fireEvent.click(within(form).getByRole("checkbox", { name: "I have checked these figures against the statement." }));
  expect(save).toBeEnabled();
});

test("an unrecognised PDF says so, fills nothing and is not kept on save", async () => {
  const calls = fakeBackend({ reading: UNRECOGNISED });
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Choose the ATO income statement PDF"), {
    target: { files: [PDF()] },
  });
  expect(await within(form).findByText(/does not look like the ATO income statement/)).toBeInTheDocument();
  expect(within(form).getByLabelText(/^Total gross amount/)).toHaveValue("");
  fireEvent.change(within(form).getByLabelText(/^Total gross amount/), { target: { value: "1.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.path === "/api/tax/records")).toBe(true));
  expect(calls.some((c) => c.path.startsWith("/api/library") && c.method === "POST")).toBe(false);
});

test("a layout that needs repair fills what was found, names the missing figure and keeps the PDF beside the form", async () => {
  const calls = fakeBackend({ reading: NEEDS_REPAIR });
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Choose the ATO income statement PDF"), {
    target: { files: [PDF()] },
  });
  const message = await within(form).findByText(/Some figures could not be read: copy them from the statement beside the form/);
  expect(message).toHaveTextContent("Still to enter: PAYGW amount");
  expect(within(form).getByLabelText(/^Total gross amount/)).toHaveValue("82000.00");
  expect(within(form).getByLabelText(/^PAYGW amount/)).toHaveValue("");
  expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-1");
  // It was claimed as the statement, so saving files it in the Library like any read PDF.
  fireEvent.change(within(form).getByLabelText(/^PAYGW amount/), { target: { value: "1.00" } });
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  await waitFor(() => expect(calls.some((c) => c.path.startsWith("/api/library") && c.method === "POST")).toBe(true));
});

test("a PDF the server refuses shows its message beside the control", async () => {
  fakeBackend({
    readError: json({ errors: [{ field: "file", message: "This PDF is encrypted; save an unprotected copy and try again." }] }, 422),
  });
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Choose the ATO income statement PDF"), {
    target: { files: [PDF()] },
  });
  expect(await within(form).findByText(/This PDF is encrypted/)).toBeInTheDocument();
});

test("if keeping the PDF fails the record still exists, the screen says so and Try again keeps it", async () => {
  const calls = fakeBackend({ attachFailures: 1 });
  const form = await openNewStatement();
  await readPdf(form);
  fireEvent.click(within(form).getByRole("button", { name: "Create Tax record" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The Tax record was saved, but the PDF could not be kept as a document: The disk is full.");
  // no second Create button that would save the record twice
  expect(within(form).queryByRole("button", { name: "Create Tax record" })).not.toBeInTheDocument();
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByRole("form", { name: "New income statement" })).not.toBeInTheDocument());
  expect(calls.filter((c) => c.path === "/api/tax/records" && c.method === "POST")).toHaveLength(1);
  expect(calls.filter((c) => c.path === "/api/library/documents" && c.method === "POST")).toHaveLength(2);
  expect(calls.filter((c) => c.path === "/api/library/links" && c.method === "POST")).toHaveLength(1);
});

// --- The document beside the form (ADR-0026 section 5) ---------------------------------------

const READ_PAGES = {
  ...READ_OK,
  found: [...READ_OK.found, { ...found("reportable_fringe_benefits", "1880.00"), page: 2 }],
};

test("before a PDF is read the form is alone; after, the document sits beside it as the chosen file, nothing stored", async () => {
  const calls = fakeBackend({ reading: READ_PAGES });
  const form = await openNewStatement();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(screen.queryByTitle("The ATO income statement PDF")).not.toBeInTheDocument();
  await readPdf(form);

  const frame = screen.getByTitle("The ATO income statement PDF");
  expect(frame.tagName).toBe("IFRAME");
  // the object URL of the chosen file, not a stored file: no Library document exists yet
  expect(frame).toHaveAttribute("src", "blob:invented-1");
  expect(createObjectURL).toHaveBeenCalledTimes(1);
  expect(calls.some((c) => c.path.startsWith("/api/library/documents") && c.method !== "GET")).toBe(false);
  expect(calls.some((c) => /\/api\/library\/documents\/\d+\/file/.test(c.path))).toBe(false);
});

test("at phone width the panes are Form and Document tabs, and switching keeps what was typed", async () => {
  fakeBackend({ reading: READ_PAGES });
  const form = await openNewStatement();
  await readPdf(form);

  const formTab = screen.getByRole("tab", { name: "Form" });
  const documentTab = screen.getByRole("tab", { name: "Document" });
  expect(formTab).toHaveAttribute("aria-selected", "true");
  const panels = screen.getAllByRole("tabpanel", { hidden: true });
  expect(panels).toHaveLength(2);
  expect(panels[0]).not.toHaveClass("hidden");
  expect(panels[1]).toHaveClass("hidden"); // beside the form from the container's width up, whatever the tab

  fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Typed before switching" } });
  fireEvent.change(within(form).getByLabelText(/^Employer$/), { target: { value: "Sample Employer Pty Ltd" } });
  fireEvent.click(documentTab);
  expect(documentTab).toHaveAttribute("aria-selected", "true");
  expect(panels[0]).toHaveClass("hidden");
  expect(panels[1]).not.toHaveClass("hidden");
  fireEvent.click(formTab);
  // the same form, not a fresh one: its state survived the switch
  expect(screen.getByRole("form", { name: "New income statement" })).toBe(form);
  expect(within(form).getByLabelText("Description")).toHaveValue("Typed before switching");
  expect(within(form).getByLabelText(/^Employer$/)).toHaveValue("Sample Employer Pty Ltd");
  expect(within(form).getByLabelText(/^Total gross amount/)).toHaveValue("82000.00");
});

test("each figure read shows the page it came from, and clicking it opens the viewer there", async () => {
  fakeBackend({ reading: READ_PAGES });
  const form = await openNewStatement();
  await readPdf(form);

  const fringe = within(form).getByLabelText(/^Reportable fringe benefits total/).closest("label") as HTMLElement;
  expect(within(fringe).getByText("Read from the PDF")).toBeInTheDocument();
  const jump = within(fringe).getByRole("button", { name: "Show page 2 of the PDF" });
  expect(jump).toHaveTextContent("page 2");
  const gross = within(form).getByLabelText(/^Total gross amount/).closest("label") as HTMLElement;
  expect(within(gross).getByRole("button", { name: "Show page 1 of the PDF" })).toHaveTextContent("page 1");
  // a line the statement omitted has no page to show
  const lump = within(form).getByLabelText(/^Lump sum payment A/).closest("label") as HTMLElement;
  expect(within(lump).queryByText(/^page/)).not.toBeInTheDocument();

  expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-1");
  fireEvent.click(jump);
  expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-1#page=2");
  // and on a phone the Document tab comes forward
  expect(screen.getByRole("tab", { name: "Document" })).toHaveAttribute("aria-selected", "true");
});

test("a browser without a PDF viewer gets the page as a plain label", async () => {
  Object.defineProperty(navigator, "pdfViewerEnabled", { value: false, configurable: true });
  try {
    fakeBackend({ reading: READ_PAGES });
    const form = await openNewStatement();
    await readPdf(form);
    const fringe = within(form).getByLabelText(/^Reportable fringe benefits total/).closest("label") as HTMLElement;
    expect(within(fringe).getByText("page 2")).toBeInTheDocument();
    expect(within(fringe).queryByRole("button")).not.toBeInTheDocument();
  } finally {
    Reflect.deleteProperty(navigator, "pdfViewerEnabled");
  }
});

test("reading a different PDF replaces the object URL and releases the old one", async () => {
  fakeBackend({ reading: READ_PAGES });
  const form = await openNewStatement();
  await readPdf(form);
  expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-1");
  fireEvent.change(within(form).getByLabelText("Choose the ATO income statement PDF"), {
    target: { files: [new File(["%PDF-1.4 other"], "other.pdf", { type: "application/pdf" })] },
  });
  await waitFor(() =>
    expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-2"),
  );
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:invented-1");
});

test("a PDF nothing recognised still opens beside the form, to copy from", async () => {
  fakeBackend({ reading: UNRECOGNISED });
  const form = await openNewStatement();
  fireEvent.change(within(form).getByLabelText("Choose the ATO income statement PDF"), {
    target: { files: [PDF()] },
  });
  const message = await within(form).findByText(/does not look like the ATO income statement/);
  expect(message).toHaveTextContent("The PDF is beside the form: copy the figures from it.");
  expect(screen.getByTitle("The ATO income statement PDF")).toHaveAttribute("src", "blob:invented-1");
});

test("a figure added up from repeated rows shows each row, and a part counted as zero is named", async () => {
  fakeBackend({
    reading: {
      ...READ_OK,
      found: [
        ...READ_OK.found,
        {
          ...found("paid_leave_amount", "3750.00"),
          parts: [
            { page: 1, value: "1500.00" },
            { page: 1, value: "2250.00" },
          ],
        },
      ],
      checks: [{ ...READ_OK.checks[0], assumed_zero: ["salary_sacrifice_amount"] }],
    },
  });
  const form = await openNewStatement();
  await readPdf(form);
  expect(await within(form).findByText(/Added up from 2 rows on the statement: 1500.00 \+ 2250.00/)).toBeInTheDocument();
  expect(within(form).getByText(/counted as 0 in the check: Salary sacrifice amount/)).toBeInTheDocument();
});

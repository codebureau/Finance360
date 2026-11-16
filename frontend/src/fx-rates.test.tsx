import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { FxRatesPage } from "@/components/fx-rates-page";

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };
type R = {
  id: number;
  from_ccy: string;
  to_ccy: string;
  as_at: string;
  rate: string;
  source: string;
  provenance_note: string | null;
  recorded_at: string;
  status: "current" | "corrected" | "removed";
  superseded_at: string | null;
  superseded_by_id: number | null;
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const rate = (id: number, over: Partial<R>): R => ({
  id,
  from_ccy: "GBP",
  to_ccy: "AUD",
  as_at: "2026-01-01",
  rate: "2.0143",
  source: "manual",
  provenance_note: "Invented manual rate",
  recorded_at: "2026-02-01T00:00:00",
  status: "current",
  superseded_at: null,
  superseded_by_id: null,
  ...over,
});

function fakeBackend(initial: R[]) {
  let rows = [...initial];
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const hasBody = request.method === "POST" || request.method === "PUT";
      const body = hasBody ? ((await request.json()) as Record<string, unknown>) : undefined;
      calls.push({ method: request.method, path: url.pathname, search: url.search, body });
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (url.pathname === "/api/fx-rates/convert") {
        const on = url.searchParams.get("on")!;
        const found = rows.filter((r) => r.status === "current" && r.as_at <= on).sort((a, b) => b.as_at.localeCompare(a.as_at))[0];
        const base = { on, amount: "100.00", currency: "GBP", to_ccy: "AUD" };
        return json(
          found
            ? { ...base, result: "converted", converted: "201.43", rate: found.rate, rate_as_at: found.as_at }
            : { ...base, result: "no_rate", converted: null, rate: null, rate_as_at: null },
        );
      }
      if (url.pathname === "/api/fx-rates" && request.method === "GET") return json(rows);
      if (url.pathname === "/api/fx-rates" && request.method === "POST") {
        if (!String(body?.provenance_note).trim())
          return json({ errors: [{ field: "provenance_note", message: "Say where this rate came from." }] }, 422);
        const created = rate(50, { as_at: String(body?.as_at), rate: String(body?.rate), provenance_note: String(body?.provenance_note) });
        rows = [created, ...rows];
        return json(created, 201);
      }
      const one = /^\/api\/fx-rates\/(\d+)$/.exec(url.pathname);
      if (one && request.method === "PUT") {
        const fresh = rate(60, { as_at: String(body?.as_at), rate: String(body?.rate) });
        rows = [fresh, ...rows.map((r) => (r.id === Number(one[1]) ? { ...r, status: "corrected" as const, superseded_at: "2026-10-17T01:00:00", superseded_by_id: 60 } : r))];
        return json(fresh);
      }
      if (one && request.method === "DELETE") {
        rows = rows.map((r) => (r.id === Number(one[1]) ? { ...r, status: "removed" as const, superseded_at: "2026-10-17T01:00:00" } : r));
        return new Response(null, { status: 204 });
      }
      return new Response("?", { status: 500 });
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FxRatesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Rows have one overflow menu (no per-row buttons); open it from the keyboard, as Radix does. */
async function openRowMenu(name: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for ${name}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

test("lists rates with their exact precision, source and provenance, superseded ones marked", async () => {
  fakeBackend([
    rate(2, { as_at: "2026-04-01", rate: "2.0204000001" }),
    rate(3, { status: "corrected", superseded_at: "2026-02-02T00:00:00", superseded_by_id: 9, rate: "1.90" }),
    rate(1, {}),
  ]);
  renderPage();
  expect(await screen.findByText("2.0204000001")).toBeInTheDocument();
  expect(screen.getByText("Superseded: corrected 2026-02-02")).toBeInTheDocument();
  expect(screen.getByText("1.90")).toBeInTheDocument();
  expect(screen.getAllByText("Invented manual rate")).toHaveLength(3);
  // Only the two current rates have actions.
  expect(screen.getAllByRole("button", { name: /^Actions for / })).toHaveLength(2);
});

test("an empty list says a foreign amount cannot be converted", async () => {
  fakeBackend([]);
  renderPage();
  expect(await screen.findByText(/cannot be converted/)).toBeInTheDocument();
});

test("adding opens a dialog; a rejected rate keeps it open with the field message, then it saves", async () => {
  const calls = fakeBackend([]);
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Add FX rate" }));
  const dialog = await screen.findByRole("dialog", { name: "Add FX rate" });
  const form = within(dialog);
  fireEvent.change(form.getByLabelText("As at"), { target: { value: "2026-05-15" } });
  fireEvent.change(form.getByLabelText(/^Rate/), { target: { value: "2.1" } });
  fireEvent.click(form.getByRole("button", { name: "Add rate" }));
  expect(await form.findByText("Say where this rate came from.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Add FX rate" })).toBeInTheDocument();
  fireEvent.change(form.getByLabelText(/^Provenance note/), { target: { value: "Checked" } });
  fireEvent.click(form.getByRole("button", { name: "Add rate" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({
    from_ccy: "GBP",
    to_ccy: "AUD",
    as_at: "2026-05-15",
    rate: "2.1",
    provenance_note: "Checked",
  });
  // Focus goes back to the control that opened the dialog.
  await waitFor(() => expect(screen.getByRole("button", { name: "Add FX rate" })).toHaveFocus());
});

test("correcting opens a dialog from the row menu; the original stays visible as superseded", async () => {
  const calls = fakeBackend([rate(1, {})]);
  renderPage();
  await openRowMenu("GBP rate of 2026-01-01");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Correct rate" }));
  const form = within(await screen.findByRole("dialog", { name: "Correct the GBP rate of 2026-01-01" }));
  expect(form.getByLabelText(/^Rate/)).toHaveValue("2.0143");
  fireEvent.change(form.getByLabelText(/^Rate/), { target: { value: "2.05" } });
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  expect(await screen.findByText("2.05")).toBeInTheDocument();
  expect(screen.getByText("2.0143")).toBeInTheDocument();
  expect(screen.getByText(/Superseded: corrected/)).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT" && c.path === "/api/fx-rates/1")?.body?.rate).toBe("2.05");
});

test("removing confirms in an AlertDialog naming the rate; Cancel changes nothing, confirming supersedes it", async () => {
  const calls = fakeBackend([rate(1, {})]);
  renderPage();
  const trigger = await openRowMenu("GBP rate of 2026-01-01");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove rate" }));
  const alert = await screen.findByRole("alertdialog", { name: "Remove the GBP rate of 2026-01-01?" });
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  await waitFor(() => expect(trigger).toHaveFocus());

  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove rate" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove FX rate" }));
  expect(await screen.findByText(/Superseded: removed/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/fx-rates/1")).toBe(true);
});

test("the preview shows the rate used and its date, or says plainly there is no rate", async () => {
  fakeBackend([rate(1, { as_at: "2026-01-01", rate: "2.0143" })]);
  renderPage();
  const form = within(await screen.findByRole("form", { name: "Conversion preview" }));
  fireEvent.change(form.getByLabelText("On"), { target: { value: "2026-05-01" } });
  fireEvent.click(form.getByRole("button", { name: "Convert to AUD" }));
  expect(await form.findByRole("status")).toHaveTextContent(
    "GBP 100.00 = AUD 201.43 at 2.0143, the rate of 2026-01-01.",
  );
  fireEvent.change(form.getByLabelText("On"), { target: { value: "2025-12-31" } });
  fireEvent.click(form.getByRole("button", { name: "Convert to AUD" }));
  expect(await form.findByText(/No GBP to AUD rate on or before 2025-12-31/)).toBeInTheDocument();
});

test("a long history of fetched rates is paged, newest first, with Show more", async () => {
  const many = Array.from({ length: 60 }, (_, i) =>
    rate(i + 1, { as_at: `2026-${i < 30 ? "09" : "10"}-${String((i % 30) + 1).padStart(2, "0")}`, source: "rba" }),
  );
  fakeBackend(many);
  renderPage();
  const table = await screen.findByRole("table", { name: "FX rate history" });
  expect(within(table).getAllByRole("row")).toHaveLength(26); // header + 25
  expect(screen.getByText("Showing 25 of 60 rates, newest first.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(within(table).getAllByRole("row")).toHaveLength(51);
});

test("a fetched rate shows its source and is overridden, not edited: no actions on its row", async () => {
  fakeBackend([
    rate(2, {
      as_at: "2026-10-19",
      rate: "2.0399836801",
      source: "rba",
      provenance_note: "RBA F11.1: A$1 = 0.4902 GBP; inverted once (half-even, 10 d.p.)",
    }),
    rate(1, {}),
  ]);
  renderPage();
  await screen.findByText("2.0399836801");
  expect(screen.getByText("rba")).toBeInTheDocument();
  expect(screen.getByText(/0.4902 GBP/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Actions for GBP rate of 2026-10-19" })).toBeNull();
  // the owner's own manual rate keeps its menu
  expect(screen.getByRole("button", { name: "Actions for GBP rate of 2026-01-01" })).toBeInTheDocument();
});

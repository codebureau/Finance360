import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";
import type { Instrument } from "@/lib/instruments";

type P = {
  id: number;
  instrument_id: number;
  as_at: string;
  value: string;
  currency: string;
  source: string;
  provenance_note: string | null;
  recorded_at: string;
  status: "current" | "corrected" | "removed";
  superseded_at: string | null;
  superseded_by_id: number | null;
};
type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

const INSTRUMENT: Instrument = {
  id: 1,
  name: "Sample Broad Market ETF",
  kind: "etf",
  currency: "AUD",
  notes: null,
  identifiers: [],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const price = (id: number, over: Partial<P>): P => ({
  id,
  instrument_id: 1,
  as_at: "2026-09-01",
  value: "98.1234",
  currency: "AUD",
  source: "yfinance",
  provenance_note: "Invented daily close (symbol SBME.AX)",
  recorded_at: "2026-10-01T00:00:00",
  status: "current",
  superseded_at: null,
  superseded_by_id: null,
  ...over,
});

function fakeBackend(initial: P[]) {
  let rows = [...initial];
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const hasBody = request.method === "POST" || request.method === "PUT";
      const body = hasBody ? ((await request.json()) as Record<string, unknown>) : undefined;
      calls.push({ method: request.method, path: url.pathname, search: url.search, body });
      if (url.pathname === "/api/instruments") return json([INSTRUMENT]);
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (url.pathname === "/api/instruments/1/price") {
        const on = url.searchParams.get("on")!;
        const found = rows
          .filter((r) => r.status === "current" && r.as_at <= on)
          .sort((a, b) => b.as_at.localeCompare(a.as_at))[0];
        return json(
          found
            ? { result: "price", on, value: found.value, currency: "AUD", as_at: found.as_at, source: found.source }
            : { result: "no_price", on, value: null, currency: null, as_at: null, source: null },
        );
      }
      if (url.pathname === "/api/instruments/1/prices" && request.method === "GET") return json(rows);
      if (url.pathname === "/api/instruments/1/prices" && request.method === "POST") {
        if (!String(body?.provenance_note).trim())
          return json({ errors: [{ field: "provenance_note", message: "Say where this Price came from." }] }, 422);
        if (body?.currency !== "AUD")
          return json({ errors: [{ field: "currency", message: "Sample Broad Market ETF is priced in AUD." }] }, 422);
        const created = price(50, {
          as_at: String(body?.as_at),
          value: String(body?.value),
          source: "manual",
          provenance_note: String(body?.provenance_note),
        });
        rows = [created, ...rows];
        return json(created, 201);
      }
      const one = /^\/api\/instruments\/1\/prices\/(\d+)$/.exec(url.pathname);
      if (one && request.method === "PUT") {
        const fresh = price(60, { as_at: String(body?.as_at), value: String(body?.value), source: "manual" });
        rows = [
          fresh,
          ...rows.map((r) =>
            r.id === Number(one[1])
              ? { ...r, status: "corrected" as const, superseded_at: "2026-10-19T01:00:00", superseded_by_id: 60 }
              : r,
          ),
        ];
        return json(fresh);
      }
      if (one && request.method === "DELETE") {
        rows = rows.map((r) =>
          r.id === Number(one[1]) ? { ...r, status: "removed" as const, superseded_at: "2026-10-19T01:00:00" } : r,
        );
        return new Response(null, { status: 204 });
      }
      return new Response("?", { status: 500 });
    }),
  );
  return calls;
}


const renderPanel = () => renderAppAt("/instruments/1");

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openMenu(name: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for ${name}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
}

test("lists Prices with exact values, source and note; only a current manual one has actions", async () => {
  fakeBackend([
    price(3, { as_at: "2026-09-03", value: "99.5000", source: "manual", provenance_note: "Statement" }),
    price(2, { as_at: "2026-09-02", value: "98.7654" }),
    price(1, { as_at: "2026-08-31", status: "corrected", superseded_at: "2026-09-04T00:00:00", superseded_by_id: 9 }),
  ]);
  await renderPanel();
  const table = await screen.findByRole("table", { name: "Price history of Sample Broad Market ETF" });
  expect(await within(table).findByText("98.7654")).toBeInTheDocument();
  expect(within(table).getByText("99.5000")).toBeInTheDocument();
  expect(within(table).getByText("Statement")).toBeInTheDocument();
  expect(within(table).getByText("Superseded: corrected 2026-09-04")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Actions for Price of 2026-09-03 for Sample Broad Market ETF" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Actions for Price of 2026-09-02/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Actions for Price of 2026-08-31/ })).not.toBeInTheDocument();
});

test("adding a manual Price in a Dialog needs a note and sends the Instrument currency", async () => {
  const calls = fakeBackend([price(2, { as_at: "2026-09-02" })]);
  await renderPanel();
  fireEvent.click(await screen.findByRole("button", { name: "Add Price" }));
  const form = within(await screen.findByRole("form", { name: "Add Price to Sample Broad Market ETF" }));
  fireEvent.change(form.getByLabelText("As at"), { target: { value: "2026-09-02" } });
  fireEvent.change(form.getByLabelText(/^Price per unit/), { target: { value: "100.25" } });
  fireEvent.click(form.getByRole("button", { name: "Add Price" }));
  expect(await form.findByText("Say where this Price came from.")).toBeInTheDocument();
  fireEvent.change(form.getByLabelText(/^Note/), { target: { value: "Checked" } });
  fireEvent.click(form.getByRole("button", { name: "Add Price" }));
  expect(await screen.findByText("100.25")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({
    as_at: "2026-09-02",
    value: "100.25",
    provenance_note: "Checked",
    currency: "AUD",
  });
});

test("correcting a manual Price keeps the original visible as superseded", async () => {
  const calls = fakeBackend([price(3, { as_at: "2026-09-03", value: "99.5000", source: "manual" })]);
  await renderPanel();
  await openMenu("Price of 2026-09-03 for Sample Broad Market ETF");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Correct Price" }));
  const form = within(await screen.findByRole("form", { name: "Correct Price of Sample Broad Market ETF" }));
  expect(form.getByLabelText(/^Price per unit/)).toHaveValue("99.5000");
  fireEvent.change(form.getByLabelText(/^Price per unit/), { target: { value: "99.75" } });
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  expect(await screen.findByText("99.75")).toBeInTheDocument();
  expect(screen.getByText("99.5000")).toBeInTheDocument();
  expect(screen.getByText(/Superseded: corrected/)).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT")?.body?.value).toBe("99.75");
});

test("removing a manual Price confirms in an AlertDialog; Cancel removes nothing", async () => {
  const calls = fakeBackend([price(3, { as_at: "2026-09-03", value: "99.5000", source: "manual" })]);
  await renderPanel();
  await openMenu("Price of 2026-09-03 for Sample Broad Market ETF");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Price" }));
  const alert = await screen.findByRole("alertdialog", {
    name: "Remove the Price of 2026-09-03 for Sample Broad Market ETF?",
  });
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);

  await openMenu("Price of 2026-09-03 for Sample Broad Market ETF");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Price" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove Price" }));
  expect(await screen.findByText(/Superseded: removed/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/instruments/1/prices/3")).toBe(true);
});

test("a long history shows the first ten and offers the rest", async () => {
  fakeBackend(
    Array.from({ length: 12 }, (_, n) => price(n + 1, { as_at: `2026-08-${String(n + 10).padStart(2, "0")}` })),
  );
  await renderPanel();
  const table = await screen.findByRole("table", { name: "Price history of Sample Broad Market ETF" });
  await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(11)); // header + 10
  fireEvent.click(screen.getByRole("button", { name: "Show all 12 Prices" }));
  expect(within(table).getAllByRole("row")).toHaveLength(13);
});

test("the lookup says which earlier date it used, and says plainly when there is no Price", async () => {
  fakeBackend([price(1, { as_at: "2026-09-01", value: "98.1234" })]);
  await renderPanel();
  const form = within(await screen.findByRole("form", { name: "Look up a Price of Sample Broad Market ETF" }));
  fireEvent.change(form.getByLabelText("On"), { target: { value: "2026-09-10" } });
  fireEvent.click(form.getByRole("button", { name: "Look up" }));
  expect(await form.findByRole("status")).toHaveTextContent("98.1234, the Price of 2026-09-01 (yfinance).");
  fireEvent.change(form.getByLabelText("On"), { target: { value: "2026-08-01" } });
  fireEvent.click(form.getByRole("button", { name: "Look up" }));
  await waitFor(() => expect(form.getByRole("status")).toHaveTextContent("No Price on or before 2026-08-01."));
  expect(form.getByRole("status")).not.toHaveTextContent(/\b0\b/);
});

test("an empty history says so", async () => {
  fakeBackend([]);
  await renderPanel();
  expect(await screen.findByText("No Prices yet.")).toBeInTheDocument();
});

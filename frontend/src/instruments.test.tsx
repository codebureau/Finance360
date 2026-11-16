import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

type Ident = { id: number; scheme: string; value: string };
type Inst = {
  id: number;
  name: string;
  kind: string;
  currency: string;
  notes: string | null;
  identifiers: Ident[];
};
type Call = { method: string; path: string; body?: Record<string, unknown> };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const SAMPLE: Inst[] = [
  {
    id: 1,
    name: "Sample Broad Market ETF",
    kind: "etf",
    currency: "AUD",
    notes: "Invented exchange-traded fund.",
    identifiers: [
      { id: 1, scheme: "ticker", value: "SBME" },
      { id: 2, scheme: "isin", value: "AU0000SMPL01" },
    ],
  },
  {
    id: 3,
    name: "Sample Balanced Growth Fund",
    kind: "fund",
    currency: "AUD",
    notes: null,
    identifiers: [{ id: 7, scheme: "vendor_id", value: "SBGF-0001" }],
  },
];

function fakeBackend(initial: Inst[]) {
  let rows = structuredClone(initial);
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const hasBody = request.method === "POST" || request.method === "PUT";
      const body = hasBody ? ((await request.json()) as Record<string, unknown>) : undefined;
      calls.push({ method: request.method, path: url.pathname, body });
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (/^\/api\/instruments\/\d+\/prices$/.test(url.pathname)) return json([]);
      if (url.pathname === "/api/instruments" && request.method === "GET") return json(rows);
      if (url.pathname === "/api/instruments" && request.method === "POST") {
        const created = { id: 50, notes: null, identifiers: [], ...body } as unknown as Inst;
        rows = [...rows, created];
        return json(created, 201);
      }
      const ident = /^\/api\/instruments\/(\d+)\/identifiers(?:\/(\d+))?$/.exec(url.pathname);
      if (ident && request.method === "POST") {
        const taken = rows.find((r) =>
          r.identifiers.some((i) => i.scheme === body?.scheme && i.value.toLowerCase() === String(body?.value).trim().toLowerCase()),
        );
        if (taken)
          return json(
            { errors: [{ field: "value", message: `That identifier already identifies ${taken.name}.` }] },
            422,
          );
        const row = rows.find((r) => r.id === Number(ident[1]))!;
        row.identifiers = [...row.identifiers, { id: 99, scheme: String(body?.scheme), value: String(body?.value) }];
        return json(row, 201);
      }
      if (ident && request.method === "DELETE") {
        const row = rows.find((r) => r.id === Number(ident[1]))!;
        row.identifiers = row.identifiers.filter((i) => i.id !== Number(ident[2]));
        return json(row);
      }
      const one = /^\/api\/instruments\/(\d+)$/.exec(url.pathname);
      if (one && request.method === "PUT") {
        const row = rows.find((r) => r.id === Number(one[1]))!;
        Object.assign(row, body);
        return json(row);
      }
      return new Response("?", { status: 500 });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openMenu(name: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for ${name}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
}

test("the list is a table of Instruments with kind, currency and identifiers", async () => {
  fakeBackend(SAMPLE);
  await renderAppAt("/instruments");
  const table = await screen.findByRole("table", { name: "Instruments" });
  const etf = within(within(table).getByRole("row", { name: "Sample Broad Market ETF" }));
  expect(etf.getByText("ETF")).toBeInTheDocument();
  expect(etf.getByText("AUD")).toBeInTheDocument();
  expect(etf.getByText(/Ticker SBME/)).toBeInTheDocument();
  expect(etf.getByText(/ISIN AU0000SMPL01/)).toBeInTheDocument();
  expect(screen.getByText("Sample Balanced Growth Fund")).toBeInTheDocument();
});

test("clicking a row opens that Instrument's detail page, which survives a deep link", async () => {
  fakeBackend(SAMPLE);
  const { router } = await renderAppAt("/instruments");
  fireEvent.click(await screen.findByRole("row", { name: "Sample Balanced Growth Fund" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/instruments/3"));
  expect(await screen.findByRole("heading", { name: "Sample Balanced Growth Fund" })).toBeInTheDocument();
  expect(screen.getByText("SBGF-0001")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Prices of Sample Balanced Growth Fund" })).toBeInTheDocument();
});

test("a deep link to a detail page works, and an unknown id says so", async () => {
  fakeBackend(SAMPLE);
  await renderAppAt("/instruments/1");
  expect(await screen.findByRole("heading", { name: "Sample Broad Market ETF" })).toBeInTheDocument();
  expect(screen.getByText("AU0000SMPL01")).toBeInTheDocument();
});

test("an unknown Instrument id shows a not-found message", async () => {
  fakeBackend(SAMPLE);
  await renderAppAt("/instruments/999");
  expect(await screen.findByText("Instrument not found")).toBeInTheDocument();
});

test("the detail page's Audit trail is a link carrying the Instrument filter", async () => {
  fakeBackend(SAMPLE);
  const { router } = await renderAppAt("/instruments/3");
  fireEvent.click(await screen.findByRole("link", { name: "Audit trail of Sample Balanced Growth Fund" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/audit"));
  expect(router.state.location.search).toEqual({ entity_type: "instrument", entity_id: 3 });
});

test("adding a name alias in a Dialog lists it", async () => {
  const calls = fakeBackend(SAMPLE);
  await renderAppAt("/instruments/3");
  fireEvent.click(await screen.findByRole("button", { name: "Add identifier" }));
  const form = within(await screen.findByRole("form", { name: "Add identifier to Sample Balanced Growth Fund" }));
  fireEvent.change(form.getByLabelText("Scheme"), { target: { value: "name" } });
  fireEvent.change(form.getByLabelText("Identifier"), { target: { value: "SBG unlisted" } });
  fireEvent.click(form.getByRole("button", { name: "Add identifier" }));
  expect(await screen.findByText("SBG unlisted")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST" && c.path === "/api/instruments/3/identifiers")?.body).toEqual({
    scheme: "name",
    value: "SBG unlisted",
  });
});

test("reusing a ticker shows the conflict in the Dialog and adds nothing", async () => {
  fakeBackend(SAMPLE);
  await renderAppAt("/instruments/3");
  fireEvent.click(await screen.findByRole("button", { name: "Add identifier" }));
  const form = within(await screen.findByRole("form", { name: "Add identifier to Sample Balanced Growth Fund" }));
  fireEvent.change(form.getByLabelText("Scheme"), { target: { value: "ticker" } });
  fireEvent.change(form.getByLabelText("Identifier"), { target: { value: " sbme " } });
  fireEvent.click(form.getByRole("button", { name: "Add identifier" }));
  expect(
    await form.findByText("That identifier already identifies Sample Broad Market ETF."),
  ).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("removing an identifier confirms in an AlertDialog; Cancel removes nothing", async () => {
  const calls = fakeBackend(SAMPLE);
  await renderAppAt("/instruments/3");
  await openMenu("Vendor ID SBGF-0001");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove identifier" }));
  const alert = await screen.findByRole("alertdialog", { name: "Remove the Vendor ID SBGF-0001?" });
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);

  await openMenu("Vendor ID SBGF-0001");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove identifier" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove identifier" }));
  await waitFor(() => expect(screen.queryByText("SBGF-0001")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/instruments/3/identifiers/7")).toBe(true);
});

test("editing in a Dialog sends the editable fields only", async () => {
  const calls = fakeBackend(SAMPLE);
  await renderAppAt("/instruments/3");
  fireEvent.click(await screen.findByRole("button", { name: "Edit Sample Balanced Growth Fund" }));
  const form = within(await screen.findByRole("form", { name: "Edit Sample Balanced Growth Fund" }));
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Sample Balanced Fund" } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("heading", { name: "Sample Balanced Fund" })).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
    name: "Sample Balanced Fund",
    kind: "fund",
    currency: "AUD",
    notes: null,
  });
});

test("one primary Add instrument creates in a Dialog and lists the new Instrument", async () => {
  const calls = fakeBackend([]);
  await renderAppAt("/instruments");
  expect(await screen.findByText(/No Instruments yet/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add instrument" }));
  const form = within(await screen.findByRole("form", { name: "Add Instrument" }));
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Sample New Share" } });
  fireEvent.change(form.getByLabelText("Kind"), { target: { value: "share" } });
  fireEvent.click(form.getByRole("button", { name: "Add Instrument" }));
  expect(await screen.findByText("Sample New Share")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    name: "Sample New Share",
    kind: "share",
    currency: "AUD",
    notes: null,
    identifiers: [],
  });
});

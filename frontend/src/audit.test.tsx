import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

type Call = { path: string; search: URLSearchParams };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const entry = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  occurred_at: "2026-10-17T01:30:00Z",
  action: "update",
  entity_type: "account",
  entity_id: 10,
  actor: "Owner",
  actor_ref: "login:1",
  source: "api",
  summary: "Edited Account 'Everyday account'",
  changes: [{ field: "Name", before: "Everyday", after: "Everyday account" }],
  ...over,
});

const ACCOUNT = {
  id: 10,
  name: "Everyday account",
  category_key: "cash",
  category_name: "Cash",
  account_type_key: "transaction_account",
  account_type_name: "Transaction account",
  currency: "AUD",
  notes: null,
  opened_on: null,
  closed_on: null,
  ownership: "sole",
  owners: [{ member_id: 1, member_name: "Alex Sample", share_bp: 10000 }],
  tags: [],
};

function fakeBackend(
  items = [entry(2), entry(1, { action: "create", changes: [{ field: "Name", before: null, after: "Everyday" }] })],
) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      calls.push({ path: url.pathname, search: url.searchParams });
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (url.pathname === "/api/audit") return json({ items, total: items.length });
      if (url.pathname === "/api/audit/auth-events")
        return json({
          total: 1,
          items: [
            {
              id: 1,
              occurred_at: "2026-10-17T01:30:00Z",
              kind: "login_failed",
              label: "Sign-in failed",
              detail: { client: "10.0.0.1" },
            },
          ],
        });
      if (url.pathname === "/api/accounts") return json([ACCOUNT]);
      if (url.pathname === "/api/accounts/10") return json(ACCOUNT);
      if (url.pathname === "/api/accounts/10/valuations") return json([]);
      if (url.pathname === "/api/accounts/tags") return json([]);
      if (url.pathname === "/api/catalogue") return json({ version: 1, classes: [] });
      if (url.pathname === "/api/position") return json({});
      return json({}, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const lastAudit = (calls: Call[]) => calls.filter((c) => c.path === "/api/audit").at(-1)!;

test("lists changes newest first with time in the Household zone, actor, source and a diff", async () => {
  fakeBackend();
  await renderAppAt("/audit");
  const table = await screen.findByRole("table", { name: "Audit trail entries" });
  const rows = within(table)
    .getAllByRole("row")
    .filter((r) => r.dataset.entity);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("Edited Account 'Everyday account'");
  // 01:30 UTC on 17 Oct is 12:30 in Melbourne (daylight saving).
  expect(rows[0]).toHaveTextContent(/17 Oct 2026.*12:30/);
  expect(rows[0]).toHaveTextContent("Owner · api");
  expect(rows[0]).toHaveTextContent("account #10");
  expect(rows[0]).toHaveTextContent("Name: Everyday → Everyday account");
  expect(rows[1]).toHaveTextContent("create");
});

test("changing a filter puts it in the URL and sends it to the API", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/audit");
  await screen.findByRole("table", { name: "Audit trail entries" });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "fx_rate" } });
  await waitFor(() => expect(router.state.location.search).toEqual({ entity_type: "fx_rate" }));
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-10-31" } });
  await waitFor(() => {
    expect(router.state.location.search).toEqual({
      entity_type: "fx_rate",
      date_from: "2026-10-01",
      date_to: "2026-10-31",
    });
    const last = lastAudit(calls);
    expect(last.search.get("entity_type")).toBe("fx_rate");
    expect(last.search.get("date_from")).toBe("2026-10-01");
    expect(last.search.get("date_to")).toBe("2026-10-31");
  });
});

test("a deep link applies its filters, and back and forward restore them", async () => {
  const calls = fakeBackend();
  const { router, history } = await renderAppAt(
    "/audit?entity_type=instrument&entity_id=3&date_from=2026-10-01",
  );
  expect(await screen.findByText(/Showing the trail of instrument #3/)).toBeInTheDocument();
  expect(screen.getByLabelText("Type")).toHaveValue("instrument");
  expect(screen.getByLabelText("From")).toHaveValue("2026-10-01");
  await waitFor(() => {
    const s = lastAudit(calls).search;
    expect([s.get("entity_type"), s.get("entity_id"), s.get("date_from")]).toEqual([
      "instrument",
      "3",
      "2026-10-01",
    ]);
  });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "price" } });
  await waitFor(() => expect(router.state.location.search).toMatchObject({ entity_type: "price", entity_id: 3 }));
  history.back();
  await waitFor(() => expect(screen.getByLabelText("Type")).toHaveValue("instrument"));
  expect(router.state.location.search).toMatchObject({ entity_type: "instrument", entity_id: 3 });
  history.forward();
  await waitFor(() => expect(screen.getByLabelText("Type")).toHaveValue("price"));
});

test("malformed search params are dropped rather than breaking the page", async () => {
  const calls = fakeBackend();
  await renderAppAt("/audit?entity_id=abc&account_id=-4&date_from=yesterday&offset=1.5&entity_type=fx_rate");
  await screen.findByRole("table", { name: "Audit trail entries" });
  const s = lastAudit(calls).search;
  expect(s.get("entity_type")).toBe("fx_rate");
  for (const k of ["entity_id", "account_id", "date_from"]) expect([k, s.get(k)]).toEqual([k, null]);
  expect(s.get("offset")).toBe("0");
});

test("shows an empty state", async () => {
  fakeBackend([]);
  await renderAppAt("/audit");
  expect(await screen.findByText("No changes match these filters.")).toBeInTheDocument();
});

test("the Sign-ins view is a URL and lists sign-in events", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/audit");
  fireEvent.click(await screen.findByRole("link", { name: "Sign-ins" }));
  await waitFor(() => expect(router.state.location.search).toEqual({ tab: "sign-ins" }));
  const table = await screen.findByRole("table", { name: "Sign-in events" });
  expect(table).toHaveTextContent("Sign-in failed");
  expect(table).toHaveTextContent("client: 10.0.0.1");
  fireEvent.change(screen.getByLabelText("Event"), { target: { value: "lockout" } });
  await waitFor(() => {
    expect(router.state.location.search).toEqual({ tab: "sign-ins", kind: "lockout" });
    expect(calls.filter((c) => c.path === "/api/audit/auth-events").at(-1)!.search.get("kind")).toBe("lockout");
  });
});

test("an Account's Audit trail link opens the audit trail filtered to that Account", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/accounts/10");
  fireEvent.click(await screen.findByRole("link", { name: /View audit trail for Everyday account/ }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/audit"));
  expect(router.state.location.search).toEqual({ account_id: 10 });
  expect(await screen.findByText(/Showing the trail of Account #10 and its Valuations/)).toBeInTheDocument();
  await waitFor(() => expect(lastAudit(calls).search.get("account_id")).toBe("10"));
  fireEvent.click(screen.getByRole("link", { name: "Show everything" }));
  await waitFor(() => expect(lastAudit(calls).search.has("account_id")).toBe(false));
  expect(router.state.location.search).toEqual({});
});

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { clearAnnouncement, signedDelta, type Notice } from "@/lib/notices";
import { everyday, fakeBackend } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

afterEach(() => {
  clearAnnouncement();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const up: Notice = {
  id: 1,
  created_at: "2026-11-28T01:00:00",
  kind: "valuation.added",
  headline: "You added a Valuation for Sample Savings.",
  effect: "Net position moved by $1,200.00 (up). What do we own and owe? went from Partly ready to Ready.",
  delta: { minor: 120000, currency: "AUD", amount: "1200.00" },
  direction: "up",
  link_path: "/position",
  dismissed: false,
};
const down: Notice = {
  ...up,
  id: 2,
  kind: "valuation.corrected",
  headline: "You corrected a Valuation for Sample Home Loan.",
  effect: "Net position moved by $300.50 (down).",
  delta: { minor: -30050, currency: "AUD", amount: "-300.50" },
  direction: "down",
};
const readinessOnly: Notice = {
  ...up,
  id: 3,
  effect: "What do we own and owe? went from Partly ready to Ready.",
  delta: null,
  direction: null,
};

/** Home with a notices list (and the other Home cards empty). */
function stubHome(items: Notice[], dismissed: number[] = []) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      calls.push(`${request.method} ${path}${url.search}`);
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1", schema_version: "0038" });
      if (path === "/api/notices") {
        const all = items.map((n) => ({ ...n, dismissed: n.dismissed || dismissed.includes(n.id) }));
        return json({ items: url.searchParams.get("include_dismissed") === "true" ? all : all.filter((n) => !n.dismissed) });
      }
      const dismiss = /^\/api\/notices\/(\d+)\/dismiss$/.exec(path);
      if (dismiss) {
        dismissed.push(Number(dismiss[1]));
        return new Response(null, { status: 204 });
      }
      if (path === "/api/questions") return json({ today: "2026-11-28", level: "steady", headline: "All 0 questions are ready.", questions: [] });
      if (path === "/api/due") return json({ level: "steady", today: "2026-11-28", items: [], snoozed: [], failed_rules: [] });
      if (path === "/api/household") return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: "steady", members: [] });
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
  return calls;
}

test("signedDelta is the sign and the exact figure, never a float", () => {
  expect(signedDelta(up)).toBe("+1,200.00");
  expect(signedDelta(down)).toBe("-300.50");
  expect(signedDelta(readinessOnly)).toBeNull();
  expect(signedDelta({ delta: { minor: 5, currency: "GBP", amount: "0.05" } })).toBe("+GBP 0.05");
});

test("Home: Recent changes lists the notices newest first, after Your questions and before What's due", async () => {
  stubHome([up, down]);
  await renderAppAt("/");
  const heading = await screen.findByRole("heading", { level: 2, name: "Recent changes" });
  const rows = await screen.findAllByTestId("notice-row");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("You added a Valuation for Sample Savings.")).toBeInTheDocument();
  expect(within(rows[0]).getByText("+1,200.00")).toBeInTheDocument();
  expect(within(rows[1]).getByText("-300.50")).toBeInTheDocument();
  expect(within(rows[0]).getByRole("link", { name: "Take a look" })).toHaveAttribute("href", "/position");
  const questions = await screen.findByRole("heading", { level: 2, name: "Your questions" });
  const due = await screen.findByRole("heading", { level: 2, name: "What's due" });
  expect(questions.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(heading.compareDocumentPosition(due) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // list form only: no Cards and List switch on this card
  expect(screen.queryByRole("button", { name: /cards/i })).not.toBeInTheDocument();
});

test("Home: a quiet empty state", async () => {
  stubHome([]);
  await renderAppAt("/");
  expect(await screen.findByText("Nothing has changed lately.")).toBeInTheDocument();
});

test("a Question's change alone shows no figure", async () => {
  stubHome([readinessOnly]);
  await renderAppAt("/");
  const row = await screen.findByTestId("notice-row");
  expect(within(row).getByText("What do we own and owe? went from Partly ready to Ready.")).toBeInTheDocument();
  expect(within(row).queryByText(/^[+-]/)).not.toBeInTheDocument();
});

test("dismissing a row removes it from Home", async () => {
  const calls = stubHome([up, down]);
  await renderAppAt("/");
  const dismiss = await screen.findByRole("button", { name: `Dismiss: ${up.headline}` });
  fireEvent.click(dismiss);
  await waitFor(() => expect(calls).toContain("POST /api/notices/1/dismiss"));
  await waitFor(() => expect(screen.getAllByTestId("notice-row")).toHaveLength(1));
  expect(screen.queryByText(up.headline)).not.toBeInTheDocument();
});

test("the Recent changes page shows dismissed rows muted, with no dismiss button", async () => {
  stubHome([up, down], [1]);
  await renderAppAt("/recent-changes");
  expect(await screen.findByRole("heading", { level: 1, name: "Recent changes" })).toBeInTheDocument();
  const rows = await screen.findAllByTestId("notice-row");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveAttribute("data-dismissed", "true");
  expect(within(rows[0]).getByText("Dismissed")).toBeInTheDocument();
  expect(within(rows[0]).queryByRole("button")).not.toBeInTheDocument();
  expect(within(rows[1]).getByRole("button", { name: `Dismiss: ${down.headline}` })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "About Recent changes" })).toHaveAttribute("href", "/guide/recent-changes");
});

test("the sidebar links to Recent changes", async () => {
  stubHome([]);
  await renderAppAt("/");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Recent changes" })).toHaveAttribute("href", "/recent-changes");
});

test("saving a Valuation announces its Notice in a live region, with arrow, sign and a link", async () => {
  fakeBackend([everyday]);
  const inner = globalThis.fetch;
  vi.stubGlobal("fetch", async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/notices") return json({ items: [] });
    if (request.method === "POST" && url.pathname.endsWith("/valuations")) {
      const res = await inner(request);
      return json({ ...(await res.json()), notice: down }, 201);
    }
    return inner(request);
  });
  await renderAppAt(`/accounts/${everyday.id}`);
  const region = within(await screen.findByRole("region", { name: `Valuations of ${everyday.name}` }));
  fireEvent.click(await region.findByRole("button", { name: "Add Valuation" }));
  const form = within(await screen.findByRole("form", { name: `Add Valuation to ${everyday.name}` }));
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "42.50" } });
  fireEvent.change(form.getByLabelText("Source"), { target: { value: "estimate" } });
  fireEvent.change(form.getByLabelText("Provenance note"), { target: { value: "Rough guess" } });
  fireEvent.click(form.getByRole("button", { name: "Add Valuation" }));
  const toast = await screen.findByTestId("notice-toast");
  expect(toast.parentElement).toHaveAttribute("aria-live", "polite");
  expect(within(toast).getByText(down.headline)).toBeInTheDocument();
  expect(within(toast).getByText("-300.50")).toBeInTheDocument(); // sign and figure, not colour alone
  expect(within(toast).getByRole("link", { name: "Take a look" })).toHaveAttribute("href", "/position");
  fireEvent.click(within(toast).getByRole("button", { name: "Dismiss this message" }));
  await waitFor(() => expect(screen.queryByTestId("notice-toast")).not.toBeInTheDocument());
});

test("a Valuation that moved nothing shows no toast", async () => {
  fakeBackend([everyday]);
  const inner = globalThis.fetch;
  vi.stubGlobal("fetch", async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/notices") return json({ items: [] });
    if (request.method === "POST" && url.pathname.endsWith("/valuations")) {
      const res = await inner(request);
      return json({ ...(await res.json()), notice: null }, 201);
    }
    return inner(request);
  });
  await renderAppAt(`/accounts/${everyday.id}`);
  const region = within(await screen.findByRole("region", { name: `Valuations of ${everyday.name}` }));
  fireEvent.click(await region.findByRole("button", { name: "Add Valuation" }));
  const form = within(await screen.findByRole("form", { name: `Add Valuation to ${everyday.name}` }));
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "42.50" } });
  fireEvent.change(form.getByLabelText("Source"), { target: { value: "estimate" } });
  fireEvent.change(form.getByLabelText("Provenance note"), { target: { value: "Rough guess" } });
  fireEvent.click(form.getByRole("button", { name: "Add Valuation" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.queryByTestId("notice-toast")).not.toBeInTheDocument();
});

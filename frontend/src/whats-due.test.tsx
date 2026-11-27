import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Item = {
  rule_key: string;
  subject_key: string;
  importance: "important" | "optional";
  title: string;
  effect: string;
  due_on: string;
  link: string;
  suits: string[];
  actions: { kind: string; label: string; capture_id: number }[];
};

const item = (over: Partial<Item>): Item => ({
  rule_key: "position.manual_valuation",
  subject_key: "account:1@2026-10",
  importance: "important",
  title: "Your Position uses a Savings balance from June",
  effect: "A current Valuation makes your net position more accurate.",
  due_on: "2026-07-01",
  link: "/accounts/1",
  suits: ["investments"],
  actions: [],
  ...over,
});

const CASH = item({});
const SUPER = item({
  rule_key: "position.super_valuation",
  subject_key: "account:2@2026-10",
  title: "Your Position uses a Super balance from April",
  link: "/accounts/2",
});
const PROPERTY = item({
  rule_key: "position.slow_valuation",
  subject_key: "account:3@2026",
  importance: "optional",
  title: "Your Position uses a Home value from last year",
  link: "/accounts/3",
});

type State = {
  level: string;
  snoozed: { snooze_id: number; until_on: string | null; item: Item }[];
  failed: { rule_key: string; name: string }[];
  all: Item[];
};

let state: State;
let calls: { method: string; path: string; body?: unknown }[];
let dueDelayMs = 0;
// The Login's Display preferences; these tests exercise the list unless a test says otherwise.
let prefs: Record<string, string>;
const puts: Record<string, string | null>[] = [];

beforeEach(() => {
  calls = [];
  dueDelayMs = 0;
  prefs = { "whats-due.items": "list" };
  puts.length = 0;
  state = { level: "steady", snoozed: [], failed: [], all: [CASH, SUPER, PROPERTY] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body = request.method === "GET" || request.method === "DELETE" ? undefined : await request.json();
      calls.push({ method: request.method, path, body });
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1.2.3", schema_version: "0004" });
      if (path === "/api/backup/status") {
        return json({
          configured: false,
          remote_configured: false,
          stale: false,
          stale_after_hours: 36,
          latest: null,
          last_success: null,
          maintenance: null,
        });
      }
      if (path === "/api/household" && request.method === "GET") {
        return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: state.level, members: [] });
      }
      if (path === "/api/household" && request.method === "PUT") {
        state.level = (body as { effort_level: string }).effort_level;
        return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: state.level, members: [] });
      }
      if (path === "/api/preferences") {
        if (request.method === "PUT") {
          const sent = (body as { preferences: Record<string, string | null> }).preferences;
          puts.push(sent);
          for (const [k, v] of Object.entries(sent)) {
            if (v === null) delete prefs[k];
            else prefs[k] = v;
          }
        }
        return json({ preferences: { ...prefs } });
      }
      if (path === "/api/due" && request.method === "GET") {
        if (dueDelayMs) await new Promise((resolve) => setTimeout(resolve, dueDelayMs));
        const hidden = new Set(state.snoozed.map((s) => `${s.item.rule_key}|${s.item.subject_key}`));
        const shown = state.all
          .filter((i) => !hidden.has(`${i.rule_key}|${i.subject_key}`))
          .filter((i) => state.level !== "light" || i.importance === "important");
        return json({
          level: state.level,
          today: "2026-11-03",
          items: shown,
          snoozed: state.snoozed,
          failed_rules: state.failed,
        });
      }
      if (path === "/api/due/snoozes" && request.method === "PUT") {
        const b = body as { rule_key: string; subject_key: string; snooze: string };
        const found = state.all.find((i) => i.rule_key === b.rule_key && i.subject_key === b.subject_key)!;
        state.snoozed.push({ snooze_id: 7, until_on: b.snooze === "skip" ? null : "2026-11-10", item: found });
        return json({ id: 7, rule_key: b.rule_key, subject_key: b.subject_key, until_on: null });
      }
      if (path === "/api/rule-figures/9" && request.method === "DELETE") {
        state.all = state.all.filter((i) => i.rule_key !== "rule_figures.release_differs");
        return json({ key: "tax.car.rate_per_km", state: "shipped", capture: null });
      }
      if (path === "/api/due/snoozes/7" && request.method === "DELETE") {
        state.snoozed = [];
        return new Response(null, { status: 204 });
      }
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const content = () => screen.getByTestId("content");

function openMenu(title: string) {
  fireEvent.keyDown(screen.getByRole("button", { name: `Snooze options for ${title}` }), { key: "Enter" });
}

test("/ renders What's due, and important items come before the optional ones", async () => {
  const { router } = await renderAppAt("/");
  expect(await screen.findByRole("heading", { name: "What's due" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/");
  const matters = await screen.findByRole("heading", { name: "What matters most" });
  const useful = screen.getByRole("heading", { name: "Also useful" });
  expect(matters.compareDocumentPosition(useful) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const first = within(matters.closest("section")!);
  expect(first.getByText(CASH.title)).toBeInTheDocument();
  expect(first.getAllByText(CASH.effect)).toHaveLength(2);
  expect(first.getAllByRole("link", { name: "Update it here" })[0]).toHaveAttribute("href", "/accounts/1?from=due");
  expect(within(useful.closest("section")!).getByText(PROPERTY.title)).toBeInTheDocument();
});

test("Light shows important items only; choosing Steady saves on the Household and shows more", async () => {
  state.level = "light";
  await renderAppAt("/");
  await screen.findByRole("heading", { name: "What matters most" });
  expect(screen.queryByRole("heading", { name: "Also useful" })).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /Light/ })).toBeChecked();

  fireEvent.click(screen.getByRole("radio", { name: /Steady/ }));
  expect(await screen.findByRole("heading", { name: "Also useful" })).toBeInTheDocument();
  const put = calls.find((c) => c.method === "PUT" && c.path === "/api/household");
  expect(put?.body).toEqual({ name: "Sample household", effort_level: "steady" });
  expect(screen.getByRole("radio", { name: /Steady/ })).toBeChecked();
});

test("snoozing moves an item to the collapsed Snoozed section and Unsnooze brings it back", async () => {
  await renderAppAt("/");
  await screen.findByText(CASH.title);
  openMenu(CASH.title);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Snooze for one week" }));
  await waitFor(() => expect(screen.queryByText(CASH.title)).not.toBeInTheDocument());
  const put = calls.find((c) => c.method === "PUT" && c.path === "/api/due/snoozes");
  expect(put?.body).toEqual({ rule_key: CASH.rule_key, subject_key: CASH.subject_key, snooze: "week" });

  const toggle = await screen.findByRole("button", { name: /Snoozed \(1\)/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("button", { name: /Unsnooze/ })).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.getByText("Snoozed until 2026-11-10")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: `Unsnooze ${CASH.title}` }));
  expect(await screen.findByText(CASH.title)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/due/snoozes/7")).toBe(true);
});

test("the one overflow menu offers one week, one month and skip this period", async () => {
  await renderAppAt("/");
  await screen.findByText(CASH.title);
  openMenu(CASH.title);
  const labels = (await screen.findAllByRole("menuitem")).map((m) => m.textContent);
  expect(labels).toEqual(["Snooze for one week", "Snooze for one month", "Skip this period"]);
});

test("a check that could not run is named in muted text, and the rest still shows", async () => {
  state.failed = [{ rule_key: "tax.income_statement", name: "income statements" }];
  await renderAppAt("/");
  const line = await screen.findByText("Could not check income statements");
  expect(line).toHaveClass("text-muted-foreground");
  expect(screen.getByText(CASH.title)).toBeInTheDocument();
});

test("nothing due reads plainly, without praise or judgment", async () => {
  state.all = [];
  await renderAppAt("/");
  expect(await screen.findByText("Nothing to refresh right now.")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "What matters most" })).not.toBeInTheDocument();
});

test("the sidebar badge counts important items only, using the info style", async () => {
  await renderAppAt("/position");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  const link = await within(nav).findByRole("link", { name: /Home/ });
  const badge = await within(link).findByLabelText("2 important");
  expect(badge).toHaveTextContent("2");
  expect(badge.className).toContain("text-info");
});

test("Home is the first link in the sidebar, beside the Guide link, and not in a group", async () => {
  await renderAppAt("/position");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  const links = within(nav).getAllByRole("link");
  expect(links[0]).toHaveTextContent("Home");
  expect(links[1]).toHaveTextContent("Guide");
  const group = within(nav).getByRole("heading", { name: "Keep it current" }).parentElement!.parentElement!;
  expect(within(group).queryByRole("link", { name: /Home/ })).not.toBeInTheDocument();
});

test("the Finance360 name links home on desktop and in the phone header, and in the menu, which it closes", async () => {
  const { router } = await renderAppAt("/position");
  const brands = screen.getAllByRole("link", { name: "Finance360" });
  expect(brands).toHaveLength(2); // the sidebar and the phone header
  for (const brand of brands) expect(brand).toHaveAttribute("href", "/");

  fireEvent.click(brands[0]);
  await waitFor(() => expect(router.state.location.pathname).toBe("/"));

  await router.navigate({ to: "/position" });
  fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
  const menu = await screen.findByRole("dialog");
  const inMenu = within(menu).getByRole("link", { name: "Finance360" });
  expect(inMenu).toHaveAttribute("href", "/");
  fireEvent.click(inMenu);
  await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

const dueCalls = () => calls.filter((c) => c.method === "GET" && c.path === "/api/due").length;

test("a What's due link carries a marker, and the shell shows Back to What's due until you go back", async () => {
  const { router } = await renderAppAt("/");
  const update = (await screen.findAllByRole("link", { name: "Update it here" }))[0];
  expect(update).toHaveAttribute("href", "/accounts/1?from=due");
  expect(screen.queryByRole("link", { name: "Back to What's due" })).not.toBeInTheDocument();

  fireEvent.click(update);
  await waitFor(() => expect(router.state.location.pathname).toBe("/accounts/1"));
  const back = await within(content()).findByRole("link", { name: "Back to What's due" });
  expect(back).toHaveAttribute("href", "/");

  fireEvent.click(back);
  await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  expect(screen.queryByRole("link", { name: "Back to What's due" })).not.toBeInTheDocument();
});

test("the same shell link serves a destination that never declared the marker", async () => {
  await renderAppAt("/tax/records?from=due");
  expect(await within(content()).findByRole("link", { name: "Back to What's due" })).toBeInTheDocument();
});

test("there is no Back to What's due without the marker, or with another value", async () => {
  await renderAppAt("/accounts/1");
  await screen.findByTestId("content");
  expect(screen.queryByRole("link", { name: "Back to What's due" })).not.toBeInTheDocument();
  await renderAppAt("/accounts/1?from=elsewhere");
  expect(screen.queryByRole("link", { name: "Back to What's due" })).not.toBeInTheDocument();
});

test("going back refetches What's due, so an item that has been refreshed has gone", async () => {
  await renderAppAt("/");
  await screen.findByText(CASH.title);
  fireEvent.click((await screen.findAllByRole("link", { name: "Update it here" }))[0]);
  await within(content()).findByRole("link", { name: "Back to What's due" });

  state.all = state.all.filter((i) => i !== CASH); // a current Valuation was added meanwhile
  const before = dueCalls();
  dueDelayMs = 100; // the refetch is slow: the list from before must not show while it runs
  fireEvent.click(within(content()).getByRole("link", { name: "Back to What's due" }));
  expect(await screen.findByRole("heading", { name: "What's due" })).toBeInTheDocument();
  expect(screen.queryByText(CASH.title)).not.toBeInTheDocument();
  expect(await screen.findByText(SUPER.title)).toBeInTheDocument();
  expect(screen.queryByText(CASH.title)).not.toBeInTheDocument();
  expect(dueCalls()).toBeGreaterThan(before);
  expect(screen.getByText(SUPER.title)).toBeInTheDocument();
});

test("the screen never says overdue and uses no warning or destructive colour", async () => {
  state.snoozed = [{ snooze_id: 7, until_on: null, item: SUPER }];
  state.failed = [{ rule_key: "x", name: "something" }];
  await renderAppAt("/");
  await screen.findByRole("heading", { name: "What matters most" });
  fireEvent.click(screen.getByRole("button", { name: /Snoozed/ }));
  const html = content().innerHTML;
  expect(content().textContent).not.toMatch(/overdue|late|missed|failed|warning/i);
  expect(html).not.toMatch(/destructive|warning|text-red|bg-red/);
});

const DIFFERS = item({
  rule_key: "rule_figures.release_differs",
  subject_key: "capture:9@shipped:abc",
  title: "FY 2026-27: cents per km differs from release r7",
  effect: "Your captured figure (0.95) is in use. The release ships 0.90.",
  link: "/rule-figures?fy=2027",
  actions: [
    { kind: "keep_mine", label: "Keep mine", capture_id: 9 },
    { kind: "use_shipped", label: "Use shipped", capture_id: 9 },
  ],
});

test("a Rule figure item links to the screen at its year and Keep mine acknowledges it", async () => {
  state.all = [DIFFERS];
  await renderAppAt("/");
  await screen.findByText(DIFFERS.title);
  expect(screen.getByRole("link", { name: "Update it here" })).toHaveAttribute("href", "/rule-figures?fy=2027&from=due");
  fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
  await waitFor(() => expect(screen.queryByText(DIFFERS.title)).not.toBeInTheDocument());
  const put = calls.find((c) => c.method === "PUT" && c.path === "/api/due/snoozes");
  expect(put?.body).toEqual({ rule_key: DIFFERS.rule_key, subject_key: DIFFERS.subject_key, snooze: "skip" });
});

test("Use shipped asks first, then withdraws the captured figure", async () => {
  state.all = [DIFFERS];
  await renderAppAt("/");
  await screen.findByText(DIFFERS.title);
  fireEvent.click(screen.getByRole("button", { name: "Use shipped" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/rule-figures/9")).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Withdraw figure" }));
  await waitFor(() => expect(screen.queryByText(DIFFERS.title)).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/rule-figures/9")).toBe(true);
});

const TAX = item({
  rule_key: "tax.income_statement",
  subject_key: "member:1@2026",
  title: "No income statement for FY 2025-26",
  effect: "An income statement makes your Tax estimate more accurate.",
  link: "/tax/records",
});

test("cards are the default: grouped by area with a count, each with a word badge, area, age and one marked link", async () => {
  prefs = {};
  state.all = [CASH, SUPER, PROPERTY, TAX];
  await renderAppAt("/");
  const keep = await within(content()).findByRole("heading", { name: /Keep it current/ });
  expect(keep).toHaveTextContent("Keep it current (3)");
  expect(within(content()).getByRole("heading", { name: /This tax year/ })).toHaveTextContent("This tax year (1)");
  expect(screen.queryByRole("heading", { name: "What matters most" })).not.toBeInTheDocument();
  const group = within(keep.closest("section")!);
  const cards = group.getAllByRole("listitem");
  expect(cards).toHaveLength(3);
  const first = within(cards[0]);
  expect(first.getByText("Important")).toBeInTheDocument();
  expect(first.getByText("Keep it current")).toBeInTheDocument();
  expect(first.getByText(CASH.title)).toBeInTheDocument();
  expect(first.getByText(CASH.effect)).toBeInTheDocument();
  expect(first.getByText("Worth refreshing since 1 July 2026 (4 months ago)")).toBeInTheDocument();
  expect(first.getByRole("link", { name: "Update it here" })).toHaveAttribute("href", "/accounts/1?from=due");
  expect(within(cards[2]).getByText("Optional")).toBeInTheDocument();
  // Snooze is the list's own.
  openMenu(CASH.title);
  fireEvent.click(await screen.findByRole("menuitem", { name: "Snooze for one week" }));
  await waitFor(() => expect(screen.queryByText(CASH.title)).not.toBeInTheDocument());
  expect(await screen.findByRole("button", { name: /Snoozed \(1\)/ })).toBeInTheDocument();
  expect(within(content()).getByRole("heading", { name: /Keep it current/ })).toHaveTextContent("Keep it current (2)");
});

test("the Show as switch is a labelled radio group; List keeps the choice and Use as my default stores the default", async () => {
  prefs = {};
  await renderAppAt("/");
  const group = await screen.findByRole("group", { name: "Show as" });
  const cards = within(group).getByRole("radio", { name: "Cards" });
  const list = within(group).getByRole("radio", { name: "List" });
  expect(cards).toBeChecked();
  expect(screen.queryByRole("button", { name: "Use as my default" })).not.toBeInTheDocument();

  fireEvent.click(list);
  expect(await screen.findByRole("heading", { name: "What matters most" })).toBeInTheDocument();
  await waitFor(() => expect(puts).toContainEqual({ "whats-due.items": "list" }));
  expect(puts.some((p) => "default.collection" in p)).toBe(false);

  fireEvent.click(await screen.findByRole("button", { name: "Use as my default" }));
  await waitFor(() => expect(puts).toContainEqual({ "default.collection": "list" }));
});

test("the choice persists across a refresh: the list shows again with the same items and wording", async () => {
  prefs = {};
  await renderAppAt("/");
  const cardTitles = (await within(content()).findAllByRole("listitem")).map((li) => li.textContent ?? "");
  fireEvent.click(await screen.findByRole("radio", { name: "List" }));
  await waitFor(() => expect(prefs["whats-due.items"]).toBe("list"));
  cleanup(); // a refresh: a new page, a new cache, the server's stored preference

  await renderAppAt("/");
  expect(await screen.findByRole("heading", { name: "What matters most" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "List" })).toBeChecked();
  const listText = content().textContent ?? "";
  for (const i of [CASH, SUPER, PROPERTY]) {
    expect(cardTitles.some((t) => t.includes(i.title) && t.includes(i.effect))).toBe(true);
    expect(listText).toContain(i.title);
    expect(listText).toContain(i.effect);
  }
});

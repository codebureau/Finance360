import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { SIDEBAR_GROUPS, TOP_ITEMS } from "@/components/sidebar-nav";
import { findGuidePage, GUIDE_PAGES, guidePath, linksIn } from "@/lib/guide";
import { createAppRouter } from "@/routes";
import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/health") return Promise.resolve(json({ status: "healthy", git_sha: "abc1234", version: "v1.2.3", schema_version: "0004" }));
      if (path === "/api/backup/status") {
        return Promise.resolve(
          json({
            configured: false,
            remote_configured: false,
            stale: false,
            stale_after_hours: 36,
            latest: null,
            last_success: null,
            maintenance: null,
          }),
        );
      }
      return Promise.resolve(json({}));
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

/** The routes the app really registers, e.g. `/tax/records/$recordId`, as the router reports them. */
const ROUTE_PATTERNS = Object.keys(createAppRouter().routesByPath);

/** Whether a path (no query or hash) is a screen the app serves. */
function routeExists(path: string): boolean {
  return ROUTE_PATTERNS.some((pattern) => new RegExp(`^${pattern.replace(/\$[^/]+/g, "[^/]+")}$`).test(path));
}

/** Where a Guide link goes, or null for an outside link: a Guide page, an app route, else it is broken. */
function resolves(target: string): boolean {
  if (/^https:\/\//.test(target)) return true;
  if (!target.startsWith("/")) return false;
  const path = target.split(/[?#]/)[0];
  if (path === "/guide") return true;
  const slug = /^\/guide\/([^/]+)$/.exec(path)?.[1];
  if (slug) return findGuidePage(slug) !== undefined;
  return routeExists(path);
}

test("the checker itself rejects a page or route that does not exist", () => {
  expect(resolves("/guide/tax")).toBe(true);
  expect(resolves("/tax/records?fy=2026")).toBe(true);
  expect(resolves("/tax/records/12")).toBe(true);
  expect(resolves("/guide/no-such-page")).toBe(false);
  expect(resolves("/no/such/screen")).toBe(false);
  expect(resolves("tax")).toBe(false);
});

test("every link in every Guide page points to a Guide page or an app route that exists", () => {
  const broken: string[] = [];
  let checked = 0;
  for (const page of GUIDE_PAGES) {
    for (const { target } of linksIn(page.body)) {
      checked++;
      if (!resolves(target)) broken.push(`${page.slug}: ${target}`);
    }
  }
  expect(checked).toBeGreaterThan(30);
  expect(broken).toEqual([]);
});

test("the Guide opens with Start here and has a page for every area", () => {
  expect(GUIDE_PAGES[0].slug).toBe("start-here");
  expect(GUIDE_PAGES[0].title).toMatch(/why are you here/);
  const slugs = GUIDE_PAGES.map((p) => p.slug);
  for (const slug of [
    "what-finance360-answers",
    "how-the-pieces-fit",
    "what-to-provide",
    "tax",
    "position",
    "accounts-and-imports",
    "projection",
    "exposure",
  ]) {
    expect(slugs).toContain(slug);
  }
  expect(new Set(slugs).size).toBe(slugs.length);
  expect(guidePath("start-here")).toBe("/guide");
  expect(guidePath("tax")).toBe("/guide/tax");
});

test("every sidebar group links to a Guide page that exists", () => {
  for (const group of SIDEBAR_GROUPS) {
    expect(group.guide, group.heading).toMatch(/^\/guide\/[^/]+$/);
    expect(resolves(group.guide), group.heading).toBe(true);
  }
});

// Every destination the sidebar had before it was regrouped by question.
// Plus Rule figures and the home screen, What's due, which sits above the groups with the Guide link.
const EARLIER_DESTINATIONS = [
  "/",
  "/position",
  "/exposure",
  "/projection",
  "/tax",
  "/tax/reconciliation",
  "/accounts",
  "/tax/records",
  "/tax/whatifs",
  "/tax/estimates",
  "/tax/lodged",
  "/library",
  "/income-streams",
  "/instruments",
  "/imports",
  "/household",
  "/fx-rates",
  "/reference-data",
  "/rule-figures",
  "/categories",
  "/audit",
  "/settings",
];

test("the sidebar groups are the agreed questions, and every earlier destination is still in one", () => {
  expect(SIDEBAR_GROUPS.map((g) => g.heading)).toEqual([
    "Where we stand",
    "Where we're heading",
    "This tax year",
    "Keep it current",
    "Your documents",
    "Reference and system",
  ]);
  const destinations = SIDEBAR_GROUPS.flatMap((g) => g.items.map((i) => i.to));
  const top = TOP_ITEMS.map((i) => i.to);
  expect(top).toEqual(["/", "/recent-changes", "/guide"]);
  expect([...destinations, "/"].sort()).toEqual([...EARLIER_DESTINATIONS].sort());
  for (const to of destinations) expect(routeExists(to), to).toBe(true);
});

test("the rendered sidebar has a Guide link on every group heading, and a Guide link at the top", async () => {
  await renderAppAt("/position");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  for (const group of SIDEBAR_GROUPS) {
    expect(within(nav).getByRole("heading", { name: group.heading })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: `Guide: ${group.heading}` })).toHaveAttribute("href", group.guide);
    for (const item of group.items) {
      expect(within(nav).getByRole("link", { name: item.label })).toHaveAttribute("href", item.to);
    }
  }
  expect(within(nav).getByRole("link", { name: "Guide" })).toHaveAttribute("href", "/guide");
});

test("/guide opens Start here, and each page loads by direct URL with its title", async () => {
  const { router } = await renderAppAt("/guide");
  const content = screen.getByTestId("content");
  expect(await within(content).findByRole("heading", { level: 1, name: GUIDE_PAGES[0].title })).toBeInTheDocument();
  for (const page of GUIDE_PAGES.slice(1)) {
    router.navigate({ to: guidePath(page.slug) });
    expect(await within(content).findByRole("heading", { level: 1, name: page.title })).toBeInTheDocument();
  }
});

test("a Guide link inside a page moves to that screen, and the page row marks where you are", async () => {
  const { router } = await renderAppAt("/guide");
  const content = screen.getByTestId("content");
  await within(content).findByRole("heading", { level: 1 });
  const pages = within(content).getByRole("navigation", { name: "Guide pages" });
  expect(within(pages).getByRole("link", { name: GUIDE_PAGES[0].title })).toHaveAttribute("aria-current", "page");
  fireEvent.click(within(content).getAllByRole("link", { name: "Keep track of tax" })[0]);
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/guide/path-tax"));
  fireEvent.click(await within(content).findByRole("link", { name: "Tax records" }));
  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/tax/records"));
});

test("the diagram on How the pieces fit is real text, and marks what is coming later", async () => {
  await renderAppAt("/guide/how-the-pieces-fit");
  const figure = await screen.findByRole("figure", { name: "How the pieces fit" });
  expect(within(figure).getAllByRole("listitem").length).toBeGreaterThanOrEqual(4);
  expect(within(figure).getByText(/Coming later/)).toBeInTheDocument();
});

test("every starting-point page (path-*) is listed on Start here, so adding one is a page plus an entry", () => {
  const start = linksIn(GUIDE_PAGES[0].body).map((l) => l.target);
  const paths = GUIDE_PAGES.filter((p) => p.slug.startsWith("path-"));
  expect(paths.length).toBeGreaterThanOrEqual(3);
  for (const p of paths) expect(start, p.slug).toContain(guidePath(p.slug));
});

test("an unknown Guide page says so and offers the way back", async () => {
  await renderAppAt("/guide/no-such-page");
  const content = screen.getByTestId("content");
  expect(await within(content).findByRole("heading", { name: "Guide page not found" })).toBeInTheDocument();
  expect(within(content).getByRole("link", { name: "Back to Start here" })).toHaveAttribute("href", "/guide");
});

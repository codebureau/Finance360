import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const NO_FIGURES = { household: "0.00", joint: "0.00", members: [] };
const EMPTY_POSITION = {
  as_at: "2026-10-17",
  tag: null,
  members: [],
  tags: [],
  classes: [
    { class: "asset", figures: NO_FIGURES, flagged: 0, categories: [] },
    { class: "liability", figures: NO_FIGURES, flagged: 0, categories: [] },
  ],
  net: NO_FIGURES,
  flagged: 0,
};

/** Routes fetch by path: the page also loads the Household and the catalogue. */
function stubBackend(health: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/health") return Promise.resolve(health);
      if (path === "/api/backup/status")
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
      if (path === "/api/accounts" || path === "/api/tags" || path === "/api/instruments")
        return Promise.resolve(json([]));
      if (/^\/api\/accounts\/\d+$/.test(path)) return Promise.resolve(json({ message: "Account not found." }, 404));
      if (path === "/api/due")
        return Promise.resolve(json({ level: "light", today: "2026-10-17", items: [], snoozed: [], failed_rules: [] }));
      if (path === "/api/fx-rates") return Promise.resolve(json([]));
      if (path === "/api/auth/security") return Promise.resolve(json({ totp_enabled: false }));
      if (path === "/api/audit") return Promise.resolve(json({ items: [], total: 0, limit: 25, offset: 0 }));
      if (path === "/api/position/history") {
        return Promise.resolve(json({ tag: null, members: [], tags: [], points: [] }));
      }
      if (path === "/api/position") return Promise.resolve(json(EMPTY_POSITION));
      if (path === "/api/catalogue") return Promise.resolve(json({ version: 1, classes: [] }));
      if (path === "/api/instruments") return Promise.resolve(json([]));
      return Promise.resolve(json({ name: "My household", members: [] }));
    }),
  );
}

const HEALTHY = () => stubBackend(json({ status: "healthy", git_sha: "abc1234", version: "v1.2.3", schema_version: "0004" }));

beforeEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

test("shows status, version and schema version from /health", async () => {
  stubBackend(json({ status: "healthy", git_sha: "abc1234", version: "v1.2.3", schema_version: "0001" }));
  await renderAppAt();
  expect(await screen.findByText("healthy")).toBeInTheDocument();
  expect(screen.getByText("v1.2.3")).toBeInTheDocument();
  expect(screen.getByText("abc1234")).toBeInTheDocument();
  expect(screen.getByText("Schema version")).toBeInTheDocument();
  expect(screen.getByText("0001")).toBeInTheDocument();
});

test("a build with no release tag reads dev build, with the commit", async () => {
  stubBackend(json({ status: "healthy", git_sha: "abc1234", version: "dev", schema_version: "0001" }));
  await renderAppAt();
  expect(await screen.findByText("dev build")).toBeInTheDocument();
  expect(screen.getByText("abc1234")).toBeInTheDocument();
});

test("shows an error when the backend is unreachable", async () => {
  stubBackend(new Response("nope", { status: 503 }));
  await renderAppAt();
  expect(await screen.findByText(/Unreachable/)).toBeInTheDocument();
});

test("/ is the What's due home screen, not a redirect", async () => {
  HEALTHY();
  const { router } = await renderAppAt("/");
  expect(await screen.findByRole("heading", { name: "What's due" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/");
});

const ROUTES: [string, string][] = [
  ["/position", "Position"],
  ["/projection", "Projection"],
  ["/accounts", "Accounts"],
  ["/accounts/1", "Account not found"],
  ["/instruments", "Instruments"],
  ["/instruments/2", "Instrument not found"],
  ["/household", "Household"],
  ["/fx-rates", "FX rates"],
  ["/categories", "Categories"],
  ["/audit", "Audit trail"],
  ["/settings", "Settings"],
];

test.each(ROUTES)("%s loads by direct URL inside the shell", async (path, heading) => {
  HEALTHY();
  await renderAppAt(path);
  expect(await within(screen.getByTestId("content")).findByRole("heading", { name: heading })).toBeInTheDocument();
});

test("an unknown path shows NotFound inside the shell, with the sidebar still there", async () => {
  HEALTHY();
  await renderAppAt("/no/such/page");
  const content = screen.getByTestId("content");
  expect(await within(content).findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Sections" })).toBeInTheDocument();
});

test("the sidebar groups the sections, marks the active one and drives the URL, back and forward", async () => {
  HEALTHY();
  const { router } = await renderAppAt("/position");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  for (const heading of ["Where we stand", "Where we're heading", "This tax year", "Keep it current", "Reference and system"]) {
    expect(within(nav).getByRole("heading", { name: heading })).toBeInTheDocument();
  }
  expect(within(nav).getByRole("link", { name: "Position" })).toHaveAttribute("aria-current", "page");

  fireEvent.click(within(nav).getByRole("link", { name: "Categories" }));
  expect(await screen.findByRole("heading", { name: "Categories" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/categories");
  expect(within(nav).getByRole("link", { name: "Categories" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "Position" })).not.toHaveAttribute("aria-current");

  fireEvent.click(within(nav).getByRole("link", { name: "Settings" }));
  expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();

  act(() => router.history.back());
  await waitFor(() => expect(router.state.location.pathname).toBe("/categories"));
  expect(await screen.findByRole("heading", { name: "Categories" })).toBeInTheDocument();
  act(() => router.history.forward());
  await waitFor(() => expect(router.state.location.pathname).toBe("/settings"));
});

test("a nested path keeps the parent section active", async () => {
  HEALTHY();
  await renderAppAt("/accounts/1");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(within(nav).getByRole("link", { name: "Accounts" })).toHaveAttribute("aria-current", "page");
});

test("the sidebar foot holds the status, sign out and the theme toggle", async () => {
  HEALTHY();
  await renderAppAt();
  const aside = screen.getByRole("complementary");
  expect(await within(aside).findByText("healthy")).toBeInTheDocument();
  expect(within(aside).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  expect(within(aside).getByRole("button", { name: /dark theme/i })).toBeInTheDocument();
});

test("the menu button opens a Sheet with the navigation and the status, and following a link closes it", async () => {
  HEALTHY();
  const { router } = await renderAppAt("/position");
  fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("navigation", { name: "Sections" })).toBeInTheDocument();
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByText("healthy")).toBeInTheDocument());
  fireEvent.click(within(sheet).getByRole("link", { name: "FX rates" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/fx-rates"));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("the content area uses the width instead of a narrow column", async () => {
  HEALTHY();
  await renderAppAt();
  const content = screen.getByTestId("content");
  expect(content).toHaveClass("max-w-7xl");
  expect(content).not.toHaveClass("max-w-3xl");
});

test("theme toggle switches between light and dark and persists", async () => {
  stubBackend(new Response("{}", { status: 500 }));
  await renderAppAt();
  fireEvent.click(screen.getAllByRole("button", { name: /dark theme/i })[0]);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("theme")).toBe("dark");
  fireEvent.click(screen.getAllByRole("button", { name: /light theme/i })[0]);
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(localStorage.getItem("theme")).toBe("light");
});

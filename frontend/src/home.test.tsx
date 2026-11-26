import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fig = (household: string) => ({ household, joint: "0.00", members: [] });
const point = (on: string, net: string, flagged = 0) => ({
  on,
  assets: fig(net),
  liabilities: fig("0.00"),
  net: fig(net),
  flagged,
});
const position = (flagged = 0) => ({
  as_at: "2026-11-26",
  tag: null,
  members: [],
  tags: [],
  classes: [
    { class: "asset", figures: fig("251200.50"), flagged: 0, categories: [] },
    { class: "liability", figures: fig("-150000.00"), flagged: 0, categories: [] },
  ],
  net: fig("101200.50"),
  flagged,
});

function stub(points: ReturnType<typeof point>[], flagged = 0) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1", schema_version: "0004" });
      if (path === "/api/position") return json(position(flagged));
      if (path === "/api/position/history") return json({ tag: null, members: [], tags: [], points });
      if (path === "/api/household") {
        return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: "steady", members: [] });
      }
      if (path === "/api/due") return json({ level: "steady", today: "2026-11-26", items: [], snoozed: [], failed_rules: [] });
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());

const tile = async () => within(await screen.findByRole("region", { name: "Net position" }));

test("Home: header, the Net position tile above What's due, and a link to Position", async () => {
  stub([point("2026-10-31", "100000.00"), point("2026-11-26", "101200.50")]);
  const { router } = await renderAppAt("/");
  expect(await screen.findByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
  const t = await tile();
  expect(await t.findByTestId("stat-hero")).toHaveTextContent("101,200.50");
  expect(t.getByText("251,200.50")).toBeInTheDocument();
  expect(t.getByText("-150,000.00")).toBeInTheDocument();
  expect(t.getByText("Net position is up since last month")).toBeInTheDocument();
  expect(await t.findByTestId("net-delta")).toHaveTextContent("+1,200.50up 1,200.50 since 31 Oct");
  expect(t.getByRole("link", { name: "Open Position" })).toHaveAttribute("href", "/position");
  const due = await screen.findByRole("heading", { level: 2, name: "What's due" });
  const tileEl = screen.getByRole("region", { name: "Net position" });
  expect(tileEl.compareDocumentPosition(due) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(router.state.location.pathname).toBe("/");
});

test("Home: a first month with no previous month-end shows no delta", async () => {
  stub([point("2026-11-26", "101200.50")]);
  await renderAppAt("/");
  const t = await tile();
  expect(await t.findByTestId("stat-hero")).toHaveTextContent("101,200.50");
  expect(t.queryByTestId("net-delta")).not.toBeInTheDocument();
  expect(t.getByText("Net position as at 2026-11-26.")).toBeInTheDocument();
});

test("Home: says how many Accounts are not counted, as Position does", async () => {
  stub([point("2026-10-31", "101200.50"), point("2026-11-26", "101200.50")], 2);
  await renderAppAt("/");
  const t = await tile();
  expect(await t.findByText("2 not counted")).toBeInTheDocument();
  expect(t.getByText(/2 Accounts are not in these totals/)).toBeInTheDocument();
  expect(t.getByTestId("net-delta")).toHaveTextContent(/^unchanged since 31 Oct$/);
});

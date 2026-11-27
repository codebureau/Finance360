import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fig = (household: string) => ({ household, joint: "0.00", members: [] });
const position = {
  as_at: "2026-11-26",
  tag: null,
  members: [],
  tags: [],
  classes: [
    { class: "asset", figures: fig("251200.50"), flagged: 0, categories: [] },
    { class: "liability", figures: fig("-150000.00"), flagged: 0, categories: [] },
  ],
  net: fig("101200.50"),
  flagged: 0,
};

const question = (over: Record<string, unknown>) => ({
  key: "own-and-owe",
  wording: "What do we own and owe?",
  screen: "/position",
  area: "Where we stand",
  ask_prompt: "Ask words",
  state: "ready",
  state_word: "Ready",
  count: 0,
  reason: "Everything this answer needs is current.",
  next_step: null,
  ...over,
});

const step = {
  input_key: "position.freshness",
  input_name: "Fresh balances",
  kind: "stale",
  title: "Your Position uses a balance for Sample Home Loan from 30 September 2026",
  effect: "A current balance makes your net position more accurate.",
  link: "/accounts/9",
};

function stub(questions: unknown[], headline: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1", schema_version: "0004" });
      if (path === "/api/position") return json(position);
      if (path === "/api/position/history") return json({ tag: null, members: [], tags: [], points: [] });
      if (path === "/api/household") {
        return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: "steady", members: [] });
      }
      if (path === "/api/due") return json({ level: "steady", today: "2026-11-26", items: [], snoozed: [], failed_rules: [] });
      if (path === "/api/questions") return json({ today: "2026-11-26", level: "steady", headline, questions });
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());

const card = async (wording: string) => within(await screen.findByRole("region", { name: wording }));

test("Home: Your questions sits between the Net position tile and What's due, under one headline sentence", async () => {
  stub(
    [
      question({}),
      question({
        key: "on-track-for-retirement",
        wording: "Are we on track for retirement?",
        screen: "/projection",
        state: "partly_ready",
        state_word: "Partly ready",
        count: 1,
        reason: step.title,
        next_step: step,
      }),
    ],
    "1 of 2 questions is ready; Are we on track for retirement? is partly ready",
  );
  await renderAppAt("/");
  const headline = await screen.findByTestId("questions-headline");
  expect(headline).toHaveTextContent("1 of 2 questions is ready; Are we on track for retirement? is partly ready");
  const net = screen.getByRole("region", { name: "Net position" });
  const group = screen.getByRole("region", { name: "Your questions" });
  const due = await screen.findByRole("heading", { level: 2, name: "What's due" });
  expect(net.compareDocumentPosition(group) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(group.compareDocumentPosition(due) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // no gauge, no score and no percentage anywhere in the group
  expect(group.textContent).not.toMatch(/%|score|gauge/i);
});

test("a Question card shows its status as a word, the count, the reason and the next step", async () => {
  stub(
    [question({ state: "partly_ready", state_word: "Partly ready", count: 1, reason: step.title, next_step: step })],
    "0 of 1 questions are ready; What do we own and owe? is partly ready",
  );
  await renderAppAt("/");
  const c = await card("What do we own and owe?");
  expect(await c.findByTestId("stat-severity")).toHaveTextContent("Partly ready");
  expect(c.getByTestId("question-count")).toHaveTextContent("1");
  expect(c.getByText("input needs attention")).toBeInTheDocument();
  expect(c.getByText(step.title)).toBeInTheDocument();
  expect(c.getByText(step.effect)).toBeInTheDocument();
  expect(c.getByRole("link", { name: "Take the next step" })).toHaveAttribute("href", "/accounts/9");
  expect(c.getByRole("link", { name: "Show items" })).toHaveAttribute("href", "/position");
});

test("a next step that carries a query keeps it as search parameters", async () => {
  stub(
    [
      question({
        key: "tax-this-year",
        wording: "How much tax will we pay, or get back?",
        screen: "/tax",
        state: "not_ready",
        state_word: "Not ready",
        count: 2,
        reason: "Your Tax estimate for FY 2026-27 cannot be worked out yet",
        next_step: { ...step, link: "/rule-figures?fy=2027&missing=1" },
      }),
    ],
    "0 of 1 questions are ready; How much tax will we pay, or get back? is not ready",
  );
  await renderAppAt("/");
  const c = await card("How much tax will we pay, or get back?");
  expect(await c.findByRole("link", { name: "Take the next step" })).toHaveAttribute(
    "href",
    "/rule-figures?fy=2027&missing=1",
  );
  expect(c.getByTestId("question-count")).toHaveTextContent("2");
  expect(c.getByText("inputs need attention")).toBeInTheDocument();
});

test.each([
  ["ready", "Ready", "bg-success"],
  ["partly_ready", "Partly ready", "bg-warning"],
  ["not_ready", "Not ready", "bg-destructive"],
  ["not_started", "Not started", "bg-info"],
  ["could_not_check", "Could not check", "bg-muted-foreground/55"],
])("%s wears its tone, an icon and its word, so colour is never the only cue", async (state, word, bar) => {
  stub([question({ state, state_word: word, count: state === "ready" ? 0 : 1 })], "0 of 1 questions are ready");
  await renderAppAt("/");
  const c = await card("What do we own and owe?");
  const badge = await c.findByTestId("stat-severity");
  expect(badge).toHaveTextContent(word);
  expect(badge.querySelector("svg")).not.toBeNull();
  const tile = screen.getByRole("region", { name: "What do we own and owe?" });
  expect(tile.previousElementSibling).toHaveClass(bar);
});

test("a Ready Question has no next step, only Show items", async () => {
  stub([question({})], "All 1 questions are ready.");
  await renderAppAt("/");
  const c = await card("What do we own and owe?");
  expect(await c.findByText("Everything this answer needs is current.")).toBeInTheDocument();
  expect(c.queryByRole("link", { name: "Take the next step" })).not.toBeInTheDocument();
  expect(c.getByRole("link", { name: "Show items" })).toBeInTheDocument();
  expect(c.getByTestId("question-count")).toHaveTextContent("0");
});

import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const ASK_PROMPT = "How ready is the picture of what we own and owe, and what should we do next?";

const STALE_STEP = {
  input_key: "position.freshness",
  input_name: "Fresh balances",
  kind: "stale",
  title: "Your Position uses a balance for Sample Home Loan from 30 September 2026",
  effect: "A current balance makes your net position more accurate.",
  link: "/accounts/9",
};

const base = {
  key: "own-and-owe",
  wording: "What do we own and owe?",
  screen: "/position",
  area: "Where we stand",
  ask_prompt: ASK_PROMPT,
};

const input = (over: Record<string, unknown>) => ({
  key: "position.accounts",
  name: "Accounts",
  importance: "important",
  state: "ready",
  state_word: "Ready",
  asked: true,
  counted: false,
  reasons: [],
  ...over,
});

const PARTLY = {
  ...base,
  state: "partly_ready",
  state_word: "Partly ready",
  count: 1,
  reason: STALE_STEP.title,
  next_step: STALE_STEP,
  inputs: [
    input({}),
    input({ key: "position.figures", name: "Account figures" }),
    input({
      key: "position.freshness",
      name: "Fresh balances",
      state: "partly_ready",
      state_word: "Partly ready",
      counted: true,
      reasons: [
        { kind: "stale", title: STALE_STEP.title, effect: STALE_STEP.effect, link: "/accounts/9", material: true },
        { kind: "stale", title: "A small old balance for Sample Spare", effect: "Tiny.", link: "/accounts/4", material: false },
      ],
    }),
    input({
      key: "position.extra",
      name: "Optional extra",
      importance: "optional",
      asked: false,
      state: "not_started",
      state_word: "Not started",
    }),
  ],
};

const READY = {
  ...base,
  state: "ready",
  state_word: "Ready",
  count: 0,
  reason: "Everything this answer needs is current.",
  next_step: null,
  inputs: [input({}), input({ key: "position.figures", name: "Account figures" })],
};

let detail: Record<string, unknown>;
let asked: { question: string; screen: string | null }[];

beforeEach(() => {
  detail = PARTLY;
  asked = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1", schema_version: "0004" });
      if (path === "/api/due") return json({ level: "steady", today: "2026-11-26", items: [], snoozed: [], failed_rules: [] });
      if (path === "/api/ai/status") {
        return json({ enabled: false, reason: "off", resets_on: null, retrieval: "keyword", answering: false });
      }
      if (path === "/api/ask") {
        const body = (await request.json()) as { question: string; screen: string | null };
        asked.push({ question: body.question, screen: body.screen });
        return json({ found: false, message: "Nothing found.", start: null, passages: [] });
      }
      if (path === "/api/questions") {
        const { inputs: _drop, ...card } = detail;
        void _drop;
        return json({
          today: "2026-11-26",
          level: "steady",
          headline: "head",
          questions: [
            card,
            { ...card, key: "tax-this-year", wording: "How much tax will we pay, or get back?", screen: "/tax", area: "This tax year" },
          ],
        });
      }
      if (path === "/api/questions/own-and-owe") return json(detail);
      if (path.startsWith("/api/questions/")) return json({ errors: [{ field: "", message: "There is no question." }] }, 404);
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const content = () => within(screen.getByTestId("content"));
const bar = () => screen.queryByRole("region", { name: "Return" });

test("the Question page leads with the same tile as its card, then the inputs in order with their states", async () => {
  await renderAppAt("/questions/own-and-owe");
  expect(await screen.findByRole("heading", { level: 1, name: "What do we own and owe?" })).toBeInTheDocument();
  const tile = within(await screen.findByRole("region", { name: "What do we own and owe?" }));
  expect(tile.getByTestId("stat-severity")).toHaveTextContent("Partly ready");
  expect(tile.getByTestId("question-count")).toHaveTextContent("1");
  expect(tile.getByText(STALE_STEP.title)).toBeInTheDocument();
  expect(tile.getByRole("link", { name: "See the answer" })).toHaveAttribute("href", "/position");
  expect(tile.getByRole("link", { name: "Take the next step" })).toHaveAttribute("href", "/accounts/9?from=q.own-and-owe");

  const rows = await screen.findAllByTestId("question-input");
  expect(rows.map((r) => r.getAttribute("data-input"))).toEqual([
    "position.accounts",
    "position.figures",
    "position.freshness",
    "position.extra",
  ]);
  // each input names its state in words (an icon accompanies the word)
  expect(within(rows[0]).getByText("Ready")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Partly ready")).toBeInTheDocument();
  expect(within(rows[2]).getByRole("link", { name: "Fix it here" })).toHaveAttribute("href", "/accounts/9?from=q.own-and-owe");
  // a small gap is listed muted, apart from what needs doing
  const small = within(within(rows[2]).getByTestId("small-gaps"));
  expect(small.getByText(/A small old balance for Sample Spare/)).toBeInTheDocument();
  expect(within(rows[3]).getByText("Not asked at your Effort level")).toBeInTheDocument();
  expect(within(rows[3]).getByText("Optional")).toBeInTheDocument();
});

test("an unknown Question says so, calmly, with a way home", async () => {
  await renderAppAt("/questions/no-such-question");
  expect(await screen.findByRole("heading", { name: "Question not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to Home" })).toHaveAttribute("href", "/");
});

test("the sidebar has Your questions built from the registry, its heading linking the Guide", async () => {
  await renderAppAt("/position");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  expect(await within(nav).findByRole("link", { name: "What do we own and owe?" })).toHaveAttribute(
    "href",
    "/questions/own-and-owe",
  );
  expect(within(nav).getByRole("link", { name: "How much tax will we pay, or get back?" })).toHaveAttribute(
    "href",
    "/questions/tax-this-year",
  );
  expect(within(nav).getByRole("heading", { name: "Your questions" })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: "Guide: Your questions" })).toHaveAttribute(
    "href",
    "/guide/what-finance360-answers",
  );
  // the first group under the top items, before Where we stand
  const headings = within(nav)
    .getAllByRole("heading")
    .map((h) => h.textContent);
  expect(headings.slice(0, 2)).toEqual(["Your questions", "Where we stand"]);
});

test("a fix link from a Question shows Back to your next step with the next thing required, and it survives a reload", async () => {
  await renderAppAt("/accounts/9?from=q.own-and-owe");
  const back = await content().findByRole("link", { name: "Back to your next step" });
  expect(back).toHaveAttribute("href", "/questions/own-and-owe");
  expect(await screen.findByTestId("return-next")).toHaveTextContent(`What do we own and owe? Next: ${STALE_STEP.title}`);
  expect(screen.queryByRole("link", { name: "Back to What's due" })).not.toBeInTheDocument();

  // a reload of the same tab, the marker no longer in the URL: the bar is remembered
  cleanup();
  await renderAppAt("/accounts/1");
  expect(await content().findByRole("link", { name: "Back to your next step" })).toBeInTheDocument();
});

test("the bar survives moving within the fix, and a new return link replaces it", async () => {
  const { router } = await renderAppAt("/accounts/9?from=q.own-and-owe");
  await content().findByRole("link", { name: "Back to your next step" });
  await router.navigate({ to: "/imports" });
  expect(await content().findByRole("link", { name: "Back to your next step" })).toBeInTheDocument();
  await router.navigate({ to: "/accounts/1", search: { from: "due" } });
  expect(await content().findByRole("link", { name: "Back to What's due" })).toBeInTheDocument();
  expect(content().queryByRole("link", { name: "Back to your next step" })).not.toBeInTheDocument();
});

test("dismissing the bar ends it for the tab", async () => {
  const { router } = await renderAppAt("/accounts/9?from=q.own-and-owe");
  await content().findByRole("link", { name: "Back to your next step" });
  fireEvent.click(content().getByRole("button", { name: "Dismiss" }));
  expect(bar()).toBeNull();
  await router.navigate({ to: "/imports" });
  expect(bar()).toBeNull();
});

test("fixing an input and following Back to your next step shows the card improved and the next step moved on", async () => {
  await renderAppAt("/questions/own-and-owe");
  const tile = within(await screen.findByRole("region", { name: "What do we own and owe?" }));
  fireEvent.click(await tile.findByRole("link", { name: "Take the next step" }));
  await content().findByRole("link", { name: "Back to your next step" });

  detail = READY; // a current balance was recorded meanwhile
  fireEvent.click(content().getByRole("link", { name: "Back to your next step" }));
  const after = within(await screen.findByRole("region", { name: "What do we own and owe?" }));
  await waitFor(() => expect(after.getByTestId("stat-severity")).toHaveTextContent("Ready"));
  expect(after.getByTestId("question-count")).toHaveTextContent("0");
  expect(after.queryByRole("link", { name: "Take the next step" })).not.toBeInTheDocument();
  expect(bar()).toBeNull();
  expect(screen.queryByText(STALE_STEP.title)).not.toBeInTheDocument();
});

test("the bar moves on as the next step does, while still inside the fix", async () => {
  const { router } = await renderAppAt("/accounts/9?from=q.own-and-owe");
  await screen.findByText(new RegExp(STALE_STEP.title));
  detail = {
    ...PARTLY,
    next_step: { ...STALE_STEP, title: "Your Position uses a balance for Sample Super from 31 August 2026" },
  };
  await router.navigate({ to: "/accounts/4" });
  expect(await screen.findByText(/Sample Super from 31 August 2026/)).toBeInTheDocument();
});

test("What's due keeps its own bar and wording on the same mechanism", async () => {
  await renderAppAt("/accounts/1?from=due");
  const back = await content().findByRole("link", { name: "Back to What's due" });
  expect(back).toHaveAttribute("href", "/");
  expect(content().queryByTestId("return-next")).not.toBeInTheDocument();
});

test.each([
  ["on the Question page", "/questions/own-and-owe"],
  ["on a Home card", "/"],
])("Ask about this %s asks the fixed prompt at once, with no digits, and says where it was asked", async (_name, path) => {
  await renderAppAt(path);
  fireEvent.click((await screen.findAllByRole("button", { name: "Ask about this" }))[0]);
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0].question).toBe(ASK_PROMPT);
  expect(asked[0].question).not.toMatch(/\d/);
  expect(asked[0].screen).toBe("/questions/own-and-owe");
  // the asked words appear as the owner's turn, in the open panel
  expect(await screen.findByRole("log", { name: "Conversation" })).toHaveTextContent(ASK_PROMPT);
});

test("asking about the same Question twice asks twice", async () => {
  await renderAppAt("/questions/own-and-owe");
  fireEvent.click(await screen.findByRole("button", { name: "Ask about this" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  await screen.findByRole("log", { name: "Conversation" });
  fireEvent.click(screen.getByRole("button", { name: "Ask about this", hidden: true }));
  await waitFor(() => expect(asked).toHaveLength(2));
});

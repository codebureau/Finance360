import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ratingsText } from "@/lib/questions";
import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Rating = {
  answer: string;
  answer_word: string;
  note: string | null;
  readiness_at_rating: string;
  readiness_word: string;
  created_at: string;
};

const QUESTIONS = [
  { key: "own-and-owe", wording: "What do we own and owe?", screen: "/position", area: "Where we stand" },
  { key: "tax-this-year", wording: "How much tax will we pay, or get back?", screen: "/tax", area: "This tax year" },
];

const rating = (over: Partial<Rating> = {}): Rating => ({
  answer: "no",
  answer_word: "No",
  note: null,
  readiness_at_rating: "ready",
  readiness_word: "Ready",
  created_at: "2026-11-27T12:00:00",
  ...over,
});

let history: Record<string, Rating[]>; // newest first
let posted: { key: string; body: { answer: string; note: string | null } }[];
let refuse: string | null;
let snoozed: { title: string; link: string; until_on: string | null }[];
let clipboard: string[];

const current = (key: string) => history[key]?.[0] ?? null;

beforeEach(() => {
  history = {};
  posted = [];
  refuse = null;
  snoozed = [];
  clipboard = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (text: string) => void clipboard.push(text) },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", version: "v1", schema_version: "0004" });
      if (path === "/api/due") return json({ level: "steady", today: "2026-11-28", items: [], snoozed: [], failed_rules: [] });
      if (path === "/api/notices") return json({ items: [] });
      if (path === "/api/ai/status") return json({ enabled: false, reason: "off", resets_on: null, retrieval: "keyword", answering: false });
      const card = (q: (typeof QUESTIONS)[number]) => ({
        ...q,
        ask_prompt: "Ask words",
        state: "ready",
        state_word: "Ready",
        count: 0,
        reason: "Everything this answer needs is current.",
        next_step: null,
        rating: current(q.key),
      });
      if (path === "/api/questions/ratings") {
        return json({ items: QUESTIONS.map((q) => ({ key: q.key, wording: q.wording, rating: current(q.key) })) });
      }
      if (path === "/api/questions") {
        return json({ today: "2026-11-28", level: "steady", headline: "head", questions: QUESTIONS.map(card) });
      }
      const rate = path.match(/^\/api\/questions\/([^/]+)\/ratings$/);
      if (rate && request.method === "POST") {
        const body = (await request.json()) as { answer: string; note: string | null };
        posted.push({ key: rate[1], body });
        if (refuse) return json({ errors: [{ field: "note", message: refuse }] }, 422);
        const word = body.answer[0].toUpperCase() + body.answer.slice(1);
        const made = rating({ answer: body.answer, answer_word: word, note: body.note, created_at: "2026-11-28T01:00:00" });
        history[rate[1]] = [made, ...(history[rate[1]] ?? [])];
        return json(made, 201);
      }
      const one = path.match(/^\/api\/questions\/([^/]+)$/);
      if (one) {
        const q = QUESTIONS.find((x) => x.key === one[1]);
        if (!q) return json({ errors: [{ field: "", message: "There is no question." }] }, 404);
        return json({
          ...card(q),
          earlier_ratings: (history[q.key] ?? []).slice(1),
          inputs: [
            {
              key: "position.accounts",
              name: "Accounts",
              importance: "important",
              state: "ready",
              state_word: "Ready",
              asked: true,
              counted: false,
              reasons: [],
              snoozed,
            },
          ],
        });
      }
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("an unrated Question says Not rated on its page and shows nothing extra on Home", async () => {
  await renderAppAt("/questions/own-and-owe");
  expect(await screen.findByTestId("current-rating")).toHaveTextContent("Not rated");
  expect(screen.queryByTestId("earlier-ratings")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save rating" })).toBeDisabled();

  cleanup();
  await renderAppAt("/");
  await screen.findByRole("region", { name: "What do we own and owe?" });
  expect(screen.queryByTestId("question-rating")).not.toBeInTheDocument();
  expect(screen.queryByText("Not rated")).not.toBeInTheDocument();
});

test("Yes, Partly or No with a note is saved, and the page then shows it beside the unchanged state", async () => {
  await renderAppAt("/questions/own-and-owe");
  const card = within(await screen.findByRole("region", { name: "Did this answer your question?" }));
  expect(card.getByText("Please do not type amounts or account numbers.")).toBeInTheDocument();
  fireEvent.click(card.getByRole("button", { name: "Partly" }));
  expect(card.getByRole("button", { name: "Partly" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.change(card.getByLabelText(/What were you hoping to see/), { target: { value: "Wanted it by owner" } });
  fireEvent.click(card.getByRole("button", { name: "Save rating" }));

  await waitFor(() =>
    expect(card.getByTestId("current-rating")).toHaveTextContent("You said Partly on 28 November 2026, when it was Ready"),
  );
  expect(posted).toEqual([{ key: "own-and-owe", body: { answer: "partly", note: "Wanted it by owner" } }]);
  // the Readiness tile is untouched: the rating is its own line
  const tile = within(screen.getByRole("region", { name: "What do we own and owe?" }));
  expect(tile.getByTestId("stat-severity")).toHaveTextContent("Ready");
  // the form is ready for another rating
  expect(card.getByLabelText(/What were you hoping to see/)).toHaveValue("");
});

test("a Ready Question rated No is shown plainly, with both, and not in an error tone", async () => {
  history = { "own-and-owe": [rating({ answer: "no", answer_word: "No", readiness_at_rating: "ready", readiness_word: "Ready" })] };
  await renderAppAt("/questions/own-and-owe");
  const tile = within(await screen.findByRole("region", { name: "What do we own and owe?" }));
  expect(tile.getByTestId("stat-severity")).toHaveTextContent("Ready");
  const line = await screen.findByTestId("current-rating");
  expect(line).toHaveTextContent("You said No on 27 November 2026, when it was Ready");
  expect(line.className).toContain("text-muted-foreground");
  expect(line.className).not.toContain("destructive");
});

test("earlier ratings sit behind a disclosure, newest current", async () => {
  history = {
    "own-and-owe": [
      rating({ answer: "yes", answer_word: "Yes", created_at: "2026-11-27T12:00:00" }),
      rating({
        answer: "no",
        answer_word: "No",
        note: "No split by owner",
        readiness_at_rating: "not_ready",
        readiness_word: "Not ready",
        created_at: "2026-11-02T12:00:00",
      }),
    ],
  };
  await renderAppAt("/questions/own-and-owe");
  expect(await screen.findByTestId("current-rating")).toHaveTextContent("You said Yes on 27 November 2026");
  const earlier = within(await screen.findByTestId("earlier-ratings"));
  expect(earlier.getByText("Earlier ratings (1)")).toBeInTheDocument();
  expect(earlier.getByText(/You said No on 2 November 2026, when it was Not ready/)).toBeInTheDocument();
  expect(earlier.getByText(/No split by owner/)).toBeInTheDocument();
});

test("a refused note shows the calm message beside the field and keeps what was typed", async () => {
  refuse = "Please leave amounts and account numbers out of the note.";
  await renderAppAt("/questions/own-and-owe");
  const card = within(await screen.findByRole("region", { name: "Did this answer your question?" }));
  fireEvent.click(card.getByRole("button", { name: "No" }));
  fireEvent.change(card.getByLabelText(/What were you hoping to see/), { target: { value: "it said $40" } });
  fireEvent.click(card.getByRole("button", { name: "Save rating" }));
  expect(await card.findByText(refuse)).toBeInTheDocument();
  expect(card.getByLabelText(/What were you hoping to see/)).toHaveValue("it said $40");
  expect(card.getByTestId("current-rating")).toHaveTextContent("Not rated");
});

test("Home shows the latest rating word, with its icon, only for a rated Question", async () => {
  history = {
    "tax-this-year": [
      rating({ answer: "partly", answer_word: "Partly", readiness_at_rating: "partly_ready", readiness_word: "Partly ready" }),
    ],
  };
  await renderAppAt("/");
  const rated = within(await screen.findByRole("region", { name: "How much tax will we pay, or get back?" }));
  expect(rated.getByTestId("question-rating")).toHaveTextContent("Your rating: Partly");
  expect(rated.getByTestId("stat-severity")).toHaveTextContent("Ready"); // the state is its own line
  const unrated = within(screen.getByRole("region", { name: "What do we own and owe?" }));
  expect(unrated.queryByTestId("question-rating")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Your ratings" })).toHaveAttribute("href", "/questions/ratings");
});

test("the ratings screen lists every Question with its rating, note and the state then, and copies it as text", async () => {
  history = {
    "own-and-owe": [rating({ answer: "no", answer_word: "No", note: "Wanted it by owner", readiness_word: "Ready" })],
  };
  await renderAppAt("/questions/ratings");
  expect(await screen.findByRole("heading", { level: 1, name: "Your ratings" })).toBeInTheDocument();
  const rows = await screen.findAllByTestId("rating-row");
  expect(rows.map((r) => r.getAttribute("data-question"))).toEqual(["own-and-owe", "tax-this-year"]);
  expect(rows[0]).toHaveTextContent("What do we own and owe?");
  expect(rows[0]).toHaveTextContent("No on 27 November 2026, when it was Ready");
  expect(rows[0]).toHaveTextContent("Wanted it by owner");
  expect(rows[1]).toHaveTextContent("Not rated");

  fireEvent.click(screen.getByRole("button", { name: "Copy" }));
  await screen.findByRole("status");
  expect(clipboard).toEqual([
    "What do we own and owe?\nYou said No on 27 November 2026, when it was Ready\nNote: Wanted it by owner\n\n" +
      "How much tax will we pay, or get back?\nNot rated",
  ]);
});

test("the ratings text is plain words with no score or percentage", () => {
  const text = ratingsText([{ key: "k", wording: "A question?", rating: rating({ answer: "yes", answer_word: "Yes" }) }] as never);
  expect(text).not.toMatch(/%|score/i);
});

test("the Question page lists Snoozed gaps muted, with the day the Snooze ends", async () => {
  snoozed = [
    { title: "Your Position uses a balance for Sample Cash from 2 January 2020", link: "/accounts/1", until_on: "2026-12-04" },
    { title: "Your Position uses a balance for Sample Loan from 3 March 2020", link: "/accounts/2", until_on: null },
  ];
  await renderAppAt("/questions/own-and-owe");
  const gaps = within(await screen.findByTestId("snoozed-gaps"));
  expect(gaps.getByText("Snoozed (2)")).toBeInTheDocument();
  const items = gaps.getAllByTestId("snoozed-gap");
  expect(items[0]).toHaveTextContent("snoozed until 4 December 2026");
  expect(items[1]).toHaveTextContent("skipped for this period");
});

test("no Snoozed section when nothing is snoozed", async () => {
  await renderAppAt("/questions/own-and-owe");
  await screen.findByTestId("current-rating");
  expect(screen.queryByTestId("snoozed-gaps")).not.toBeInTheDocument();
});

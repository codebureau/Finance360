import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { costText, formatDate } from "@/lib/ask";
import { renderAppAt } from "./test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Status = {
  enabled: boolean;
  reason: string | null;
  resets_on: string | null;
  retrieval: "keyword";
  answering: boolean;
};
type Asked = { question: string; history: { role: string; text: string }[]; screen: string | null; guide_slug: string | null };

/** Nothing set up: the default. Ask searches the Guide and writes nothing. */
const SEARCH_ONLY: Status = { enabled: false, reason: "off", resets_on: null, retrieval: "keyword", answering: false };
/** A model is set up and may be used. */
const WRITING: Status = { enabled: true, reason: null, resets_on: null, retrieval: "keyword", answering: true };

const PASSAGES = {
  found: true,
  message: "",
  start: null,
  passages: [
    {
      page: "accounts-and-imports",
      label: "Accounts and imports",
      heading: "Income streams",
      excerpt: "A salary is an Income stream; add one for each Member who earns.",
      link: "/guide/accounts-and-imports",
    },
    {
      page: "what-to-provide",
      label: "What to provide, when",
      heading: "Once, then when it changes",
      excerpt: "Enter each salary once, then again when it changes.",
      link: "/guide/what-to-provide",
    },
  ],
};

const ANSWER = {
  answered: true,
  answer: "Your salary goes in as an Income stream.",
  model: "claude-haiku-5-5",
  sources: [
    { kind: "guide", ref: "accounts-and-imports", label: "Accounts and Imports", link: "/guide/accounts-and-imports" },
    { kind: "screen", ref: "/income-streams", label: "Income streams", link: "/income-streams" },
  ],
};

const LOG_ENTRY = {
  id: 7,
  occurred_at: "2026-11-05T01:30:00Z",
  task: "ask",
  data_class: "guidance",
  provider: "anthropic",
  model: "claude-haiku-5-5",
  outcome: "ok",
  sent: "Where do I enter [MEMBER_1]'s salary?",
  public_parts: '[{"name":"guide","sha256":"abc"}]',
  received: '{"answered": true}',
  input_tokens: 1200,
  output_tokens: 80,
  cache_read_tokens: 1000,
  cost_usd_micros: 1500,
  cost_estimated: false,
  why: "Ask panel question",
};

let status: Status;
/** Every POST /api/ask (the search) and POST /api/ask/answer (the written answer), in order. */
let searched: Asked[];
let answered: Asked[];
/** What the next search / written answer returns: a status code and a body. */
let search: () => Response | Promise<Response>;
let write: () => Response | Promise<Response>;
let statusCalls: number;

beforeEach(() => {
  status = SEARCH_ONLY;
  searched = [];
  answered = [];
  statusCalls = 0;
  search = () => json(PASSAGES);
  write = () => json(ANSWER);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
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
      if (path === "/api/due") return json({ level: "light", today: "2026-11-03", items: [], snoozed: [], failed_rules: [] });
      if (path === "/api/household") return json({ name: "Sample household", time_zone: "Australia/Melbourne", effort_level: "light", members: [] });
      if (path === "/api/ai/status") {
        statusCalls++;
        return json(status);
      }
      if (path === "/api/ai/log") return json([LOG_ENTRY, { ...LOG_ENTRY, id: 6, outcome: "refused_cap", received: null, cost_usd_micros: 0 }]);
      if (path === "/api/ask") {
        searched.push((await request.json()) as Asked);
        return search();
      }
      if (path === "/api/ask/answer") {
        answered.push((await request.json()) as Asked);
        return write();
      }
      return json({ errors: [{ field: "", message: "Not mocked in this test." }] }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

/** A wide screen docks the panel beside the page; a phone gets a Sheet. Wide by default. */
let docked: boolean;
const useWidth = (wide: boolean) => {
  docked = wide;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
};
beforeEach(() => useWidth(true));

const panelRole = () => (docked ? "complementary" : "dialog");
/** The panel is "Search the Guide" at Search only and "Ask" once a model writes answers. */
const PANEL_NAME = /^(Ask|Search the Guide)$/;
const queryPanel = () => screen.queryByRole(panelRole(), { name: PANEL_NAME });

const openPanel = async () => {
  fireEvent.click(screen.getAllByRole("button", { name: "Ask" })[0]);
  return screen.findByRole(panelRole(), { name: PANEL_NAME });
};

const type = (panel: HTMLElement, text: string) =>
  fireEvent.change(within(panel).getByLabelText(/Your question/), { target: { value: text } });

/** The submit button reads "Search" at Search only and "Ask" once a model writes answers. */
const submit = (panel: HTMLElement) => within(panel).getByRole("button", { name: /^(Ask|Search)$/ });

const send = async (panel: HTMLElement, text: string) => {
  type(panel, text);
  fireEvent.click(submit(panel));
};

// --- Search only: the default, with nothing set up -------------------------------------------

test.each(["/position", "/guide", "/settings", "/accounts"])("the Ask button is on %s and opens a Search the Guide panel", async (path) => {
  await renderAppAt(path);
  expect(screen.getAllByRole("button", { name: "Ask" }).length).toBeGreaterThan(0);
  const panel = await openPanel();
  expect(within(panel).getByRole("heading", { name: "Search the Guide" })).toBeInTheDocument();
  expect(within(panel).getByText("Searches this Guide on your server. Nothing is sent anywhere.")).toBeInTheDocument();
  // Nothing is sent to a model, so no claim that names are removed and no link to the AI log.
  expect(within(panel).queryByText(/names and numbers/)).not.toBeInTheDocument();
  expect(within(panel).queryByRole("link", { name: "See what was sent" })).not.toBeInTheDocument();
  expect(within(panel).queryByText(/isn't switched on/)).not.toBeInTheDocument();
  fireEvent.click(within(panel).getByRole("button", { name: "Close panel" }));
  await waitFor(() => expect(queryPanel()).not.toBeInTheDocument());
});

test("at Search only a question shows the best passages with links, and calls only the search", async () => {
  let release: (r: Response) => void = () => {};
  search = () => new Promise<Response>((resolve) => (release = resolve));
  const { router } = await renderAppAt("/accounts");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);

  await send(panel, "Where do I enter my salary?");
  expect(await within(panel).findByRole("status")).toHaveTextContent("Searching the Guide");
  expect(submit(panel)).toBeDisabled();
  release(json(PASSAGES));

  const list = await within(panel).findByRole("list", { name: "Passages from the Guide" });
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Accounts and imports");
  expect(items[0]).toHaveTextContent("Income streams");
  expect(items[0]).toHaveTextContent("A salary is an Income stream");
  expect(within(items[0]).getByRole("link", { name: "Accounts and imports" })).toHaveAttribute("href", "/guide/accounts-and-imports");
  // The server is told the route pattern, never an id, and no history for a first question.
  expect(searched[0]).toEqual({ question: "Where do I enter my salary?", history: [], screen: "/accounts", guide_slug: null });
  expect(within(panel).getByLabelText(/Your question/)).toHaveValue(""); // the draft cleared
  // No model: no written answer, no "Written by", and the written-answer call is never made.
  expect(within(panel).queryByTestId("written-answer")).not.toBeInTheDocument();
  expect(within(panel).queryByText(/Written by/)).not.toBeInTheDocument();
  expect(answered).toHaveLength(0);

  fireEvent.click(within(items[1]).getByRole("link", { name: "What to provide, when" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/guide/what-to-provide"));
  // Docked, the panel stays open beside the new screen with the passages in it.
  expect(within(queryPanel()!).getByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
});

test("a question with no match shows the fixed text and the Start here link", async () => {
  search = () =>
    json({
      found: false,
      passages: [],
      message: "I couldn't find that in the Guide. Start here may help.",
      start: { kind: "guide", ref: "start-here", label: "Start here", link: "/guide" },
    });
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "What is the weather?");
  expect(await within(panel).findByTestId("nothing-found")).toHaveTextContent("I couldn't find that in the Guide");
  expect(within(panel).getByRole("link", { name: "Start here" })).toHaveAttribute("href", "/guide");
  expect(within(panel).queryByRole("list", { name: "Passages from the Guide" })).not.toBeInTheDocument();
  expect(answered).toHaveLength(0);
});

test("on a phone the panel is a Sheet: a followed passage closes it and the thread survives", async () => {
  useWidth(false);
  const { router } = await renderAppAt("/accounts");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "Where do I enter my salary?");
  fireEvent.click(await within(panel).findByRole("link", { name: "What to provide, when" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/guide/what-to-provide"));
  await waitFor(() => expect(queryPanel()).not.toBeInTheDocument());

  const again = await openPanel();
  expect(within(again).getByText(/A salary is an Income stream/)).toBeInTheDocument();
  expect(within(again).getByText("Where do I enter my salary?")).toBeInTheDocument();
});

test("Enter sends the question; Shift+Enter and Ctrl+Enter start a new line", async () => {
  await renderAppAt("/position");
  const panel = await openPanel();
  const box = await within(panel).findByLabelText(/Your question/);
  type(panel, "Where");

  // Shift+Enter is left to the browser (a new line); Ctrl+Enter inserts one at the cursor.
  expect(fireEvent.keyDown(box, { key: "Enter", shiftKey: true })).toBe(true);
  (box as HTMLTextAreaElement).setSelectionRange(5, 5);
  expect(fireEvent.keyDown(box, { key: "Enter", ctrlKey: true })).toBe(false);
  expect(box).toHaveValue("Where\n");
  expect(searched).toHaveLength(0);

  type(panel, "Where do I enter my salary?");
  expect(fireEvent.keyDown(box, { key: "Enter" })).toBe(false);
  expect(await within(panel).findByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(searched[0].question).toBe("Where do I enter my salary?");
});

test("on a Guide page the slug goes too; New question clears the thread", async () => {
  await renderAppAt("/guide/tax");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "What is a Rule figure?");
  await within(panel).findByRole("list", { name: "Passages from the Guide" });
  expect(searched[0].screen).toBe("/guide/$slug");
  expect(searched[0].guide_slug).toBe("tax");

  fireEvent.click(within(panel).getByRole("button", { name: "New question" }));
  expect(within(panel).queryByRole("list", { name: "Passages from the Guide" })).not.toBeInTheDocument();
  expect(within(panel).queryByText("What is a Rule figure?")).not.toBeInTheDocument();
});

test("only the last three exchanges are kept", async () => {
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  for (const q of ["one", "two", "three", "four"]) {
    await send(panel, q);
    await waitFor(() => expect(within(panel).getByLabelText(/Your question/)).toHaveValue(""));
    await waitFor(() => expect(submit(panel)).toBeDisabled()); // empty draft
  }
  await waitFor(() => expect(searched).toHaveLength(4));
  expect(within(panel).queryByText("one")).not.toBeInTheDocument();
  expect(within(panel).getByText("four")).toBeInTheDocument();
});

test("a failed search shows a calm error, keeps what was typed, and can be sent again", async () => {
  search = () => json({ message: "Something went wrong. Try again in a moment." }, 500);
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "Where do I enter my salary?");
  expect(await within(panel).findByText(/Something went wrong/)).toBeInTheDocument();
  expect(within(panel).getByLabelText(/Your question/)).toHaveValue("Where do I enter my salary?");

  search = () => json(PASSAGES);
  fireEvent.click(submit(panel));
  expect(await within(panel).findByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(within(panel).queryByText(/Something went wrong/)).not.toBeInTheDocument();
});

test("a validation error (422) shows its message", async () => {
  search = () => json({ errors: [{ field: "question", message: "Keep the question to 500 characters." }] }, 422);
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "Hello");
  expect(await within(panel).findByText("Keep the question to 500 characters.")).toBeInTheDocument();
});

// --- Written answers: a model is set up --------------------------------------------------------

test("with a model the passages show first, then the written answer above them with its model", async () => {
  status = WRITING;
  let release: (r: Response) => void = () => {};
  write = () => new Promise<Response>((resolve) => (release = resolve));
  await renderAppAt("/accounts");
  const panel = await openPanel();
  expect(within(panel).getByRole("heading", { name: "Ask" })).toBeInTheDocument();
  await within(panel).findByLabelText(/Your question/);

  await send(panel, "Where do I enter my salary?");
  // The passages are on screen at once; the answer is still being written.
  expect(await within(panel).findByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(await within(panel).findByRole("status")).toHaveTextContent("Writing an answer from the passages below");
  expect(submit(panel)).toBeDisabled(); // one answer at a time
  expect(answered).toHaveLength(1);
  release(json(ANSWER));

  const written = await within(panel).findByTestId("written-answer");
  expect(written).toHaveTextContent(ANSWER.answer);
  expect(written).toHaveTextContent("Written by claude-haiku-5-5 from the passages below.");
  // Above the passages, not below them.
  const list = within(panel).getByRole("list", { name: "Passages from the Guide" });
  expect(written.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const sources = within(within(written).getByRole("list", { name: "Sources" }));
  expect(sources.getByRole("link", { name: "Income streams" })).toHaveAttribute("href", "/income-streams");
  expect(within(panel).queryByRole("status")).not.toBeInTheDocument();
  // The same question, whereabouts and history go to both steps; the client names no Passage.
  expect(answered[0]).toEqual(searched[0]);
  expect(Object.keys(answered[0]).sort()).toEqual(["guide_slug", "history", "question", "screen"]);
  // Footer: what is sent, and where to see it.
  expect(within(panel).getByText(/Writing an answer sends your question and the best passages to an AI service/)).toBeInTheDocument();
  expect(within(panel).getByText(/account names and numbers removed first/)).toBeInTheDocument();
  expect(within(panel).queryByText(/Nothing is sent anywhere/)).not.toBeInTheDocument();
});

test("a follow-up sends the earlier written answer as history", async () => {
  status = WRITING;
  await renderAppAt("/guide/tax");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "What is a Rule figure?");
  await within(panel).findByTestId("written-answer");
  await send(panel, "And where is that?");
  await waitFor(() => expect(answered).toHaveLength(2));
  expect(answered[1].history).toEqual([
    { role: "user", text: "What is a Rule figure?" },
    { role: "assistant", text: ANSWER.answer },
  ]);
  expect(answered[0].guide_slug).toBe("tax");
});

test("a failed written answer leaves the passages with a calm line", async () => {
  status = WRITING;
  write = () => json({ message: "The AI service did not answer. Try again in a moment." }, 502);
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "Where do I enter my salary?");
  const failed = await within(panel).findByTestId("answer-failed");
  expect(failed).toHaveTextContent("A written answer isn't available just now");
  expect(failed).toHaveTextContent("The passages below are the best matches.");
  expect(within(panel).getByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(within(panel).queryByTestId("written-answer")).not.toBeInTheDocument();
  // Search still works for the next question.
  expect(submit(panel)).toBeDisabled(); // empty draft, not busy
  type(panel, "Another?");
  expect(submit(panel)).toBeEnabled();
});

test("a daily limit reached mid-session (409) pauses written answers and says when they return", async () => {
  status = WRITING;
  write = () => {
    status = { enabled: false, reason: "daily_limit", resets_on: "2026-11-07", retrieval: "keyword", answering: true };
    return json({ message: "The daily limit of AI questions has been reached.", reason: "daily_limit", resets_on: "2026-11-07" }, 409);
  };
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  await send(panel, "One more");
  expect(await within(panel).findByTestId("answers-paused")).toHaveTextContent(
    "Written answers have reached today's question limit. They start again on 7 November 2026. Search still works.",
  );
  expect(within(panel).getByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(statusCalls).toBeGreaterThan(1);
  // Search still works while paused.
  search = () => json(PASSAGES);
  await send(panel, "And another");
  await waitFor(() => expect(searched).toHaveLength(2));
});

test("when a limit is reached the panel says when written answers start again but still searches", async () => {
  status = { enabled: false, reason: "monthly_cap", resets_on: "2026-12-01", retrieval: "keyword", answering: true };
  await renderAppAt("/position");
  const panel = await openPanel();
  expect(await within(panel).findByTestId("answers-paused")).toHaveTextContent(
    "Written answers have reached this month's spending limit. They start again on 1 December 2026. Search still works.",
  );
  await send(panel, "Where do I enter my salary?");
  expect(await within(panel).findByRole("list", { name: "Passages from the Guide" })).toBeInTheDocument();
  expect(answered).toHaveLength(0); // no call to a model that is capped
});

test("the footer link opens the AI log when a model is used", async () => {
  status = WRITING;
  const { router } = await renderAppAt("/position");
  const panel = await openPanel();
  fireEvent.click(await within(panel).findByRole("link", { name: "See what was sent" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/settings/ai-log"));
  expect(await screen.findByRole("heading", { level: 1, name: "AI log" })).toBeInTheDocument();
});

test("docked, the panel is not modal: the page stays usable and the Ask button closes it again", async () => {
  const { router } = await renderAppAt("/position");
  const panel = await openPanel();
  // No dialog, so nothing is dimmed or trapped: the sidebar's links still work with the panel open.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("link", { name: "Accounts" })[0]);
  await waitFor(() => expect(router.state.location.pathname).toBe("/accounts"));
  expect(queryPanel()).toBe(panel);

  const button = screen.getAllByRole("button", { name: "Ask" })[0];
  expect(button).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(button);
  await waitFor(() => expect(queryPanel()).not.toBeInTheDocument());
});

test.each([true, false])("closing never loses a typed question, so it never asks (wide: %s)", async (wide) => {
  useWidth(wide);
  await renderAppAt("/position");
  const panel = await openPanel();
  await within(panel).findByLabelText(/Your question/);
  type(panel, "Half a question");

  fireEvent.click(within(panel).getByRole("button", { name: "Close panel" }));
  await waitFor(() => expect(queryPanel()).not.toBeInTheDocument());
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

  const again = await openPanel();
  expect(await within(again).findByLabelText(/Your question/)).toHaveValue("Half a question");
});

test("the AI log lists every call newest first, and an entry shows what was sent exactly", async () => {
  const { router } = await renderAppAt("/settings");
  fireEvent.click(await screen.findByRole("link", { name: "Open the AI log" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/settings/ai-log"));
  expect(await screen.findByRole("heading", { level: 1, name: "AI log" })).toBeInTheDocument();

  const rows = within(await screen.findByRole("table")).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("Completed")).toBeInTheDocument();
  expect(within(rows[0]).getByText("US$0.0015")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Refused: limit")).toBeInTheDocument();

  fireEvent.click(within(rows[0]).getByRole("link"));
  const sheet = await screen.findByRole("dialog");
  expect(router.state.location.search).toEqual(expect.objectContaining({ entry: 7 }));
  // The redacted text, tokens and all.
  expect(within(sheet).getByText("Where do I enter [MEMBER_1]'s salary?")).toBeInTheDocument();
  expect(within(sheet).getByText('{"answered": true}')).toBeInTheDocument();
  expect(within(sheet).getByText(/1200 tokens in, 80 out, 1000 read from cache\. Cost US\$0\.0015\./)).toBeInTheDocument();

  fireEvent.click(within(sheet).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("/settings/ai-log?entry=7 deep-links to the entry", async () => {
  await renderAppAt("/settings/ai-log?entry=7");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Ask panel question")).toBeInTheDocument();
});

test("costs are shown from integer micros with no rounding", () => {
  expect(costText(0)).toBe("US$0.00");
  expect(costText(1500)).toBe("US$0.0015");
  expect(costText(1)).toBe("US$0.000001");
  expect(costText(2_500_000)).toBe("US$2.50");
  expect(costText(5_000_000)).toBe("US$5.00");
  expect(formatDate("2026-12-01")).toBe("1 December 2026");
});

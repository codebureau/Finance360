import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  RuleFigure,
  RuleFigureProposal,
  RuleFigureYear,
} from "@/lib/rule-figures";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// Every figure, page and sentence here is invented.
const CAR_PAGE = "https://example.com/invented-car-page";
const TAX_PAGE = "https://example.com/invented-levy-page";
const PASTED = "The invented car page. The rate is 99 cents. The limit is 5,000 km.";

function figure(
  over: Partial<RuleFigure> & Pick<RuleFigure, "key" | "label">,
): RuleFigure {
  return {
    kind: "money",
    unit: "km",
    where: "Under the rates heading.",
    page_url: CAR_PAGE,
    url_confirmed: true,
    scheduled_change: null,
    plausible_range: "$0.10 to $3.00 per km",
    entry_hint: "dollars per km, e.g. 1234.50",
    financial_year: 2027,
    financial_year_label: "2026-27",
    state: "missing",
    value: null,
    shipped_value: null,
    provisional_note: null,
    previous: {
      financial_year: 2026,
      financial_year_label: "2025-26",
      value: "0.88",
    },
    capture: null,
    ...over,
  };
}

const rate = figure({
  key: "tax.car.rate_per_km",
  label: "Car expenses: cents per kilometre rate",
});
const limit = figure({
  key: "tax.car.max_km",
  label: "Car expenses: kilometre limit per car",
  kind: "int",
  unit: null,
  plausible_range: "1000 to 20000",
  entry_hint: "a whole number",
  previous: { financial_year: 2026, financial_year_label: "2025-26", value: "5000" },
});
const levy = figure({
  key: "tax.medicare.levy_rate",
  label: "Medicare levy rate",
  kind: "rate",
  unit: null,
  page_url: TAX_PAGE,
  plausible_range: "0% to 10%",
  entry_hint: "a percentage, e.g. 16.5",
  previous: { financial_year: 2026, financial_year_label: "2025-26", value: "2%" },
});

function candidate(
  f: RuleFigure,
  value: string,
  over: Partial<RuleFigureProposal> = {},
): RuleFigureProposal {
  const quote = `The invented sentence says ${value}.`;
  return {
    key: f.key,
    label: f.label,
    status: "candidate",
    reason: null,
    value,
    quote,
    provisional_note: null,
    note: "",
    confidence: "high",
    jump: null,
    ai_log_id: 41,
    proposal: {
      key: f.key,
      fy: 2027,
      value,
      quote,
      provisional: false,
      note: "",
      confidence: "high",
    },
    ...over,
  };
}

function notFound(f: RuleFigure, reason: string | null): RuleFigureProposal {
  return {
    key: f.key,
    label: f.label,
    status: "not_found",
    reason,
    value: null,
    quote: null,
    provisional_note: null,
    note: "",
    confidence: "low",
    jump: null,
    ai_log_id: 41,
    proposal: null,
  };
}

const ON = { enabled: true, reason: null, resets_on: null, retrieval: "keyword", answering: true };
const OFF = { enabled: false, reason: "off", resets_on: null, retrieval: "keyword", answering: false };

/** What the matcher's proposal looks like: no confidence, no AI log entry. */
function matched(f: RuleFigure, value: string): RuleFigureProposal {
  const found = candidate(f, value, { confidence: null, ai_log_id: null });
  return { ...found, proposal: found.proposal && { ...found.proposal, confidence: null } };
}

type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

function backend(opts: {
  figures?: RuleFigure[];
  status?: {
    enabled: boolean;
    reason: string | null;
    resets_on: string | null;
    retrieval: string;
    answering: boolean;
  };
  /** The reply to a proposal request. */
  propose?: () => Response;
  /** The reply to a capture request, if it should not succeed. */
  capture?: (body: Record<string, unknown>) => Response | undefined;
}) {
  const calls: Call[] = [];
  let figures = opts.figures ?? [rate, limit, levy];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const body =
        request.method === "POST" ? await request.json() : undefined;
      calls.push({ method: request.method, path: url.pathname, search: url.search, body });
      if (url.pathname === "/api/ai/status")
        return json(opts.status ?? ON);
      if (url.pathname === "/api/rule-figures/proposals")
        return opts.propose?.() ?? json({ financial_year: 2027, method: "ai", fallback: null, proposals: [] });
      if (url.pathname === "/api/rule-figures" && request.method === "GET") {
        const year: RuleFigureYear = {
          jurisdiction: "au",
          financial_year: 2027,
          financial_year_label: "2026-27",
          first_financial_year: 2026,
          last_financial_year: 2028,
          rule_pack_version: "au/2026-27/r6",
          figures,
          carry_offer: null,
          no_longer_fit: [],
        };
        return json(year);
      }
      if (url.pathname === "/api/rule-figures" && request.method === "POST") {
        const refused = opts.capture?.(body ?? {});
        if (refused) return refused;
        figures = figures.map((f) =>
          f.key === body?.key
            ? { ...f, state: "captured" as const, value: String(body?.value) }
            : f,
        );
        return json(figures[0], 201);
      }
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

async function openReader() {
  await renderAppAt("/rule-figures?fy=2027");
  const button = await screen.findByRole("button", { name: "Read the page for me" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return screen.findByRole("dialog");
}

function paste(dialog: HTMLElement, text = PASTED) {
  fireEvent.change(within(dialog).getByLabelText(/Page text/), { target: { value: text } });
}

const writes = (calls: Call[]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/api/rule-figures");

test("reading the page pre-fills each figure with its quote and confidence, and writes nothing", async () => {
  const calls = backend({
    propose: () =>
      json({
        financial_year: 2027,
        method: "ai",
        fallback: null,
        proposals: [
          candidate(rate, "0.99", { note: "From the rates box.", confidence: "medium" }),
          candidate(limit, "5000", { provisional_note: "Announced in the Budget." }),
        ],
      }),
  });
  const dialog = await openReader();
  // Only the car page's figures are asked for: the other missing figure is on another page.
  expect(within(dialog).getByText(/Looking for: .*cents per kilometre rate/)).toBeInTheDocument();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));

  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  expect(row).toHaveTextContent("$0.99 per km");
  expect(row).toHaveTextContent("Medium confidence");
  expect(row).toHaveTextContent("“The invented sentence says 0.99.”");
  expect(row).toHaveTextContent("From the rates box.");
  const second = within(dialog).getByTestId("proposal-tax.car.max_km");
  expect(second).toHaveTextContent("5,000");
  expect(second).toHaveTextContent("Provisional");

  const asked = calls.find((c) => c.path === "/api/rule-figures/proposals");
  expect(asked?.body).toEqual({
    jurisdiction: "au",
    financial_year: 2027,
    keys: ["tax.car.rate_per_km", "tax.car.max_km"],
    page_text: PASTED,
  });
  expect(writes(calls)).toHaveLength(0); // nothing is stored before Confirm
});

test("Confirm stores the figure through the typed write, carrying the log row and the proposal", async () => {
  const calls = backend({
    propose: () =>
      json({ financial_year: 2027, method: "ai", fallback: null, proposals: [candidate(rate, "0.99"), candidate(limit, "5000")] }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  await within(dialog).findByTestId("proposal-tax.car.rate_per_km");

  fireEvent.click(
    within(dialog).getByRole("button", { name: "Confirm Car expenses: cents per kilometre rate" }),
  );
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  const sent = writes(calls)[0].body;
  expect(sent).toMatchObject({
    jurisdiction: "au",
    financial_year: 2027,
    key: "tax.car.rate_per_km",
    value: "0.99",
    quote: "The invented sentence says 0.99.",
    page_url: CAR_PAGE,
    provisional_note: null,
    confirm_jump: false,
    ai_log_id: 41,
    ai_proposal: candidate(rate, "0.99").proposal,
  });
  const row = within(dialog).getByTestId("proposal-tax.car.rate_per_km");
  await waitFor(() => expect(row).toHaveTextContent("Captured"));
  // The other candidate is untouched until it is confirmed too.
  expect(within(dialog).getByRole("button", { name: /Confirm Car expenses: kilometre limit/ })).toBeEnabled();
});

test("Confirm all stores every waiting candidate once", async () => {
  const calls = backend({
    propose: () =>
      json({ financial_year: 2027, method: "ai", fallback: null, proposals: [candidate(rate, "0.99"), candidate(limit, "5000")] }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: "Confirm all (2)" }));
  await waitFor(() => expect(writes(calls)).toHaveLength(2));
  expect(writes(calls).map((c) => c.body?.key)).toEqual([
    "tax.car.rate_per_km",
    "tax.car.max_km",
  ]);
  await waitFor(() =>
    expect(within(dialog).queryByRole("button", { name: /Confirm all/ })).not.toBeInTheDocument(),
  );
});

test("a large change needs the explicit tick before it can be confirmed, singly or all at once", async () => {
  const calls = backend({
    propose: () =>
      json({
        financial_year: 2027,
        method: "ai",
        fallback: null,
        proposals: [
          candidate(rate, "1.50", { jump: "Changed by more than 20% from 0.88 to 1.50." }),
          candidate(limit, "5000"),
        ],
      }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  expect(row).toHaveTextContent("FY 2025-26 holds $0.88 per km; the page says $1.50 per km.");
  const confirm = within(row).getByRole("button", { name: /Confirm/ });
  const all = within(dialog).getByRole("button", { name: "Confirm all (2)" });
  expect(confirm).toBeDisabled();
  expect(all).toBeDisabled();
  fireEvent.click(within(row).getByLabelText("Yes, this is right"));
  expect(confirm).toBeEnabled();
  expect(all).toBeEnabled();
  fireEvent.click(confirm);
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  expect(writes(calls)[0].body?.confirm_jump).toBe(true);
});

test("a failed proposal shows Not found; enter it with the reason and the typed field, and typing captures it", async () => {
  const calls = backend({
    propose: () =>
      json({
        financial_year: 2027,
        method: "ai",
        fallback: null,
        proposals: [
          notFound(rate, "The proposed figure '12.50' is not valid: outside the plausible range."),
          candidate(limit, "5000"),
        ],
      }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  expect(row).toHaveTextContent("Not found; enter it");
  expect(row).toHaveTextContent("outside the plausible range");
  expect(row).not.toHaveTextContent("Medium confidence");
  const input = within(row).getByLabelText("Enter Car expenses: cents per kilometre rate");
  const capture = within(row).getByRole("button", { name: /Capture Car expenses/ });
  expect(capture).toBeDisabled(); // nothing typed yet
  fireEvent.change(input, { target: { value: "0.97" } });
  fireEvent.click(capture);
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  const sent = writes(calls)[0].body ?? {};
  expect(sent).toMatchObject({ key: "tax.car.rate_per_km", value: "0.97", page_url: CAR_PAGE });
  expect("ai_log_id" in sent && sent.ai_log_id != null).toBe(false); // typed, not AI
  await waitFor(() => expect(row).toHaveTextContent("Captured"));
});

test("a typed figure refused by the server shows why beside the field and keeps what was typed", async () => {
  backend({
    propose: () =>
      json({ financial_year: 2027, method: "ai", fallback: null, proposals: [notFound(rate, "The AI gave no sentence."), candidate(limit, "5000")] }),
    capture: () =>
      json({ errors: [{ field: "value", message: "12.50 is outside the plausible range." }] }, 422),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  const input = within(row).getByLabelText("Enter Car expenses: cents per kilometre rate");
  fireEvent.change(input, { target: { value: "12.50" } });
  fireEvent.click(within(row).getByRole("button", { name: /Capture Car expenses/ }));
  expect(await within(row).findByText("12.50 is outside the plausible range.")).toBeInTheDocument();
  expect(input).toHaveValue("12.50");
});

test("a figure the page does not mention is listed, not shown as a row", async () => {
  backend({
    propose: () =>
      json({ financial_year: 2027, method: "ai", fallback: null, proposals: [notFound(rate, null), candidate(limit, "5000")] }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  await within(dialog).findByTestId("proposal-tax.car.max_km");
  expect(within(dialog).queryByTestId("proposal-tax.car.rate_per_km")).not.toBeInTheDocument();
  expect(within(dialog).getByTestId("not-on-page")).toHaveTextContent(
    "Not on the page you pasted: Car expenses: cents per kilometre rate",
  );
  expect(within(dialog).getByRole("status")).toHaveTextContent("Read by AI: found 1 of 2 figures");
});

test("with no AI the button is enabled, the page is read by matching words and the origin says so", async () => {
  const calls = backend({
    status: OFF,
    propose: () =>
      json({
        financial_year: 2027,
        method: "matched",
        fallback: null,
        proposals: [matched(rate, "0.99"), matched(limit, "5000")],
      }),
  });
  await renderAppAt("/rule-figures?fy=2027");
  const button = await screen.findByRole("button", { name: "Read the page for me" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.queryByTestId("reader-paused")).not.toBeInTheDocument();
  // Typed entry on each row is untouched.
  const menu = await screen.findByRole("button", {
    name: "Actions for Car expenses: cents per kilometre rate",
  });
  fireEvent.keyDown(menu, { key: "Enter" });
  expect(await screen.findByRole("menuitem", { name: "Capture figure" })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("menuitem", { name: "Capture figure" }), { key: "Escape" });
  fireEvent.click(button);
  const dialog = await screen.findByRole("dialog");
  // It says plainly what reads the page, and that nothing leaves the server; no AI log is mentioned.
  expect(dialog).toHaveTextContent("Finance360 will find each missing figure by matching words");
  expect(dialog).toHaveTextContent("Nothing is sent anywhere");
  expect(within(dialog).queryByRole("link", { name: "AI log" })).not.toBeInTheDocument();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));

  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  expect(row).toHaveTextContent("Matched by words on the page");
  expect(row).not.toHaveTextContent("confidence");
  expect(row).not.toHaveTextContent("Read by AI");
  expect(within(dialog).getByRole("status")).toHaveTextContent("Matched by words on the page: found 2 of 2");

  fireEvent.click(
    within(dialog).getByRole("button", { name: "Confirm Car expenses: cents per kilometre rate" }),
  );
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  const sent = writes(calls)[0].body ?? {};
  expect(sent).toMatchObject({
    key: "tax.car.rate_per_km",
    value: "0.99",
    matched_proposal: matched(rate, "0.99").proposal,
  });
  // The matcher is not an AI call: nothing about an AI log entry goes with the figure.
  expect("ai_log_id" in sent).toBe(false);
  expect("ai_proposal" in sent).toBe(false);
});

test("when a model is set up the sheet says AI reads the page and shows its confidence", async () => {
  backend({
    propose: () =>
      json({
        financial_year: 2027,
        method: "ai",
        fallback: null,
        proposals: [candidate(rate, "0.99"), candidate(limit, "5000")],
      }),
  });
  const dialog = await openReader();
  expect(dialog).toHaveTextContent("AI will find each missing figure");
  expect(within(dialog).getByRole("link", { name: "AI log" })).toHaveAttribute("href", "/settings/ai-log");
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const row = await within(dialog).findByTestId("proposal-tax.car.rate_per_km");
  expect(row).toHaveTextContent("Read by AI");
  expect(row).toHaveTextContent("High confidence");
});

test("a capped model is said under the button and the page is still read, with the reason", async () => {
  backend({
    status: { enabled: false, reason: "monthly_cap", resets_on: "2026-12-01", retrieval: "keyword", answering: true },
    propose: () =>
      json({
        financial_year: 2027,
        method: "matched",
        fallback: "The monthly AI limit has been reached. The page was read by matching words instead.",
        proposals: [matched(rate, "0.99"), matched(limit, "5000")],
      }),
  });
  await renderAppAt("/rule-figures?fy=2027");
  expect(await screen.findByTestId("reader-paused")).toHaveTextContent(
    "AI has reached this month's spending limit, so the page is read by matching words until 1 December 2026.",
  );
  const button = screen.getByRole("button", { name: "Read the page for me" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  const dialog = await screen.findByRole("dialog");
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  expect(await within(dialog).findByTestId("reader-fallback")).toHaveTextContent(
    "The monthly AI limit has been reached. The page was read by matching words instead.",
  );
});

test("the status asked about is the rule capture task, not Ask", async () => {
  const calls = backend({});
  await renderAppAt("/rule-figures?fy=2027");
  await screen.findByRole("button", { name: "Read the page for me" });
  await waitFor(() =>
    expect(calls.some((c) => c.path === "/api/ai/status" && c.search === "?task=rule_capture")).toBe(true),
  );
});

test("a failed read shows its message and keeps the pasted text", async () => {
  backend({
    propose: () => json({ message: "Something went wrong. Try again in a moment." }, 500),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const error = await within(dialog).findByTestId("reader-error");
  expect(error).toHaveTextContent("Something went wrong. Try again in a moment.");
  expect(error).toHaveTextContent("Your pasted text is still here");
  expect(within(dialog).getByLabelText(/Page text/)).toHaveValue(PASTED);
  expect(within(dialog).getByRole("button", { name: "Read the page" })).toBeEnabled(); // retry
});

test("the pasted text and the results survive closing and reopening the sheet", async () => {
  backend({
    propose: () => json({ financial_year: 2027, method: "ai", fallback: null, proposals: [candidate(limit, "5000"), notFound(rate, null)] }),
  });
  const dialog = await openReader();
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  await within(dialog).findByTestId("proposal-tax.car.max_km");
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Read the page for me" }));
  const again = await screen.findByRole("dialog");
  expect(within(again).getByLabelText(/Page text/)).toHaveValue(PASTED);
  expect(within(again).getByTestId("proposal-tax.car.max_km")).toBeInTheDocument();
});

test("text over the limit is not sent and says what to do", async () => {
  const calls = backend({});
  const dialog = await openReader();
  paste(dialog, "x".repeat(40_001));
  expect(within(dialog).getByText(/40,001 of 40,000 characters/)).toHaveTextContent(
    "Paste only the part of the page with the figures.",
  );
  expect(within(dialog).getByRole("button", { name: "Read the page" })).toBeDisabled();
  expect(calls.some((c) => c.path === "/api/rule-figures/proposals")).toBe(false);
});

test("with several pages missing figures, the page chosen decides which figures are asked for", async () => {
  const calls = backend({
    propose: () => json({ financial_year: 2027, method: "ai", fallback: null, proposals: [candidate(levy, "2%")] }),
  });
  const dialog = await openReader();
  const picker = within(dialog).getByLabelText("Which page did you copy?") as HTMLSelectElement;
  expect([...picker.options].map((o) => o.textContent)).toEqual([
    "Car: 2 missing figures",
    "Medicare: 1 missing figure",
  ]);
  fireEvent.change(picker, { target: { value: TAX_PAGE } });
  paste(dialog);
  fireEvent.click(within(dialog).getByRole("button", { name: "Read the page" }));
  const row = await within(dialog).findByTestId("proposal-tax.medicare.levy_rate");
  expect(row).toHaveTextContent("2%");
  expect(calls.find((c) => c.path === "/api/rule-figures/proposals")?.body?.keys).toEqual([
    "tax.medicare.levy_rate",
  ]);
});

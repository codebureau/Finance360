import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  CarryPreview,
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
const PAGE_A = "https://example.com/invented-offset-page";
const PAGE_B = "https://example.com/invented-levy-page";
const NOTE = "Same as 2025-26, not checked against the page";

function figure(
  over: Partial<RuleFigure> & Pick<RuleFigure, "key" | "label">,
): RuleFigure {
  return {
    kind: "money",
    unit: "year",
    where: "Under the heading.",
    page_url: PAGE_A,
    url_confirmed: true,
    scheduled_change: null,
    plausible_range: "$0 to $5,000 per year",
    entry_hint: "dollars, e.g. 1234.50",
    financial_year: 2027,
    financial_year_label: "2026-27",
    state: "missing",
    value: null,
    shipped_value: null,
    provisional_note: null,
    previous: null,
    capture: null,
    ...over,
  };
}

function carried(
  id: number,
  over: Partial<RuleFigure> & Pick<RuleFigure, "key" | "label" | "value">,
): RuleFigure {
  return figure({
    state: "captured",
    provisional_note: NOTE,
    capture: {
      id,
      quote: null,
      page_url: over.page_url ?? PAGE_A,
      retrieved_on: "2026-10-27",
      captured_at: "2026-11-14T01:02:03",
      captured_by: "owner",
      provisional_note: NOTE,
      method: "carried",
    },
    previous: {
      financial_year: 2026,
      financial_year_label: "2025-26",
      value: over.value ?? "",
    },
    ...over,
  });
}

const offsetMax = carried(11, {
  key: "tax.lito.max",
  label: "Offset: maximum",
  value: "700.00",
});
const offsetFrom = carried(12, {
  key: "tax.lito.taper1_from",
  label: "Offset: reduction starts at income",
  value: "37500.00",
});
const levyRate = carried(13, {
  key: "tax.medicare.levy_rate",
  label: "Levy rate",
  kind: "rate",
  unit: null,
  value: "2%",
  page_url: PAGE_B,
  scheduled_change: "Invented: the rate is legislated to change on 1 July 2028.",
});
const confirmedOffset = figure({
  key: "tax.lito.taper1_rate",
  label: "Offset: first reduction rate",
  kind: "rate",
  unit: null,
  state: "captured",
  value: "5%",
});

const PREVIEW: CarryPreview = {
  financial_year: 2027,
  financial_year_label: "2026-27",
  source_financial_year: 2026,
  source_financial_year_label: "2025-26",
  proposals: [
    {
      key: "tax.lito.max",
      label: "Offset: maximum",
      kind: "money",
      unit: "year",
      value: "700.00",
      source_financial_year: 2026,
      source_financial_year_label: "2025-26",
      note: NOTE,
      page_url: PAGE_A,
      scheduled_change: null,
    },
    {
      key: "tax.medicare.levy_rate",
      label: "Levy rate",
      kind: "rate",
      unit: null,
      value: "2%",
      source_financial_year: 2026,
      source_financial_year_label: "2025-26",
      note: NOTE,
      page_url: PAGE_B,
      scheduled_change: "Invented: the rate is legislated to change on 1 July 2028.",
    },
  ],
  no_earlier: [{ key: "tax.standard_work_deduction", label: "Instant deduction" }],
};

type Call = {
  method: string;
  path: string;
  search: string;
  body?: Record<string, unknown>;
};

function backend(opts: {
  figures: RuleFigure[];
  offer?: RuleFigureYear["carry_offer"];
  propose?: RuleFigureProposal[];
}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const body = ["POST", "PUT"].includes(request.method)
        ? await request.json().catch(() => undefined)
        : undefined;
      calls.push({
        method: request.method,
        path: url.pathname,
        search: url.search,
        body,
      });
      if (url.pathname === "/api/rule-figures" && request.method === "GET")
        return json({
          jurisdiction: "au",
          financial_year: 2027,
          financial_year_label: "2026-27",
          first_financial_year: 2026,
          last_financial_year: 2028,
          rule_pack_version: "au/2026-27/r7+3:abc123",
          carry_offer: opts.offer ?? null,
          figures: opts.figures,
          no_longer_fit: [],
        });
      if (url.pathname === "/api/rule-figures/carry" && request.method === "GET")
        return json(PREVIEW);
      if (url.pathname === "/api/rule-figures/carry")
        return json(PREVIEW);
      if (url.pathname === "/api/rule-figures/confirm-page")
        return json({
          financial_year: 2027,
          confirmed: ["tax.lito.max", "tax.lito.taper1_from"],
          needs_own_confirm: [],
        });
      if (url.pathname === "/api/rule-figures/proposals")
        return json({
          financial_year: 2027,
          method: "matched",
          fallback: null,
          proposals: opts.propose ?? [],
        });
      if (url.pathname.startsWith("/api/rule-figures/"))
        return json(opts.figures[0]);
      if (url.pathname === "/api/ai/status")
        return json({
          enabled: false,
          reason: "off",
          resets_on: null,
          retrieval: "keyword",
          answering: false,
        });
      if (url.pathname === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: [] });
      if (url.pathname === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

test("Start from last year previews every proposal, names figures with no earlier year and writes only when accepted", async () => {
  const calls = backend({
    figures: [figure({ key: "tax.lito.max", label: "Offset: maximum" })],
    offer: {
      source_financial_year: 2026,
      source_financial_year_label: "2025-26",
      count: 2,
    },
  });
  await renderAppAt("/rule-figures?fy=2027");
  fireEvent.click(
    await screen.findByRole("button", { name: "Start from 2025-26" }),
  );

  const list = await screen.findByRole("list", { name: "Proposed figures" });
  expect(within(list).getByTestId("carry-tax.lito.max")).toHaveTextContent(
    "$700.00 per year",
  );
  expect(within(list).getByTestId("carry-tax.lito.max")).toHaveTextContent(
    "from FY 2025-26",
  );
  // a known change is on the candidate, so it is not waved through
  expect(
    within(list).getByTestId("carry-tax.medicare.levy_rate"),
  ).toHaveTextContent("legislated to change on 1 July 2028");
  expect(screen.getByTestId("no-earlier")).toHaveTextContent(
    "No earlier figure; capture it",
  );
  expect(screen.getByTestId("no-earlier")).toHaveTextContent("Instant deduction");
  // previewing wrote nothing
  expect(calls.filter((c) => c.method === "POST")).toEqual([]);

  fireEvent.click(
    screen.getByRole("button", { name: "Carry 2 figures forward" }),
  );
  await waitFor(() =>
    expect(
      calls.filter((c) => c.method === "POST" && c.path.endsWith("/carry")),
    ).toHaveLength(1),
  );
  expect(
    calls.find((c) => c.method === "POST" && c.path.endsWith("/carry"))?.body,
  ).toEqual({ jurisdiction: "au", financial_year: 2027 });
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Started FY 2026-27 from earlier years: 2 figures carried, each provisional",
  );
});

test("a carried year shows what is provisional, and a page confirm leaves a figure with a known change for its own row", async () => {
  const calls = backend({
    figures: [offsetMax, offsetFrom, confirmedOffset, levyRate],
  });
  await renderAppAt("/rule-figures?fy=2027");
  const banner = await screen.findByRole("region", {
    name: "Carried figures to check",
  });
  expect(banner).toHaveTextContent("3 carried figures not yet checked");

  const offsets = screen.getAllByTestId("carried-page")[0];
  expect(offsets).toHaveTextContent("2 carried");
  expect(
    within(offsets).getByRole("link", { name: "Open the official page" }),
  ).toHaveAttribute("href", PAGE_A);
  const levyPage = screen.getAllByTestId("carried-page")[1];
  // the only figure on that page has a known change: no bulk button, and it says why
  expect(
    within(levyPage).queryByRole("button", { name: /unchanged from/ }),
  ).toBeNull();
  expect(levyPage).toHaveTextContent("1 with a known change (Levy rate)");

  // the row carries the badge, the note and its known change beside its own Confirm
  const row = screen.getByText("Levy rate", { selector: "p" }).closest("tr") as HTMLElement;
  expect(row).toHaveTextContent("Carried from an earlier year, not checked");
  expect(row).toHaveTextContent(NOTE);
  expect(within(row).getByTestId("scheduled-change")).toHaveTextContent(
    "legislated to change on 1 July 2028",
  );
  // a confirmed figure shows no carried badge
  const done = screen.getByText("Offset: first reduction rate").closest("tr") as HTMLElement;
  expect(done).not.toHaveTextContent("Carried from an earlier year");

  fireEvent.click(
    within(offsets).getByRole("button", { name: /Confirm 2 unchanged/ }),
  );
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Offset: maximum");
  expect(dialog).toHaveTextContent("$700.00 per year");
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Confirm unchanged" }),
  );
  await waitFor(() =>
    expect(calls.some((c) => c.path === "/api/rule-figures/confirm-page")).toBe(
      true,
    ),
  );
  expect(
    calls.find((c) => c.path === "/api/rule-figures/confirm-page")?.body,
  ).toEqual({ jurisdiction: "au", financial_year: 2027, page_url: PAGE_A });
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Confirmed 2 figures as unchanged.",
  );
});

test("one carried figure is confirmed unchanged from its own row", async () => {
  const calls = backend({ figures: [levyRate] });
  await renderAppAt("/rule-figures?fy=2027");
  fireEvent.click(
    await screen.findByRole("button", { name: "Confirm Levy rate unchanged" }),
  );
  await waitFor(() =>
    expect(
      calls.find((c) => c.path === "/api/rule-figures/13/confirm")?.method,
    ).toBe("POST"),
  );
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Confirmed Levy rate for FY 2026-27 as unchanged.",
  );
});

function reading(f: RuleFigure, value: string): RuleFigureProposal {
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
    confidence: null,
    jump: null,
    ai_log_id: null,
    proposal: {
      key: f.key,
      fy: 2027,
      value,
      quote,
      provisional: false,
      note: "",
      confidence: null,
    },
  };
}

test("reading the page checks carried figures: an agreeing one confirms, a differing one needs the explicit tick, both supersede through the correction", async () => {
  const calls = backend({
    figures: [offsetMax, offsetFrom],
    propose: [
      reading(offsetMax, "700.00"),
      reading(offsetFrom, "38000.00"),
    ],
  });
  await renderAppAt("/rule-figures?fy=2027");
  const open = await screen.findByRole("button", {
    name: "Read the page for me",
  });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  const sheet = await screen.findByRole("dialog");
  fireEvent.change(within(sheet).getByLabelText(/Page text/), {
    target: { value: "The invented page." },
  });
  fireEvent.click(within(sheet).getByRole("button", { name: "Read the page" }));

  const same = await screen.findByTestId("proposal-tax.lito.max");
  expect(same).toHaveTextContent("The page agrees with the carried figure.");
  const differs = screen.getByTestId("proposal-tax.lito.taper1_from");
  expect(differs).toHaveTextContent("This differs from the carried figure");
  expect(differs).toHaveTextContent("Carried: $37,500.00 per year");
  const confirmDiffers = within(differs).getByRole("button", { name: /Confirm/ });
  expect(confirmDiffers).toBeDisabled(); // not without the tick
  expect(screen.getByRole("button", { name: /Confirm all/ })).toBeDisabled();

  fireEvent.click(within(same).getByRole("button", { name: /Confirm/ }));
  await waitFor(() =>
    expect(calls.find((c) => c.path === "/api/rule-figures/11")?.method).toBe(
      "PUT", // the carried row is superseded, not captured a second time
    ),
  );
  expect(calls.some((c) => c.method === "POST" && c.path === "/api/rule-figures")).toBe(
    false,
  );
  fireEvent.click(within(differs).getByRole("checkbox"));
  expect(confirmDiffers).toBeEnabled();
  fireEvent.click(confirmDiffers);
  await waitFor(() =>
    expect(
      calls.find((c) => c.path === "/api/rule-figures/12")?.method,
    ).toBe("PUT"),
  );
});

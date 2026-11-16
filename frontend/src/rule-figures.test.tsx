import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { RuleFigure, RuleFigureYear } from "@/lib/rule-figures";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// Every figure and quote here is invented.
const PAGE = "https://example.com/invented-page";

function figure(
  over: Partial<RuleFigure> & Pick<RuleFigure, "key" | "label">,
): RuleFigure {
  return {
    kind: "money",
    unit: "km",
    where: "Under the rates heading.",
    page_url: PAGE,
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

const CAPTURE = {
  id: 5,
  quote: "The invented rate is 95 cents.",
  page_url: PAGE,
  retrieved_on: "2026-10-01",
  captured_at: "2026-10-02T03:04:05",
  captured_by: "owner",
  provisional_note: null,
  method: "typed" as const,
};

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
  state: "shipped",
  value: "5000",
  previous: null,
});
const levy = figure({
  key: "tax.medicare.levy_rate",
  label: "Medicare levy rate",
  kind: "rate",
  unit: null,
  state: "shipped",
  value: "2%",
  provisional_note: "Announced in an invented Budget, not yet law.",
  url_confirmed: false,
  previous: {
    financial_year: 2026,
    financial_year_label: "2025-26",
    value: "2%",
  },
});
const home = figure({
  key: "tax.home_office.rate_per_hour",
  label: "Working from home: fixed rate per hour",
  state: "captured",
  value: "0.75",
  shipped_value: "0.70",
  unit: "hour",
  previous: {
    financial_year: 2026,
    financial_year_label: "2025-26",
    value: "0.70",
  },
  capture: CAPTURE,
});

type Call = {
  method: string;
  path: string;
  search: string;
  body?: Record<string, unknown>;
};

function year(
  figures: RuleFigure[],
  over: Partial<RuleFigureYear> = {},
): RuleFigureYear {
  return {
    jurisdiction: "au",
    financial_year: 2027,
    financial_year_label: "2026-27",
    first_financial_year: 2026,
    last_financial_year: 2028,
    rule_pack_version: "au/2026-27/r6+1:a3f9c1",
    figures,
    carry_offer: null,
    no_longer_fit: [],
    ...over,
  };
}

function fakeBackend(
  opts: {
    figures?: RuleFigure[];
    noLongerFit?: RuleFigureYear["no_longer_fit"];
    write?: (
      method: string,
      body: Record<string, unknown> | undefined,
    ) => Response | undefined;
  } = {},
) {
  const calls: Call[] = [];
  let figures = opts.figures ?? [rate, limit, levy, home];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const body = ["POST", "PUT"].includes(request.method)
        ? await request.json()
        : undefined;
      calls.push({
        method: request.method,
        path: url.pathname,
        search: url.search,
        body,
      });
      if (url.pathname === "/api/rule-figures" && request.method === "GET") {
        const fy = Number(url.searchParams.get("financial_year"));
        return json(
          year(
            figures.map((f) => ({
              ...f,
              financial_year: fy,
              financial_year_label: financialYearLabel(fy),
            })),
            {
              financial_year: fy,
              financial_year_label: financialYearLabel(fy),
              no_longer_fit: opts.noLongerFit ?? [],
            },
          ),
        );
      }
      if (url.pathname.startsWith("/api/rule-figures")) {
        const refused = opts.write?.(request.method, body);
        if (refused) return refused;
        // A successful write: the figure becomes captured, or (withdraw) falls back.
        if (request.method === "DELETE") {
          figures = figures.map((f) =>
            f.capture?.id === Number(url.pathname.split("/").pop())
              ? {
                  ...f,
                  capture: null,
                  state: f.shipped_value
                    ? ("shipped" as const)
                    : ("missing" as const),
                  value: f.shipped_value,
                }
              : f,
          );
          return json(figures[0]);
        }
        figures = figures.map((f) =>
          (
            request.method === "POST"
              ? f.key === body?.key
              : f.capture?.id === Number(url.pathname.split("/").pop())
          )
            ? {
                ...f,
                state: "captured" as const,
                value: String(body?.value),
                capture: CAPTURE,
              }
            : f,
        );
        return json(figures[0], request.method === "POST" ? 201 : 200);
      }
      if (url.pathname === "/api/household")
        return json({
          name: "The Sample Household",
          time_zone: "Australia/Melbourne",
          members: [],
        });
      if (url.pathname === "/health") return json({ status: "ok" });
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

async function openRowMenu(name: string) {
  const trigger = await screen.findByRole("button", {
    name: `Actions for ${name}`,
  });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

test("lists every figure grouped, each with its value, state, provisional note, last year and official page", async () => {
  fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await screen.findByRole("heading", { name: "Car" });
  expect(screen.getByRole("heading", { name: "Medicare" })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Home office" }),
  ).toBeInTheDocument();

  const car = screen.getByRole("table", { name: "Car figures" });
  const missing = within(car)
    .getByText("Car expenses: cents per kilometre rate")
    .closest("tr") as HTMLElement;
  expect(missing).toHaveTextContent("Missing");
  expect(missing).toHaveTextContent("$0.88 per km"); // last year's value
  expect(missing).toHaveTextContent("FY 2025-26");

  const shipped = within(car)
    .getByText("Car expenses: kilometre limit per car")
    .closest("tr") as HTMLElement;
  expect(shipped).toHaveTextContent("Shipped (r6)");
  expect(shipped).toHaveTextContent("5,000");

  const medicare = screen.getByRole("table", { name: "Medicare figures" });
  expect(medicare).toHaveTextContent("Provisional");
  expect(medicare).toHaveTextContent(
    "Announced in an invented Budget, not yet law.",
  );
  expect(medicare).toHaveTextContent("(address not confirmed)");

  const captured = within(
    screen.getByRole("table", { name: "Home office figures" }),
  ).getAllByRole("row")[1];
  expect(captured).toHaveTextContent("Captured 2 Oct 2026 by the owner");
  expect(captured).toHaveTextContent("$0.75 per hour");
  expect(captured).toHaveTextContent("The release holds $0.70 per hour.");
  expect(captured).toHaveTextContent("The invented rate is 95 cents.");

  const links = within(car).getAllByRole("link", { name: "Official page" });
  for (const link of links) {
    expect(link).toHaveAttribute("href", PAGE);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  }
  expect(
    screen.getByText("Rule pack au/2026-27/r6+1:a3f9c1"),
  ).toBeInTheDocument();
});

test("the year picker defaults to the current year, offers the pack's first year to next year, and changes the list", async () => {
  const calls = fakeBackend();
  const { router } = await renderAppAt("/rule-figures");
  const current = currentFinancialYear();
  await waitFor(() =>
    expect(calls.some((c) => c.search === `?financial_year=${current}`)).toBe(
      true,
    ),
  );
  const picker = (await screen.findByLabelText(
    "Financial year",
  )) as HTMLSelectElement;
  await waitFor(() => expect(picker.options.length).toBe(3));
  expect([...picker.options].map((o) => o.textContent)).toEqual([
    "FY 2027-28",
    "FY 2026-27",
    "FY 2025-26",
  ]);
  expect(picker.value).toBe(String(current));

  fireEvent.change(picker, { target: { value: "2026" } });
  await waitFor(() =>
    expect(calls.some((c) => c.search === "?financial_year=2026")).toBe(true),
  );
  expect(router.state.location.search).toMatchObject({ fy: 2026 });
});

test("Missing only keeps just the figures nobody has supplied", async () => {
  fakeBackend();
  const { router } = await renderAppAt("/rule-figures?fy=2027");
  await screen.findByRole("heading", { name: "Car" });
  fireEvent.click(screen.getByLabelText("Missing only"));
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Medicare" })).toBeNull(),
  );
  expect(
    screen.queryByText("Car expenses: kilometre limit per car"),
  ).toBeNull();
  expect(
    screen.getByText("Car expenses: cents per kilometre rate"),
  ).toBeInTheDocument();
  expect(router.state.location.search).toMatchObject({
    fy: 2027,
    missing: true,
  });
});

test("Capture sends the typed figure, closes, says Captured and shows the new state", async () => {
  const calls = fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );

  // the entry hint, the plausible range and the spec's page address are all there
  expect(form.getByText(/dollars per km, e.g. 1234.50/)).toBeInTheDocument();
  expect(form.getByText(/\$0.10 to \$3.00 per km/)).toBeInTheDocument();
  expect(form.getByLabelText(/^Page address/)).toHaveValue(PAGE);
  expect(
    form.getByText("The sentence on the page that states it."),
  ).toBeInTheDocument();

  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "0.95" },
  });
  fireEvent.change(form.getByLabelText(/^Quote/), {
    target: { value: "An invented sentence." },
  });
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));

  await waitFor(() =>
    expect(screen.queryByRole("form", { name: /^Capture / })).toBeNull(),
  );
  const post = calls.find((c) => c.method === "POST");
  expect(post?.body).toMatchObject({
    financial_year: 2027,
    key: "tax.car.rate_per_km",
    value: "0.95",
    quote: "An invented sentence.",
    page_url: PAGE,
    provisional_note: null,
    confirm_jump: false,
  });
  expect(post?.body?.retrieved_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Captured Car expenses: cents per kilometre rate for FY 2026-27.",
  );
  // the list was fetched again and the row is now captured
  const row = (
    await screen.findByText("Car expenses: cents per kilometre rate")
  ).closest("tr") as HTMLElement;
  await waitFor(() =>
    expect(row).toHaveTextContent("Captured 2 Oct 2026 by the owner"),
  );
});

test("a refusal is shown beside its field and the sheet stays open", async () => {
  fakeBackend({
    write: () =>
      json(
        {
          errors: [
            {
              field: "value",
              message:
                "That is outside the plausible range of $0.10 to $3.00 per km.",
            },
          ],
        },
        422,
      ),
  });
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );
  fireEvent.change(form.getByLabelText(/^Value/), { target: { value: "88" } });
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));
  expect(
    await form.findByText(/outside the plausible range/),
  ).toBeInTheDocument();
  expect(screen.getByRole("form", { name: /^Capture / })).toBeInTheDocument();
  expect(screen.queryByTestId("rule-figures-done")).toBeNull();
});

test("a large change shows last year's figure and needs Yes, this is right before it resubmits with confirm_jump", async () => {
  let refuse = true;
  const calls = fakeBackend({
    write: (_method, body) => {
      if (refuse && !body?.confirm_jump) {
        return json(
          {
            errors: [
              {
                field: "confirm_jump",
                message: "That is a 62% change from last year's figure.",
              },
            ],
          },
          422,
        );
      }
      return undefined;
    },
  });
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );
  expect(form.queryByTestId("jump-check")).toBeNull();

  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "1.43" },
  });
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));

  const check = await form.findByTestId("jump-check");
  expect(check).toHaveTextContent(
    "FY 2025-26 holds $0.88 per km; you entered 1.43.",
  );
  expect(check).toHaveTextContent("62% change");
  expect(form.getByRole("button", { name: "Capture figure" })).toBeDisabled();

  fireEvent.click(form.getByLabelText("Yes, this is right"));
  expect(form.getByRole("button", { name: "Capture figure" })).toBeEnabled();
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));

  await waitFor(() =>
    expect(screen.queryByRole("form", { name: /^Capture / })).toBeNull(),
  );
  const posts = calls.filter((c) => c.method === "POST");
  expect(posts.map((p) => p.body?.confirm_jump)).toEqual([false, true]);
  expect(posts[1].body?.value).toBe("1.43");
  refuse = false;
});

test("changing the value after confirming a jump takes the confirmation back", async () => {
  fakeBackend({
    write: (_m, body) =>
      body?.confirm_jump
        ? undefined
        : json(
            { errors: [{ field: "confirm_jump", message: "A large change." }] },
            422,
          ),
  });
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );
  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "1.43" },
  });
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));
  fireEvent.click(await form.findByLabelText("Yes, this is right"));
  expect(form.getByLabelText("Yes, this is right")).toBeChecked();
  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "1.50" },
  });
  expect(form.getByLabelText("Yes, this is right")).not.toBeChecked();
  expect(form.getByRole("button", { name: "Capture figure" })).toBeDisabled();
});

test("the provisional note appears only behind the announced-not-yet-law tick, and is sent only then", async () => {
  const calls = fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );
  expect(form.queryByLabelText("Provisional note")).toBeNull();
  fireEvent.click(form.getByLabelText("This is announced but not yet law"));
  fireEvent.change(form.getByLabelText("Provisional note"), {
    target: { value: "Announced, not law." },
  });
  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "0.95" },
  });
  fireEvent.click(form.getByRole("button", { name: "Capture figure" }));
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST")).toBe(true),
  );
  expect(calls.find((c) => c.method === "POST")?.body?.provisional_note).toBe(
    "Announced, not law.",
  );
});

test("Correct on a captured figure starts from its value and quote and sends a PUT to its capture", async () => {
  const calls = fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Working from home: fixed rate per hour");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Correct figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Correct Working from home: fixed rate per hour",
    }),
  );
  expect(form.getByLabelText(/^Value/)).toHaveValue("0.75");
  expect(form.getByLabelText(/^Quote/)).toHaveValue(
    "The invented rate is 95 cents.",
  );
  fireEvent.change(form.getByLabelText(/^Value/), {
    target: { value: "0.80" },
  });
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT");
  expect(put?.path).toBe("/api/rule-figures/5");
  expect(put?.body).toMatchObject({
    value: "0.80",
    quote: "The invented rate is 95 cents.",
  });
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Corrected Working from home",
  );
});

test("the sheet asks before discarding typed input, and a click outside does not close it", async () => {
  const calls = fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  );
  const form = within(
    await screen.findByRole("form", {
      name: "Capture Car expenses: cents per kilometre rate",
    }),
  );
  fireEvent.change(form.getByLabelText(/^Value/), { target: { value: "0.9" } });

  // outside click (the overlay) leaves it open
  const overlay = document.querySelector(".fixed.inset-0") as HTMLElement;
  fireEvent.pointerDown(overlay);
  fireEvent.click(overlay);
  expect(screen.getByRole("form", { name: /^Capture / })).toBeInTheDocument();

  fireEvent.click(form.getByRole("button", { name: "Cancel" }));
  const ask = await screen.findByRole("alertdialog");
  expect(ask).toHaveTextContent("Discard your changes?");
  fireEvent.click(within(ask).getByRole("button", { name: "Keep editing" }));
  expect(screen.getByRole("form", { name: /^Capture / })).toBeInTheDocument();
  fireEvent.click(form.getByRole("button", { name: "Cancel" }));
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Discard",
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("form", { name: /^Capture / })).toBeNull(),
  );
  expect(calls.some((c) => c.method === "POST")).toBe(false);
});

test("Withdraw confirms, names the figure the release falls back to, then withdraws it", async () => {
  const calls = fakeBackend({
    figures: [
      {
        ...home,
        kind: "money",
        unit: "km",
        shipped_value: "0.88",
        value: "0.95",
        label: "Car rate",
      },
    ],
  });
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Withdraw figure" }),
  );
  const ask = await screen.findByRole("alertdialog");
  expect(ask).toHaveTextContent("Withdraw the captured car rate?");
  expect(ask).toHaveTextContent("Use the release's $0.88 per km again?");
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);

  fireEvent.click(within(ask).getByRole("button", { name: "Withdraw figure" }));
  await waitFor(() =>
    expect(
      calls.some(
        (c) => c.method === "DELETE" && c.path === "/api/rule-figures/5",
      ),
    ).toBe(true),
  );
  expect(await screen.findByTestId("rule-figures-done")).toHaveTextContent(
    "Withdrawn Car rate for FY 2026-27.",
  );
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  const row = screen.getByText("Car rate").closest("tr") as HTMLElement;
  await waitFor(() => expect(row).toHaveTextContent("Shipped (r6)"));
});

test("a captured figure with no shipped value says it will be missing, and keeping it cancels", async () => {
  const calls = fakeBackend({
    figures: [{ ...rate, state: "captured", value: "0.95", capture: CAPTURE }],
  });
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Withdraw figure" }),
  );
  const ask = await screen.findByRole("alertdialog");
  expect(ask).toHaveTextContent(
    "has no car expenses: cents per kilometre rate until you capture one",
  );
  fireEvent.click(
    within(ask).getByRole("button", { name: "Keep captured figure" }),
  );
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
});

test("a missing or shipped figure has no Withdraw", async () => {
  fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  await openRowMenu("Car expenses: cents per kilometre rate");
  expect(
    await screen.findByRole("menuitem", { name: "Capture figure" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("menuitem", { name: "Withdraw figure" }),
  ).toBeNull();
});

test("captures that no longer fit are listed at the top, each with Correct and Withdraw", async () => {
  const stale = { ...rate, capture: { ...CAPTURE, id: 9 } };
  const calls = fakeBackend({
    figures: [stale, limit],
    noLongerFit: [
      {
        capture_id: 9,
        key: rate.key,
        financial_year: 2027,
        financial_year_label: "2026-27",
        reason:
          "The release now limits it to $0.10 to $0.50 per km; re-capture it.",
      },
      {
        capture_id: 10,
        key: limit.key,
        financial_year: 2028,
        financial_year_label: "2027-28",
        reason: "Re-capture it.",
      },
    ],
  });
  await renderAppAt("/rule-figures?fy=2027");
  const notice = await screen.findByTestId("no-longer-fits");
  expect(notice).toHaveTextContent("2 captured figures no longer fit");
  expect(notice).toHaveTextContent("The release now limits it");
  // for the year on screen: Correct opens the sheet; for another year it links there
  expect(
    within(notice).getByRole("link", { name: "Show FY 2027-28 to correct it" }),
  ).toHaveAttribute("href", "/rule-figures?fy=2028");
  fireEvent.click(
    within(notice).getByRole("button", {
      name: "Correct Car expenses: cents per kilometre rate",
    }),
  );
  expect(
    await screen.findByRole("form", {
      name: "Correct Car expenses: cents per kilometre rate",
    }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  fireEvent.click(
    within(notice).getByRole("button", {
      name: "Withdraw Car expenses: cents per kilometre rate",
    }),
  );
  const ask = await screen.findByRole("alertdialog");
  fireEvent.click(within(ask).getByRole("button", { name: "Withdraw figure" }));
  await waitFor(() =>
    expect(
      calls.some(
        (c) => c.method === "DELETE" && c.path === "/api/rule-figures/9",
      ),
    ).toBe(true),
  );
});

test("Rule figures is in the sidebar under Reference and system, and the header links to the Guide", async () => {
  fakeBackend();
  await renderAppAt("/rule-figures?fy=2027");
  const nav = screen.getByRole("navigation", { name: "Sections" });
  const link = within(nav).getByRole("link", { name: "Rule figures" });
  expect(link).toHaveAttribute("href", "/rule-figures");
  expect(link).toHaveAttribute("aria-current", "page");
  expect(
    await screen.findByRole("link", { name: "How to capture a figure" }),
  ).toHaveAttribute("href", "/guide/rule-figures");
});

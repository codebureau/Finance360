import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { groupDigits, shortDate } from "@/components/tax-record-trace";
import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  notes: null,
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};
const setup = {
  rule_pack_version: "x",
  years: [],
  sections: [{ key: "business", name: "Sole trader" }],
  labels: [
    {
      key: "business_expenses",
      return_item: "P8/15",
      name: "Business expenses",
      description: "",
      direction: "deduction",
      kind: "money",
      modelled: true,
      section: "business",
      record_types: ["line", "home_office_hours"],
    },
  ],
};

const operand = (
  value: string,
  unit: string | null,
  label: string | null,
  operator: string | null = null,
) => ({ value, unit, label, operator });

const figure = {
  key: "tax.home_office.rate_per_hour",
  name: "Working from home: fixed rate per hour",
  financial_year: 2026,
  financial_year_label: "2025-26",
  value: "0.70 an hour",
  source: "ATO, Working from home expenses: fixed rate method",
  url: "https://www.ato.gov.au/invented/page",
  url_confirmed: true,
  retrieved: "2026-11-04",
  provisional: false,
  provisional_note: null,
  rule_pack_version: "au/2026-27/r5",
  origin: "shipped",
  capture_id: null,
  quote: null,
  captured_by: null,
  captured_at: null,
  shipped_value: null,
  method: null,
};

const record = {
  id: 8,
  member_id: 1,
  member_name: "Alex Sample",
  record_type: "home_office_hours",
  from_on: "2025-07-01",
  to_on: "2026-06-30",
  description: "Invented home office hours",
  notes: null,
  label_key: "business_expenses",
  label_name: "Business expenses",
  return_item: "P8/15",
  kind: "money",
  amount: null,
  work_use_percent: null,
  counted: "1037.40",
  value: null,
  statement: null,
  asset: null,
  home_office: {
    arrangement: "regular",
    hours_per_day: "7.60",
    home_weekdays: ["mon", "tue", "thu", "fri"],
    office_days_per_month: null,
    months: [],
    total_hours: "1482.00",
    rate_per_hour: "0.70",
    deduction: "1037.40",
    missing: [],
  },
  figures_used: [figure],
  calculation: {
    steps: [
      {
        title: "Hours worked from home",
        operands: [
          operand("195.00", "home days", "working days less leave and office"),
          operand("7.60", "hours a day", "hours per day", "×"),
        ],
        result: operand("1482.00", "hours", "total hours"),
        note: null,
      },
      {
        title: "Deduction",
        operands: [
          operand("1482.00", "hours", "total hours"),
          operand("0.70", "an hour", "rate for FY 2025-26", "×"),
        ],
        result: operand("1037.40", null, "deduction"),
        note: "Rounded once, to the cent, for the whole record (not month by month).",
      },
    ],
    waiting_for: [],
  },
};

function fakeBackend(records: Record<string, unknown>[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/library/check")
        return json({ checked: 0, problems: [] });
      if (path === "/api/library/links") return json([]);
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        const found = records.find((r) => r.id === Number(one[1]));
        return found ? json(found) : json({ message: "none" }, 404);
      }
      return json({ message: "not mocked" }, 404);
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

test("digits are grouped and dates read as text, with no rounding", () => {
  expect(groupDigits("1482.00")).toBe("1,482.00");
  expect(groupDigits("1234567.125")).toBe("1,234,567.125");
  expect(groupDigits("195")).toBe("195");
  expect(shortDate("2026-11-04")).toBe("4 Nov 2026");
});

test("the card replays the steps and links the official page in a new tab", async () => {
  fakeBackend([record]);
  await renderAppAt("/tax/records/8");
  const card = await screen.findByLabelText("How this is worked out");
  expect(card).toHaveTextContent("Hours worked from home");
  expect(card).toHaveTextContent("195.00home days");
  expect(card).toHaveTextContent("1,482.00hours");
  expect(card).toHaveTextContent("0.70an hour");
  expect(card).toHaveTextContent("1,037.40");
  expect(card).toHaveTextContent("Rounded once, to the cent");
  expect(within(card).getAllByText("×")).toHaveLength(2);
  expect(within(card).getByText("Where the figures come from")).toBeVisible();
  expect(card).toHaveTextContent("Working from home: fixed rate per hour");
  expect(card).toHaveTextContent("0.70 an hour");
  expect(card).toHaveTextContent("FY 2025-26");
  const link = within(card).getByRole("link", {
    name: "ATO, Working from home expenses: fixed rate method",
  });
  expect(link).toHaveAttribute("href", "https://www.ato.gov.au/invented/page");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link.getAttribute("rel")).toContain("noopener");
  expect(card).toHaveTextContent("captured 4 Nov 2026");
  expect(card).toHaveTextContent("Rule pack au/2026-27/r5");
  expect(card).not.toHaveTextContent("address not confirmed");
  expect(within(card).queryByText("Provisional")).not.toBeInTheDocument();
  expect(card).toHaveTextContent("Shipped in r5");
  expect(card).not.toHaveTextContent("Captured here");
});

test("a captured figure says where it came from, who captured it and what the release held", async () => {
  fakeBackend([
    {
      ...record,
      figures_used: [
        {
          ...figure,
          origin: "captured",
          capture_id: 12,
          quote: "An invented sentence about the rate.",
          captured_by: "owner",
          captured_at: "2026-11-05T01:30:00",
          shipped_value: "0.67 an hour",
          rule_pack_version: "au/2026-27/r5+1:a3f9c1",
        },
      ],
    },
  ]);
  await renderAppAt("/tax/records/8");
  const card = await screen.findByLabelText("How this is worked out");
  expect(card).toHaveTextContent(
    "Captured here on 5 Nov 2026 by the owner: “An invented sentence about the rate.”",
  );
  expect(card).toHaveTextContent("The release holds 0.67 an hour for this year.");
  expect(card).toHaveTextContent("Rule pack au/2026-27/r5+1:a3f9c1");
  expect(card).not.toHaveTextContent("Shipped in");
});

test.each([
  ["matched", "Matched by words on the page"],
  ["ai", "Read by AI"],
])("a figure captured with method %s says so on the trace card", async (method, label) => {
  fakeBackend([
    {
      ...record,
      figures_used: [
        {
          ...figure,
          origin: "captured",
          capture_id: 12,
          quote: "An invented sentence about the rate.",
          captured_by: "owner",
          captured_at: "2026-11-05T01:30:00",
          method,
          rule_pack_version: "au/2026-27/r5+1:a3f9c1",
        },
      ],
    },
  ]);
  await renderAppAt("/tax/records/8");
  const card = await screen.findByLabelText("How this is worked out");
  expect(card).toHaveTextContent(
    `Captured here on 5 Nov 2026 by the owner. ${label}: “An invented sentence about the rate.”`,
  );
});

test("an unconfirmed address and a provisional figure are both said", async () => {
  fakeBackend([
    {
      ...record,
      figures_used: [
        {
          ...figure,
          url_confirmed: false,
          provisional: true,
          provisional_note: "Invented note: announced, not yet law.",
        },
      ],
    },
  ]);
  await renderAppAt("/tax/records/8");
  const card = await screen.findByLabelText("How this is worked out");
  expect(card).toHaveTextContent("(address not confirmed)");
  expect(within(card).getByText("Provisional")).toBeInTheDocument();
  expect(card).toHaveTextContent("Invented note: announced, not yet law.");
});

test("a missing figure shows what the calculation is waiting for, beside the Figure needed alert", async () => {
  fakeBackend([
    {
      ...record,
      counted: null,
      figures_used: [],
      home_office: {
        ...record.home_office,
        rate_per_hour: null,
        deduction: null,
        missing: ["tax.home_office.rate_per_hour"],
      },
      calculation: {
        steps: [record.calculation.steps[0]],
        waiting_for: [
          {
            key: "tax.home_office.rate_per_hour",
            name: "Working from home: fixed rate per hour",
            financial_year: 2026,
            financial_year_label: "2025-26",
          },
        ],
      },
    },
  ]);
  await renderAppAt("/tax/records/8");
  const card = await screen.findByLabelText("How this is worked out");
  expect(card).toHaveTextContent("Waiting for a figure");
  expect(card).toHaveTextContent(
    "Working from home: fixed rate per hour (FY 2025-26)",
  );
  expect(card).toHaveTextContent("1,482.00hours");
  expect(card).not.toHaveTextContent("Where the figures come from");
  const capture = within(card).getByRole("link", { name: "Capture it on Rule figures (FY 2025-26)" });
  expect(capture).toHaveAttribute("href", "/rule-figures?fy=2026");
  // the page's own alert stays, and is still the only one
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "This record cannot be worked out yet",
  );
});

test("a record with no calculation shows no card", async () => {
  fakeBackend([{ ...record, calculation: null, figures_used: [] }]);
  await renderAppAt("/tax/records/8");
  await screen.findByText("Monthly worksheet");
  expect(
    screen.queryByLabelText("How this is worked out"),
  ).not.toBeInTheDocument();
});

import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { renderAppAt } from "@/test-utils";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const household = {
  name: "The Sample Household",
  time_zone: "Australia/Melbourne",
  members: [{ id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" }],
};
const labels = [
  {
    key: "business_expenses",
    return_item: "P8/15",
    name: "Business expenses",
    description: "",
    direction: "deduction",
    kind: "money",
    modelled: true,
    section: "business",
    record_types: ["line", "depreciating_asset", "home_office_hours"],
  },
  {
    key: "salary",
    return_item: "1",
    name: "Salary or wages",
    description: "",
    direction: "income",
    kind: "money",
    modelled: true,
    section: "business",
    record_types: ["line"],
  },
];
const setup = {
  rule_pack_version: "x",
  years: [],
  sections: [{ key: "business", name: "Sole trader" }],
  labels,
};

const month = (
  m: string,
  label: string,
  w: string,
  l: string,
  o: string,
  home: string,
  hours: string,
) => ({
  month: m,
  month_label: label,
  working_days: w,
  leave_days: l,
  office_days: o,
  home_days: home,
  hours,
});

const sheet = {
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
  counted: "162.21",
  value: null,
  statement: null,
  asset: null,
  home_office: {
    arrangement: "regular",
    hours_per_day: "7.50",
    home_weekdays: ["mon", "tue", "thu", "fri"],
    office_days_per_month: null,
    months: [
      month("2025-07", "July 2025", "22.00", "1.50", "4.00", "16.50", "123.75"),
      month(
        "2025-08",
        "August 2025",
        "21.00",
        "0.00",
        "3.50",
        "17.50",
        "131.25",
      ),
      month(
        "2025-09",
        "September 2025",
        "22.00",
        "2.25",
        "1.00",
        "18.75",
        "140.625",
      ),
    ],
    total_hours: "395.625",
    rate_per_hour: "0.41",
    deduction: "162.21",
    missing: [],
  },
};
const noRate = {
  ...sheet,
  id: 9,
  description: "Invented hours without a rate",
  counted: null,
  home_office: {
    ...sheet.home_office,
    rate_per_hour: null,
    deduction: null,
    missing: ["tax.home_office.rate_per_hour"],
  },
};

type Call = { method: string; path: string; body?: Record<string, unknown> };

function fakeBackend(
  records: Record<string, unknown>[] = [],
  failWith?: unknown,
) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body = ["POST", "PUT"].includes(request.method)
        ? await request.json()
        : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/tax/setup") return json(setup);
      if (path === "/api/household") return json(household);
      if (path === "/health") return json({ status: "ok" });
      if (path === "/api/tax/label-totals") {
        return json({
          financial_year: 2026,
          financial_year_label: "2025-26",
          totals: [],
          uncounted: [],
        });
      }
      if (path === "/api/library/check")
        return json({ checked: 0, problems: [] });
      if (path === "/api/library/links") return json([]);
      if (path === "/api/tax/records" && request.method === "POST") {
        return failWith ? json(failWith, 422) : json({ ...sheet, id: 99 }, 201);
      }
      const one = path.match(/^\/api\/tax\/records\/(\d+)$/);
      if (one) {
        const found = records.find((r) => r.id === Number(one[1]));
        return found
          ? json(found)
          : json({ message: "Tax record does not exist." }, 404);
      }
      if (path === "/api/tax/records") return json(records);
      return json({ message: "not mocked" }, 404);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

async function openForm(type = "home_office_hours") {
  await renderAppAt("/tax/records");
  fireEvent.click(
    await screen.findByRole("button", { name: "Add Tax record" }),
  );
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("What it records"), {
    target: { value: type },
  });
  fireEvent.change(within(form).getByLabelText("Return label"), {
    target: { value: "business_expenses" },
  });
  fireEvent.change(within(form).getByLabelText("Description"), {
    target: { value: "Invented home office hours" },
  });
  return form;
}

async function openYear() {
  const form = await openForm();
  setDates(form, "2025-07-01", "2026-06-30");
  return form;
}

function setDates(form: HTMLElement, from: string, to: string) {
  // The form starts on a financial year; part of a year is Custom dates.
  if (!within(form).queryByLabelText("From")) {
    fireEvent.click(within(form).getByLabelText("Custom dates"));
  }
  fireEvent.change(within(form).getByLabelText("From"), {
    target: { value: from },
  });
  fireEvent.change(within(form).getByLabelText("To"), {
    target: { value: to },
  });
}

const field = (form: HTMLElement, label: string) =>
  within(form).getByLabelText(label) as HTMLInputElement;
const monthRows = (form: HTMLElement) =>
  within(within(form).getByTestId("month-table")).getAllByRole("row").slice(1);
const post = (calls: Call[]) => calls.find((c) => c.method === "POST")?.body;

test("setting From and To lays out a prefilled row for every month, and a weekday pattern with one day in the office sends them as text", async () => {
  const calls = fakeBackend();
  const form = await openYear();
  expect(monthRows(form)).toHaveLength(12);
  expect(monthRows(form)[0]).toHaveTextContent("July 2025");
  expect(monthRows(form)[11]).toHaveTextContent("June 2026");
  // all five weekdays at home to begin with: the calendar's 23 weekdays, no office days
  expect(field(form, "Working days, July 2025").value).toBe("23");
  expect(field(form, "Office days, July 2025").value).toBe("0");
  expect(form).toHaveTextContent("5 days a week at home");
  fireEvent.change(field(form, "Hours per day"), { target: { value: "7.6" } });
  // Wednesdays in the office: July 2025 has five of them, so 18 home days x 7.6 = 136.80 hours
  fireEvent.click(within(form).getByRole("checkbox", { name: "Wednesday" }));
  expect(form).toHaveTextContent("4 days a week at home");
  expect(field(form, "Working days, July 2025").value).toBe("23");
  expect(field(form, "Office days, July 2025").value).toBe("5");
  expect(within(form).getByLabelText("Hours, July 2025")).toHaveTextContent(
    "136.80",
  );
  // February 2026 has 20 weekdays and four Wednesdays: 16 x 7.6 = 121.60
  expect(field(form, "Working days, February 2026").value).toBe("20");
  expect(within(form).getByLabelText("Hours, February 2026")).toHaveTextContent(
    "121.60",
  );
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  await waitFor(() => expect(post(calls)).toBeDefined());
  const body = post(calls) as Record<string, unknown> & {
    months: Record<string, string>[];
  };
  expect(body).toMatchObject({
    record_type: "home_office_hours",
    arrangement: "regular",
    hours_per_day: "7.6",
    home_weekdays: ["mon", "tue", "thu", "fri"],
    from_on: "2025-07-01",
    to_on: "2026-06-30",
  });
  expect(body.months).toHaveLength(12);
  expect(body.months[0]).toEqual({
    month: "2025-07",
    working_days: "23",
    leave_days: "",
    office_days: "5",
    hours: "",
  });
  expect(body).not.toHaveProperty("office_days_per_month");
});

test("a month the person edited keeps its values when the weekdays change, and untouched months follow", async () => {
  fakeBackend();
  const form = await openYear();
  fireEvent.change(field(form, "Hours per day"), { target: { value: "7.6" } });
  fireEvent.click(within(form).getByRole("checkbox", { name: "Wednesday" }));
  // July is edited: two days of leave typed over the prefill
  fireEvent.change(field(form, "Leave days, July 2025"), {
    target: { value: "2" },
  });
  // Friday goes to the office too: August 2025 has 21 weekdays, four Wednesdays and five Fridays
  fireEvent.click(within(form).getByRole("checkbox", { name: "Friday" }));
  expect(field(form, "Office days, August 2025").value).toBe("9");
  expect(field(form, "Office days, July 2025").value).toBe("5"); // not 10: it was edited
  expect(field(form, "Leave days, July 2025").value).toBe("2");
  // 23 - 2 - 5 = 16 home days x 7.6 = 121.60
  expect(within(form).getByLabelText("Hours, July 2025")).toHaveTextContent(
    "121.60",
  );
});

test("changing the dates adds and drops months and keeps what was typed for those still in range", async () => {
  fakeBackend();
  const form = await openYear();
  fireEvent.change(field(form, "Hours per day"), { target: { value: "7.6" } });
  fireEvent.change(field(form, "Leave days, September 2025"), {
    target: { value: "3" },
  });
  setDates(form, "2025-07-01", "2025-12-31");
  expect(monthRows(form)).toHaveLength(6);
  expect(field(form, "Leave days, September 2025").value).toBe("3");
  setDates(form, "2025-10-01", "2026-06-30");
  expect(monthRows(form)).toHaveLength(9);
  expect(monthRows(form)[0]).toHaveTextContent("October 2025");
  expect(
    within(form).queryByLabelText("Leave days, September 2025"),
  ).not.toBeInTheDocument();
  // back to the whole year: September returns with what was typed
  setDates(form, "2025-07-01", "2026-06-30");
  expect(monthRows(form)).toHaveLength(12);
  expect(field(form, "Leave days, September 2025").value).toBe("3");
});

test("no months are laid out until the dates are in one financial year", async () => {
  fakeBackend();
  const form = await openForm();
  expect(within(form).getByTestId("month-table")).toBeInTheDocument(); // starts on a year
  setDates(form, "2025-06-01", "2025-08-31"); // crosses 30 June
  expect(within(form).queryByTestId("month-table")).not.toBeInTheDocument();
  expect(form).toHaveTextContent(
    /Choose a From and To date in one financial year/,
  );
});

test("Add month only brings back a removed month within the dates, and is disabled when none is missing", async () => {
  fakeBackend();
  const form = await openYear();
  const add = within(form).getByRole("button", { name: "Add month" });
  expect(add).toBeDisabled();
  fireEvent.click(
    within(form).getByRole("button", { name: "Remove August 2025" }),
  );
  fireEvent.click(
    within(form).getByRole("button", { name: "Remove October 2025" }),
  );
  expect(monthRows(form)).toHaveLength(10);
  expect(add).toBeEnabled();
  fireEvent.click(add); // the earliest missing month
  expect(monthRows(form)[1]).toHaveTextContent("August 2025");
  expect(monthRows(form)).toHaveLength(11);
  fireEvent.click(add);
  expect(monthRows(form)).toHaveLength(12);
  expect(add).toBeDisabled();
});

test("kept as office days a month, the figure is prefilled into untouched months and capped at the working days", async () => {
  const calls = fakeBackend();
  const form = await openYear();
  fireEvent.change(field(form, "Hours per day"), { target: { value: "7.6" } });
  fireEvent.click(
    within(form).getByRole("radio", { name: "Office days a month" }),
  );
  fireEvent.change(
    within(form).getByRole("textbox", { name: /^Office days a month/ }),
    {
      target: { value: "2.5" },
    },
  );
  expect(field(form, "Working days, July 2025").value).toBe("23");
  expect(field(form, "Office days, July 2025").value).toBe("2.5");
  // (23 - 2.5) x 7.6 = 155.80
  expect(within(form).getByLabelText("Hours, July 2025")).toHaveTextContent(
    "155.80",
  );
  fireEvent.change(
    within(form).getByRole("textbox", { name: /^Office days a month/ }),
    {
      target: { value: "30" },
    },
  );
  expect(field(form, "Office days, July 2025").value).toBe("23"); // all 23 working days
  expect(field(form, "Office days, February 2026").value).toBe("20");
  fireEvent.change(
    within(form).getByRole("textbox", { name: /^Office days a month/ }),
    {
      target: { value: "2" },
    },
  );
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  await waitFor(() => expect(post(calls)).toBeDefined());
  const body = post(calls) as Record<string, unknown>;
  expect(body).toMatchObject({
    arrangement: "regular",
    office_days_per_month: "2",
  });
  expect(body).not.toHaveProperty("home_weekdays");
});

test("a casual record asks for the hours each month, and a blank month is not sent", async () => {
  const calls = fakeBackend();
  const form = await openYear();
  fireEvent.click(within(form).getByLabelText(/^Casual/));
  expect(
    within(form).queryByLabelText("Hours per day"),
  ).not.toBeInTheDocument();
  expect(monthRows(form)).toHaveLength(12);
  fireEvent.change(field(form, "Hours, July 2025"), {
    target: { value: "18.5" },
  });
  fireEvent.change(field(form, "Hours, September 2025"), {
    target: { value: "22" },
  });
  expect(within(form).getByLabelText("Worksheet totals")).toHaveTextContent(
    "40.5",
  );
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  await waitFor(() => expect(post(calls)).toBeDefined());
  const body = post(calls) as Record<string, unknown>;
  expect(body.arrangement).toBe("casual");
  expect(body).not.toHaveProperty("hours_per_day");
  expect(body).not.toHaveProperty("home_weekdays");
  const noDays = { working_days: "", leave_days: "", office_days: "" };
  expect(body.months).toEqual([
    { month: "2025-07", ...noDays, hours: "18.5" },
    { month: "2025-09", ...noDays, hours: "22" },
  ]);
});

test("a row whose leave and office days exceed its working days shows no hours and the server's message", async () => {
  fakeBackend([], {
    errors: [
      {
        field: "months.0.home_days",
        message:
          "July 2025: leave and office days are more than the working days.",
      },
    ],
  });
  const form = await openForm();
  setDates(form, "2025-07-01", "2025-07-31");
  fireEvent.change(field(form, "Hours per day"), { target: { value: "7.5" } });
  fireEvent.change(field(form, "Leave days, July 2025"), {
    target: { value: "20" },
  });
  fireEvent.change(field(form, "Office days, July 2025"), {
    target: { value: "5" },
  });
  expect(within(form).getByLabelText("Hours, July 2025")).toHaveTextContent(
    "–",
  );
  fireEvent.click(
    within(form).getByRole("button", { name: "Create Tax record" }),
  );
  expect(
    await within(form).findByText(
      /July 2025: leave and office days are more than the working days/,
    ),
  ).toBeInTheDocument();
});

test("the Sheet opens at full width for home office hours, collapses on request, and a click outside never closes it", async () => {
  fakeBackend();
  const form = await openForm("line");
  const sheetEl = screen.getByRole("dialog", { name: "New Tax record" });
  expect(sheetEl).toHaveAttribute("data-expanded", "false");
  fireEvent.change(within(form).getByLabelText("What it records"), {
    target: { value: "home_office_hours" },
  });
  await waitFor(() => expect(sheetEl).toHaveAttribute("data-expanded", "true"));
  fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
  expect(sheetEl).toHaveAttribute("data-expanded", "false");
  fireEvent.click(screen.getByRole("button", { name: "Expand" }));
  expect(sheetEl).toHaveAttribute("data-expanded", "true");
  // pointing or clicking on the page behind does not close it, and loses nothing
  fireEvent.change(within(form).getByLabelText("Description"), {
    target: { value: "Typed and kept" },
  });
  await new Promise((r) => setTimeout(r, 20));
  const behind = document.querySelector("main") as HTMLElement;
  for (const target of [document.body, behind]) {
    fireEvent.pointerDown(target);
    fireEvent.mouseDown(target);
    fireEvent.pointerUp(target);
    fireEvent.click(target);
  }
  expect(screen.getByRole("dialog", { name: "New Tax record" })).toBeVisible();
  expect(field(form, "Description").value).toBe("Typed and kept");
});

test("a Sheet that is not for home office hours opens at its normal width", async () => {
  fakeBackend();
  await openForm("line");
  expect(
    screen.getByRole("dialog", { name: "New Tax record" }),
  ).toHaveAttribute("data-expanded", "false");
});

test("Cancel closes straight away when nothing changed, and asks before discarding when something did", async () => {
  fakeBackend();
  await renderAppAt("/tax/records");
  fireEvent.click(
    await screen.findByRole("button", { name: "Add Tax record" }),
  );
  await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "New Tax record" }),
    ).not.toBeInTheDocument(),
  );
  // now with something typed: Cancel, the X and Esc each ask
  fireEvent.click(screen.getByRole("button", { name: "Add Tax record" }));
  const form = await screen.findByRole("form", { name: "New Tax record" });
  fireEvent.change(within(form).getByLabelText("Description"), {
    target: { value: "Typed and kept" },
  });
  const ways: (() => void)[] = [
    () => fireEvent.click(screen.getByRole("button", { name: "Cancel" })),
    () => fireEvent.click(screen.getByRole("button", { name: "Close panel" })),
    () =>
      fireEvent.keyDown(
        screen.getByRole("dialog", { name: "New Tax record" }),
        {
          key: "Escape",
        },
      ),
  ];
  for (const dismiss of ways) {
    dismiss();
    const ask = await screen.findByRole("alertdialog");
    expect(ask).toHaveTextContent("Discard your changes?");
    fireEvent.click(within(ask).getByRole("button", { name: "Keep editing" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("dialog", { name: "New Tax record" }),
    ).toBeVisible();
    expect(field(form, "Description").value).toBe("Typed and kept");
  }
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Discard",
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "New Tax record" }),
    ).not.toBeInTheDocument(),
  );
});

test("editing a saved record shows its months as saved and a weekday change leaves them alone", async () => {
  fakeBackend([sheet]);
  await renderAppAt("/tax/records/8");
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit Tax record" }),
  );
  const form = await screen.findByRole("form", {
    name: "Edit Invented home office hours",
  });
  // the saved months, not a fresh calendar prefill: July's 22 working days with 1.5 of leave
  expect(monthRows(form)).toHaveLength(3);
  expect(field(form, "Working days, July 2025").value).toBe("22.00");
  expect(field(form, "Leave days, July 2025").value).toBe("1.50");
  expect(
    within(form).getByRole("checkbox", { name: "Wednesday" }),
  ).not.toBeChecked();
  // the nine months the record never held come back one by one with Add month
  expect(
    within(form).queryByLabelText("Working days, October 2025"),
  ).not.toBeInTheDocument();
  expect(within(form).getByRole("button", { name: "Add month" })).toBeEnabled();
  fireEvent.click(within(form).getByRole("checkbox", { name: "Wednesday" }));
  expect(field(form, "Office days, July 2025").value).toBe("4.00");
  // the rate is known, so the preview is shown: 395.625 hours x 0.41 = 162.21
  const totals = within(form).getByLabelText("Worksheet totals");
  expect(totals).toHaveTextContent("395.625");
  expect(totals).toHaveTextContent("162.21");
});

test("the page shows the worksheet read-only with its total hours, rate and deduction", async () => {
  fakeBackend([sheet]);
  await renderAppAt("/tax/records/8");
  const table = await screen.findByRole("table", {
    name: "Home office months",
  });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent("July 2025");
  expect(rows[0]).toHaveTextContent("16.50");
  expect(rows[0]).toHaveTextContent("123.75");
  expect(rows[2]).toHaveTextContent("140.625");
  const card = screen.getByLabelText("Home office worksheet");
  expect(card).toHaveTextContent("395.625");
  expect(card).toHaveTextContent("0.41");
  expect(card).toHaveTextContent("162.21");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a worksheet whose rate is not captured says so and points at Rule figures", async () => {
  fakeBackend([noRate]);
  await renderAppAt("/tax/records/9");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("tax.home_office.rate_per_hour");
  expect(alert).toHaveTextContent("Capture the figure on Rule figures (FY 2025-26).");
  expect(alert).not.toHaveTextContent("rulepack_update");
  expect((await screen.findAllByText("Figure needed")).length).toBeGreaterThan(
    0,
  );
});

test("the register shows Figure needed for a worksheet with no rate", async () => {
  fakeBackend([noRate]);
  await renderAppAt("/tax/records");
  expect((await screen.findAllByText("Figure needed")).length).toBeGreaterThan(
    0,
  );
});

const casualSheet = {
  ...sheet,
  id: 10,
  description: "Invented casual hours",
  counted: "26.86",
  home_office: {
    arrangement: "casual",
    hours_per_day: null,
    home_weekdays: null,
    office_days_per_month: null,
    months: [
      {
        month: "2025-07",
        month_label: "July 2025",
        working_days: null,
        leave_days: null,
        office_days: null,
        home_days: null,
        hours: "18.50",
      },
    ],
    total_hours: "18.50",
    rate_per_hour: "0.41",
    deduction: "7.59",
    missing: [],
  },
};

test("the page says which arrangement a record is and shows the table for that shape", async () => {
  fakeBackend([sheet, casualSheet]);
  await renderAppAt("/tax/records/8");
  expect(await screen.findByText("Arrangement")).toBeInTheDocument();
  expect(
    screen.getByText("Regular: Mon, Tue, Thu, Fri at home, 7.50 hours a day"),
  ).toBeInTheDocument();
  cleanup();
  await renderAppAt("/tax/records/10");
  expect(await screen.findByText("Casual: actual hours")).toBeInTheDocument();
  const table = await screen.findByRole("table", {
    name: "Home office months",
  });
  expect(within(table).queryByText("Working days")).not.toBeInTheDocument();
  expect(within(table).getAllByRole("row")[1]).toHaveTextContent("18.50");
});

test("a record saved before arrangements existed reads as regular with no weekdays, and a monthly pattern reads as office days a month", async () => {
  const legacy = {
    ...sheet,
    id: 11,
    home_office: {
      ...sheet.home_office,
      home_weekdays: null,
    },
  };
  const monthly = {
    ...sheet,
    id: 12,
    home_office: {
      ...sheet.home_office,
      home_weekdays: null,
      office_days_per_month: "2.00",
    },
  };
  fakeBackend([legacy, monthly]);
  await renderAppAt("/tax/records/11");
  expect(
    await screen.findByText("Regular, 7.50 hours a day"),
  ).toBeInTheDocument();
  cleanup();
  await renderAppAt("/tax/records/12");
  expect(
    await screen.findByText("Regular: 2 office days a month, 7.50 hours a day"),
  ).toBeInTheDocument();
});

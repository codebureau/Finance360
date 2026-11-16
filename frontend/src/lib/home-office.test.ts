import { expect, test } from "vitest";

import {
  deductionText,
  hoursFromTenThousandths,
  hundredthsText,
  monthsInRange,
  prefillRegular,
  rowTenThousandths,
  weekdayCounts,
  weekdaysIn,
} from "@/lib/tax";

test("July 2025 has 23 weekdays, with five each of Tuesday, Wednesday and Thursday (it starts on a Tuesday)", () => {
  expect(weekdaysIn("2025-07")).toBe(23);
  expect(weekdayCounts("2025-07")).toEqual({
    mon: 4,
    tue: 5,
    wed: 5,
    thu: 5,
    fri: 4,
  });
  // the Wednesday pattern's office days: 5; the other four weekdays are 18 home days
  expect(weekdayCounts("2025-07").wed).toBe(5);
});

test("February counts follow the calendar, including a leap year", () => {
  expect(weekdaysIn("2026-02")).toBe(20); // 28 days, four whole weeks
  expect(weekdayCounts("2026-02")).toEqual({
    mon: 4,
    tue: 4,
    wed: 4,
    thu: 4,
    fri: 4,
  });
  expect(weekdaysIn("2024-02")).toBe(21); // 29 days; 1 February 2024 was a Thursday
  expect(weekdayCounts("2024-02").thu).toBe(5);
});

test("the months of a record are the months from the From date to the To date, in one financial year only", () => {
  expect(monthsInRange("2025-07-01", "2026-06-30")).toHaveLength(12);
  expect(monthsInRange("2025-09-15", "2025-11-02")).toEqual([
    "2025-09",
    "2025-10",
    "2025-11",
  ]);
  expect(monthsInRange("2025-06-01", "2025-08-31")).toEqual([]); // crosses 30 June
  expect(monthsInRange("2025-08-31", "2025-08-01")).toEqual([]); // backwards
  expect(monthsInRange("2025-08-01", "")).toEqual([]);
});

test("a regular month is prefilled from the weekdays at home, or from the office days a month", () => {
  const weekly = {
    kind: "weekly",
    weekdays: ["mon", "tue", "thu", "fri"],
  } as const;
  expect(prefillRegular("2025-07", weekly)).toEqual({
    working: "23",
    office: "5",
  });
  expect(
    prefillRegular("2025-07", {
      kind: "weekly",
      weekdays: ["mon", "tue", "wed", "thu", "fri"],
    }).office,
  ).toBe("0");
  expect(
    prefillRegular("2025-07", { kind: "weekly", weekdays: [] }).office,
  ).toBe("23");
  const monthly = (perMonth: string) =>
    prefillRegular("2026-02", { kind: "monthly", perMonth });
  expect(monthly("2")).toEqual({ working: "20", office: "2" });
  expect(monthly("2.5").office).toBe("2.5");
  expect(monthly("45").office).toBe("20"); // capped at the working days
  expect(monthly("").office).toBe("");
});

test("hours and the deduction are worked in whole numbers: home days x hours per day, then rate x hours rounded once, half to even", () => {
  // (23 - 0 - 5) x 7.6 = 136.8
  const row = rowTenThousandths("7.6", "23", "", "5");
  expect(row).toBe(1_368_000);
  expect(hoursFromTenThousandths(row as number)).toBe("136.80");
  expect(rowTenThousandths("7.6", "5", "3", "3")).toBeNull(); // negative home days
  expect(hoursFromTenThousandths(1_406_250)).toBe("140.625");
  // 1.5 hours at 41 cents = 61.5 cents -> 62 ; 2.5 hours = 102.5 cents -> 102
  expect(deductionText("0.41", 15_000)).toBe("0.62");
  expect(deductionText("0.41", 25_000)).toBe("1.02");
  // 1,482.00 hours at 0.70 = 1,037.40
  expect(deductionText("0.70", 14_820_000)).toBe("1037.40");
  expect(hundredthsText(250)).toBe("2.5");
  expect(hundredthsText(200)).toBe("2");
  expect(hundredthsText(225)).toBe("2.25");
});

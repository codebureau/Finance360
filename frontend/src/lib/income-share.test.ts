import { expect, test } from "vitest";

import { packageHeadline, packageShareRows } from "@/lib/income-share";
import { toMinor } from "@/lib/money";
import type { IncomeReconciliation } from "@/lib/tax";

// Invented figures.
const slice = (key: string, name: string, amount: string) => ({ key, name, amount, share_percent: "0" });
const item = (slices: ReturnType<typeof slice>[]) => ({ slices }) as unknown as IncomeReconciliation;
const alex = item([
  slice("tax", "Tax withheld (includes Medicare levy)", "24000.00"),
  slice("super", "Super", "12000.00"),
  slice("lease", "Novated lease costs", "5000.00"),
  slice("other", "Other deductions", "1000.00"),
  slice("take_home", "Take-home", "69000.00"),
  slice("unreconciled", "Unreconciled", "1000.00"),
]);

test("the segments are the table's figures, every part drawn, Unreconciled included and not folded", () => {
  const rows = packageShareRows(alex);
  expect(rows.map((r) => r.key)).toEqual(["tax", "super", "lease", "other", "take_home", "unreconciled"]);
  expect(rows.map((r) => r.amountText)).toEqual(["24,000.00", "12,000.00", "5,000.00", "1,000.00", "69,000.00", "1,000.00"]);
  // Shares are integer parts per million of the whole; they add up to the whole within rounding.
  const whole = rows.reduce((sum, r) => sum + r.ppm, 0);
  expect(whole).toBeGreaterThan(999_990);
  expect(whole).toBeLessThanOrEqual(1_000_000);
  expect(rows.find((r) => r.key === "take_home")?.percentText).toBe("61.6%");
  // Source minor units match the response, so nothing was recalculated.
  expect(alex.slices.reduce((sum, s) => sum + (toMinor(s.amount) ?? 0n), 0n)).toBe(11_200_000n);
});

test("each part has its own fixed colour and Unreconciled is never muted into Other", () => {
  const rows = packageShareRows(alex);
  expect(rows.map((r) => r.tone)).toEqual([
    { token: 0 },
    { token: 1 },
    { token: 2 },
    { token: 3 },
    { token: 4 },
    { token: 5 },
  ]);
});

test("a part with no amount is left out, and no gap means no Unreconciled part", () => {
  const rows = packageShareRows(item([slice("tax", "Tax", "10.00"), slice("lease", "Lease", "0.00"), slice("take_home", "Take-home", "90.00")]));
  expect(rows.map((r) => r.key)).toEqual(["tax", "take_home"]);
});

test("the headline leads with take-home and names the next largest part", () => {
  expect(packageHeadline(packageShareRows(alex))).toBe("Take-home is 61.6% of the package; tax withheld is the next largest.");
});

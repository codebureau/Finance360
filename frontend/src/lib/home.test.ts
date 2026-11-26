import { expect, test } from "vitest";

import { netDelta, previousMonthEnd, shortDate } from "@/lib/home";
import type { HistoryPoint } from "@/lib/position";

const fig = (household: string) => ({ household, joint: "0.00", members: [] });
const point = (on: string, net: string, flagged = 0): HistoryPoint => ({
  on,
  assets: fig(net),
  liabilities: fig("0.00"),
  net: fig(net),
  flagged,
});
const prev = { on: "2026-10-31", net: "100000.00" };

test("up: sign, exact change and words", () => {
  const d = netDelta("101200.50", prev, "2026-11-26");
  expect(d).toEqual({
    direction: "up",
    signed: "+1,200.50",
    words: "up 1,200.50 since 31 Oct",
    headline: "Net position is up since last month",
  });
});

test("down, including across zero and with a large exact figure", () => {
  const d = netDelta("-50.01", { on: "2026-10-31", net: "9007199254740993.99" }, "2026-11-26");
  expect(d?.direction).toBe("down");
  expect(d?.signed).toBe("-9,007,199,254,741,044.00");
  expect(d?.headline).toBe("Net position is down since last month");
  expect(netDelta("99999.99", prev, "2026-11-26")?.signed).toBe("-0.01");
});

test("flat: unchanged, no figure in the words", () => {
  const d = netDelta("100000.00", prev, "2026-11-26");
  expect(d).toMatchObject({ direction: "flat", signed: "0.00", words: "unchanged since 31 Oct", headline: "Net position is unchanged since last month" });
});

test("no previous month-end: no delta", () => {
  expect(netDelta("100.00", null, "2026-11-26")).toBeNull();
});

test("the date names the year when it is not this year", () => {
  expect(shortDate("2025-12-31", "2026-01-05")).toBe("31 Dec 2025");
  expect(shortDate("2026-10-03", "2026-11-26")).toBe("3 Oct");
});

test("previousMonthEnd: the last point before today, ignoring today's own point", () => {
  const points = [point("2026-09-30", "1.00"), point("2026-10-31", "2.00"), point("2026-11-26", "3.00")];
  expect(previousMonthEnd(points, "2026-11-26")?.on).toBe("2026-10-31");
});

test("previousMonthEnd: none when only today's point exists, or the previous one is incomplete", () => {
  expect(previousMonthEnd([point("2026-11-26", "3.00")], "2026-11-26")).toBeNull();
  expect(previousMonthEnd([point("2026-10-31", "2.00", 1), point("2026-11-26", "3.00")], "2026-11-26")).toBeNull();
});

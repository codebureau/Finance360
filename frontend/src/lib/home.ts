import { formatMoney, fromMinor, toMinor } from "@/lib/money";
import type { HistoryPoint } from "@/lib/position";

export type DeltaDirection = "up" | "down" | "flat";

export type NetDelta = {
  direction: DeltaDirection;
  /** The sign and the change, exact: "+1,200.00", "-300.00", or "0.00". */
  signed: string;
  /** "up since 31 Oct": the direction in words. Colour is never the only carrier. */
  words: string;
  /** "Net position is up since last month". */
  headline: string;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "31 Oct", or "31 Oct 2025" when it is not in the same year as `asAt`. No time-zone shift. */
export function shortDate(iso: string, asAt: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const base = `${d} ${MONTHS[m - 1]}`;
  return String(y) === asAt.slice(0, 4) ? base : `${base} ${y}`;
}

/**
 * The month-end to compare against: the last history point before `asAt` that counts every
 * Account. An incomplete point (an Account with no Valuation yet) would make the change a
 * comparison of unlike sets, so it gives no delta. None before: no delta.
 */
export function previousMonthEnd(points: HistoryPoint[], asAt: string): HistoryPoint | null {
  const before = points.filter((p) => p.on < asAt).sort((a, b) => (a.on < b.on ? -1 : 1));
  const last = before[before.length - 1];
  return last && last.flagged === 0 ? last : null;
}

/**
 * The headline and delta for the Home tile, from exact strings (integer cents, never a float).
 * `null` when there is no previous month-end, or an amount cannot be read.
 */
export function netDelta(current: string, previous: { on: string; net: string } | null, asAt: string): NetDelta | null {
  if (!previous) return null;
  const now = toMinor(current);
  const then = toMinor(previous.net);
  if (now === null || then === null) return null;
  const change = now - then;
  const since = shortDate(previous.on, asAt);
  if (change === 0n) {
    return { direction: "flat", signed: formatMoney(fromMinor(0n)), words: `unchanged since ${since}`, headline: "Net position is unchanged since last month" };
  }
  const up = change > 0n;
  const size = formatMoney(fromMinor(up ? change : -change));
  return {
    direction: up ? "up" : "down",
    signed: `${up ? "+" : "-"}${size}`,
    words: `${up ? "up" : "down"} ${size} since ${since}`,
    headline: `Net position is ${up ? "up" : "down"} since last month`,
  };
}

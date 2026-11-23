import { groupDigits } from "@/lib/valuations";

/**
 * The one place a money amount becomes display text. Amounts arrive from the API as exact decimal
 * strings and are only ever regrouped as text, never parsed into a number. `null` (an Account that
 * could not be valued) renders as a dash, never as zero.
 */
export function formatMoney(amount: string | null | undefined, currency = "AUD"): string {
  if (amount === null || amount === undefined) return "—";
  return currency === "AUD" ? groupDigits(amount) : `${currency} ${groupDigits(amount)}`;
}

/**
 * An exact decimal string in integer minor units (cents), for shares and comparisons only: never
 * displayed and never turned into a number. A third decimal place is dropped, not rounded; the API
 * sends two. Null if the text is not a plain decimal.
 */
export function toMinor(amount: string): bigint | null {
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(amount.trim());
  if (!match) return null;
  const minor = BigInt(match[2]) * 100n + BigInt((match[3] ?? "").padEnd(2, "0").slice(0, 2));
  return match[1] ? -minor : minor;
}

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

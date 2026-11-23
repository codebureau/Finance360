import type { ShareForm, ShareRow } from "@/lib/charts/share";
import { toneOf } from "@/lib/entity-colour";
import { formatMoney, toMinor } from "@/lib/money";
import { percentText } from "@/lib/position-share";
import type { IncomeReconciliation } from "@/lib/tax";

/**
 * Shapes an Income reconciliation response into the `share` rows of "Where the package goes" (a
 * pure function over the existing response; nothing is recalculated). Money rule: amounts stay the
 * API's exact strings; shares are integer parts per million of the slices drawn, which are the
 * parts the table lists. Every part is drawn (never folded); Unreconciled is drawn when the API
 * sends it (a positive gap); a negative gap is a note, as before.
 */

/** The forms this view offers: the stacked bar first, so it is the view's own default. */
export const PACKAGE_FORMS: ShareForm[] = ["stacked-bar", "donut", "bars"];

/** The legend's name for each part (the table keeps the API's longer names). */
const NAMES: Record<string, string> = {
  tax: "Tax withheld",
  super: "Super",
  lease: "Novated lease costs",
  other: "Other deductions",
  take_home: "Take-home",
  unreconciled: "Unreconciled",
};

// The API's "other" is Other deductions; the colour table must not read it as the folded "Other".
const toneKey = (key: string) => (key === "other" ? "other_deductions" : key);

export function packageShareRows(item: IncomeReconciliation): ShareRow[] {
  const parts = item.slices
    .map((s) => ({ s, minor: toMinor(s.amount) ?? 0n }))
    .filter((p) => p.minor > 0n);
  const whole = parts.reduce((sum, p) => sum + p.minor, 0n);
  return parts.map(({ s, minor }) => {
    const ppm = whole > 0n ? Number((minor * 1_000_000n) / whole) : 0;
    return {
      key: s.key,
      name: NAMES[s.key] ?? s.name,
      ppm,
      percentText: percentText(ppm),
      amountText: formatMoney(s.amount),
      tone: toneOf("package-part", toneKey(s.key)),
    };
  });
}

/** What to notice: Take-home's share of the package and the next largest part. */
export function packageHeadline(rows: ShareRow[]): string {
  const takeHome = rows.find((r) => r.key === "take_home");
  const others = rows
    .filter((r) => r.key !== "take_home" && r.key !== "unreconciled")
    .sort((a, b) => b.ppm - a.ppm);
  const next = others[0];
  if (!takeHome) {
    return next ? `${next.name} is the largest part of the package, ${next.percentText}.` : "Nothing is accounted for yet.";
  }
  const lead = `Take-home is ${takeHome.percentText} of the package`;
  return next ? `${lead}; ${next.name.toLowerCase()} is the next largest.` : `${lead}.`;
}

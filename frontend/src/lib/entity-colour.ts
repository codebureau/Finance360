/**
 * Entity colour (ADR-0031 section 5): a label keeps one colour on every screen and under every
 * filter. The colour follows the entity, never its rank or its position in a list. Pure.
 */

/** A chart token (0 based: `--chart-1` is 0), muted (Other and residual lines) or the warning tone
 * (an explicit line that needs attention: No profile). */
export type Tone = { token: number } | "muted" | "warning";

/** The six categorical tokens. A seventh hue is never generated. */
export const TOKEN_COUNT = 6;

export type Family = "category" | "asset-class" | "member" | "scenario" | "dimension" | "package-part";

/** Families with at most six keys have a fixed table (ADR-0031 section 5). */
const FIXED: Partial<Record<Family, Record<string, number>>> = {
  category: {
    investments: 0,
    property: 1,
    cash: 2,
    superannuation: 3,
    overseas_pensions: 4,
    vehicles: 5,
  },
  "asset-class": {
    australian_shares: 0,
    property: 1,
    cash: 2,
    international_shares: 3,
    fixed_income: 4,
    infrastructure: 5,
  },
  // The parts of a total package (Income reconciliation): six fixed parts, so each keeps its colour
  // on every card. Unreconciled is a part, not a status: a chart token, never green, red or amber.
  "package-part": {
    tax: 0,
    super: 1,
    lease: 2,
    other_deductions: 3,
    take_home: 4,
    unreconciled: 5,
  },
};

/**
 * The tone of `key` in `family`. A fixed-table family looks the key up; a key it does not list (a
 * Category the household added) and every ordered family (Members, Scenarios, other Dimensions)
 * take `order` (the key's stable position: vocabulary seed order or creation order) modulo six.
 * "other", an unknown fixed key with no order, and anything unplaced are muted.
 */
export function toneOf(family: Family, key: string, order?: number): Tone {
  if (key === "other") return "muted";
  const fixed = FIXED[family]?.[key];
  if (fixed !== undefined) return { token: fixed };
  if (order === undefined || order < 0) return "muted";
  return { token: order % TOKEN_COUNT };
}

/** Tailwind classes for a group's token, written out so the class scanner finds them. */
export const TOKEN_TEXT = [
  "text-chart-1",
  "text-chart-2",
  "text-chart-3",
  "text-chart-4",
  "text-chart-5",
  "text-chart-6",
] as const;
export const TOKEN_BG = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
  "bg-chart-6",
] as const;
export const TOKEN_TINT = [
  "bg-chart-1/10",
  "bg-chart-2/10",
  "bg-chart-3/10",
  "bg-chart-4/10",
  "bg-chart-5/10",
  "bg-chart-6/10",
] as const;

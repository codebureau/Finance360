/**
 * Entity colour (ADR-0031 section 5): a label keeps one colour on every screen and under every
 * filter. The colour follows the entity, never its rank or its position in a list. Pure.
 */

/** A chart token (0 based: `--chart-1` is 0), muted (Other and residual lines), the warning tone
 * (an explicit line that needs attention: No profile) or neutral ink (the answer line: Net). */
export type Tone = { token: number } | "muted" | "warning" | "ink";

/** The six categorical tokens. A seventh hue is never generated. */
export const TOKEN_COUNT = 6;

export type Family = "category" | "asset-class" | "member" | "scenario" | "dimension" | "package-part" | "position-part";

/** Families with at most six keys have a fixed table (ADR-0031 section 5). */
const FIXED: Partial<Record<Family, Record<string, number | "ink">>> = {
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
  // Assets, Liabilities and Net on Position "Over time". Assets and Liabilities wear the two
  // Category tokens least likely to sit beside them in the share chart above (Overseas pensions,
  // magenta; Vehicles, gold). Net is the answer, so it is neutral ink and drawn bold. Never a
  // status colour: Liabilities is not "bad", and also keeps a dash so colour is not the only cue.
  "position-part": {
    assets: 4,
    liabilities: 5,
    net: "ink",
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
  if (fixed === "ink") return "ink";
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

/** The background class of a swatch for a tone, written out so the class scanner finds them. */
export function toneBg(tone: Tone): string {
  if (tone === "warning") return "bg-warning/85";
  if (tone === "muted") return "bg-muted-foreground/55";
  if (tone === "ink") return "bg-foreground";
  return TOKEN_BG[tone.token % TOKEN_BG.length];
}

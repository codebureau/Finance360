import { headline, lookThroughRows } from "@/lib/exposure-chart";
import type { ExposureDimension, RolledFigures, Rollup } from "@/lib/exposure";
import type { ShareRow } from "@/lib/charts/share";
import { formatMoney } from "@/lib/money";

/**
 * Presentation of the Household roll-up (ADR-0030 section 8). Pure, so it is unit-tested without a
 * canvas. Money rule: every amount shown is the API's exact string through `formatMoney`; the share
 * of the Position that is analysed is worked in integers (BigInt) and is display only.
 */

export type Level = "household" | "member" | "category" | "account";
export const LEVELS: { key: Level; label: string }[] = [
  { key: "household", label: "Household" },
  { key: "member", label: "Member" },
  { key: "category", label: "Category" },
  { key: "account", label: "Account" },
];

export type ExposureSearch = { dimension?: string; level?: Level; asat?: string };

/** The URL's view: a named level (Household is the default and is left out), a Dimension key and a date. */
export function parseExposureSearch(raw: Record<string, unknown>): ExposureSearch {
  const out: ExposureSearch = {};
  if (typeof raw.dimension === "string" && /^[a-z_]{1,30}$/.test(raw.dimension)) out.dimension = raw.dimension;
  if (raw.level === "member" || raw.level === "category" || raw.level === "account") out.level = raw.level;
  if (typeof raw.asat === "string" && /^\d{4}-\d{2}-\d{2}$/.test(raw.asat)) out.asat = raw.asat;
  return out;
}

/** One thing the roll-up reports on: the Household, a Member, a Category or an analysed Account. */
export type Entity = {
  key: string;
  name: string;
  figures: RolledFigures;
  /** An Account: where its own look-through, the source of these figures, is. */
  accountId?: number;
};

export function entitiesOf(rollup: Rollup, level: Level): Entity[] {
  switch (level) {
    case "household":
      return [{ key: "household", name: "Household", figures: rollup.household }];
    case "member":
      return rollup.members.map((m) => ({
        key: `member-${m.member_id}`,
        name: m.name,
        figures: m.figures,
      }));
    case "category":
      return rollup.categories.map((c) => ({ key: `category-${c.key}`, name: c.name, figures: c.figures }));
    case "account":
      return rollup.accounts.map((a) => ({
        key: `account-${a.account_id}`,
        name: a.name,
        figures: a.figures,
        accountId: a.account_id,
      }));
  }
}

/** Entities with nothing analysed in them have no chart (their value is all Not analysed). */
export const hasChart = (e: Entity) => e.figures.lines.length > 0;

/** The chart rows of one entity: its lines by label, folded to at most six parts. */
export function entityRows(e: Entity, rollup: Rollup, vocab: ExposureDimension[]): ShareRow[] {
  return lookThroughRows({ dimension_key: rollup.dimension_key, lines: e.figures.lines }, vocab);
}

/** `part` as a whole percent of `whole` (minor units), rounded half up in integers; `null` when
 * `whole` is not positive. */
export function wholePercent(part: number, whole: number): number | null {
  if (whole <= 0) return null;
  const w = BigInt(whole);
  return Number((BigInt(part) * 100n + w / 2n) / w);
}

/** The Household's headline: how much of the Position is analysed, then what to notice in it. */
export function householdHeadline(rollup: Rollup): string {
  const { household } = rollup;
  const share = wholePercent(household.analysed.amount_minor, household.total.amount_minor);
  const noted = headline({ dimension_name: rollup.dimension_name, lines: household.lines });
  if (household.lines.length === 0)
    return `Nothing is analysed by ${rollup.dimension_name.toLowerCase()} yet: no Account holds Investments or a Super statement with a profile.`;
  const lead =
    share === null
      ? `${formatMoney(household.analysed.amount, household.analysed.currency)} is analysed`
      : `${share}% of the Position (${formatMoney(household.analysed.amount, household.analysed.currency)}) is analysed`;
  return `${lead}. ${noted}.`;
}

const PLURAL: Record<Exclude<Level, "household">, string> = {
  member: "Member",
  category: "Category",
  account: "Account",
};

/** What to notice across Members, Categories or Accounts: who holds most of the analysed value. */
export function levelHeadline(rollup: Rollup, level: Exclude<Level, "household">): string {
  const whole = rollup.household.analysed.amount_minor;
  const charted = entitiesOf(rollup, level)
    .filter(hasChart)
    .sort((a, b) => b.figures.analysed.amount_minor - a.figures.analysed.amount_minor);
  if (charted.length === 0) return `No ${PLURAL[level]} has anything analysed yet.`;
  const pct = (e: Entity) => wholePercent(e.figures.analysed.amount_minor, whole);
  const [top, next] = charted;
  if (!next) return `All of the analysed value is in ${top.name}.`;
  return `${top.name} holds the most analysed value, ${pct(top) ?? 0}% of it; ${next.name} holds ${pct(next) ?? 0}%.`;
}

/**
 * Why a whole Category has no look-through, in plain words: said once per group on the Not analysed
 * card, not once per Account. A Category not listed here gets the general sentence.
 */
const NO_LOOK_THROUGH: Record<string, string> = {
  cash: "Held as money, so there is nothing inside to look through.",
  property: "Owned directly, not through investments, so there is nothing to look through.",
  vehicles: "Owned directly, not through investments, so there is nothing to look through.",
  loans_credit: "Debts: they reduce the Position but hold no investments.",
  overseas_pensions: "Valued from statements of the pension, not from what it holds.",
};
const NO_LOOK_THROUGH_DEFAULT = "Valued from recorded Valuations, not from Holdings or a Super statement.";

export type NotAnalysedGroup = {
  key: string;
  name: string;
  /** Said once for the group when any Account in it has no look-through by kind. */
  reason: string | null;
  accounts: Rollup["not_analysed"];
};

/** The Not analysed Accounts grouped by Category, in the roll-up's Category order. */
export function notAnalysedGroups(rollup: Rollup): NotAnalysedGroup[] {
  const order = rollup.categories.map((c) => c.key);
  const names = new Map(rollup.categories.map((c) => [c.key, c.name]));
  const groups = new Map<string, NotAnalysedGroup>();
  for (const n of rollup.not_analysed) {
    let g = groups.get(n.category_key);
    if (!g) {
      g = { key: n.category_key, name: names.get(n.category_key) ?? n.category_key, reason: null, accounts: [] };
      groups.set(n.category_key, g);
    }
    g.accounts.push(n);
    if (n.reason === null && n.value !== null) g.reason = NO_LOOK_THROUGH[n.category_key] ?? NO_LOOK_THROUGH_DEFAULT;
  }
  const rank = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length);
  return [...groups.values()].sort((a, b) => rank(a.key) - rank(b.key));
}

import { areaOf } from "@/components/area-chip";
import type { GroupTone } from "@/components/sidebar-nav";
import type { DueItem } from "@/lib/due";

/**
 * The card view of What's due (ADR-0031, shape `collection`, view key `whats-due.items`). A pure
 * model over the same Due items the list shows: the title, the effect line, the importance and the
 * link are the item's own, never reworded; the cards add only an area, a group and an age line.
 */

/** The view key of What's due's items (a `collection`, forms Cards then List). */
export const DUE_VIEW_KEY = "whats-due.items";

export type DueCard = {
  /** `rule_key|subject_key`, the item's identity. */
  id: string;
  item: DueItem;
  title: string;
  effect: string;
  importance: "important" | "optional";
  /** The word on the badge: severity is never colour alone. */
  importanceLabel: "Important" | "Optional";
  /** The age or gap in plain words. */
  age: string;
};

export type DueGroup = {
  /** The area's name (a sidebar group heading), or "Other" for a link no area owns. */
  area: string;
  /** The area's colour token (0 based), or null for muted. */
  tone: GroupTone;
  count: number;
  cards: DueCard[];
};

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const dayNumber = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
};

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

/** "1 July 2026" from an ISO date, with no time zone in the way. */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** How long a Due item has been waiting, in plain words and never as lateness. */
export function ageWords(dueOn: string, today: string): string {
  const days = dayNumber(today) - dayNumber(dueOn);
  if (days < 0) return `Comes due on ${longDate(dueOn)}`;
  if (days === 0) return "Due today";
  const span =
    days < 14
      ? plural(days, "day")
      : days < 60
        ? plural(Math.round(days / 7), "week")
        : days < 730
          ? plural(Math.round(days / 30.4), "month")
          : plural(Math.round(days / 365.25), "year");
  return `Worth refreshing since ${longDate(dueOn)} (${span} ago)`;
}

export function dueCard(item: DueItem, today: string): DueCard {
  const important = item.importance === "important";
  return {
    id: `${item.rule_key}|${item.subject_key}`,
    item,
    title: item.title,
    effect: item.effect,
    importance: important ? "important" : "optional",
    importanceLabel: important ? "Important" : "Optional",
    age: ageWords(item.due_on, today),
  };
}

/**
 * The items grouped by area (the sidebar group the item's link lands in), a count per group. The
 * items arrive important first and the groups keep the order they first appear in, so a group
 * holding an important item leads. Within a group the order is the list's.
 */
export function dueGroups(items: DueItem[], today: string): DueGroup[] {
  const groups = new Map<string, DueGroup>();
  for (const item of items) {
    const area = areaOf(item.link.split("?")[0]);
    const key = area?.heading ?? "Other";
    let group = groups.get(key);
    if (!group) {
      group = { area: key, tone: area ? area.tone : null, count: 0, cards: [] };
      groups.set(key, group);
    }
    group.cards.push(dueCard(item, today));
    group.count += 1;
  }
  return [...groups.values()];
}

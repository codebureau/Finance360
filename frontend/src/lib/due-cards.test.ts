import { expect, test } from "vitest";

import type { DueItem } from "@/lib/due";
import { ageWords, dueCard, dueGroups, longDate } from "@/lib/due-cards";

const item = (over: Partial<DueItem>): DueItem => ({
  rule_key: "position.manual_valuation",
  subject_key: "account:1@2026-10",
  importance: "important",
  title: "Your Position uses a Savings balance from June",
  effect: "A current Valuation makes your net position more accurate.",
  due_on: "2026-07-01",
  link: "/accounts/1",
  suits: [],
  actions: [],
  ...over,
});

const TODAY = "2026-11-03";

test("a card carries the item's own title, effect, importance and link, never reworded", () => {
  for (const importance of ["important", "optional"]) {
    const i = item({ importance });
    const card = dueCard(i, TODAY);
    expect(card.title).toBe(i.title);
    expect(card.effect).toBe(i.effect);
    expect(card.importance).toBe(importance);
    expect(card.importanceLabel).toBe(importance === "important" ? "Important" : "Optional");
    expect(card.item.link).toBe(i.link);
  }
});

test("groups by the area the link lands in, with a count, and every item appears once", () => {
  const items = [
    item({}),
    item({ rule_key: "tax.income_statement", subject_key: "m1", link: "/tax/records", title: "Tax one" }),
    item({ rule_key: "position.super_valuation", subject_key: "account:2@x", link: "/accounts/2", importance: "optional" }),
    item({ rule_key: "rule_figures.missing", subject_key: "fy", link: "/rule-figures?fy=2027", title: "Figure" }),
    item({ rule_key: "x", subject_key: "y", link: "/nowhere", title: "Stray" }),
  ];
  const groups = dueGroups(items, TODAY);
  expect(groups.map((g) => [g.area, g.count])).toEqual([
    ["Keep it current", 2],
    ["This tax year", 1],
    ["Reference and system", 1],
    ["Other", 1],
  ]);
  expect(groups[0].tone).toBe(2);
  expect(groups[1].tone).toBe(5);
  expect(groups[2].tone).toBeNull();
  expect(groups.flatMap((g) => g.cards.map((c) => c.id)).sort()).toEqual(
    items.map((i) => `${i.rule_key}|${i.subject_key}`).sort(),
  );
  // Within a group the list's order (important first) is kept.
  expect(groups[0].cards.map((c) => c.importance)).toEqual(["important", "optional"]);
});

test("age reads in plain words and never as lateness", () => {
  expect(longDate("2026-07-01")).toBe("1 July 2026");
  expect(ageWords("2026-11-03", TODAY)).toBe("Due today");
  expect(ageWords("2026-11-02", TODAY)).toBe("Worth refreshing since 2 November 2026 (1 day ago)");
  expect(ageWords("2026-10-06", TODAY)).toBe("Worth refreshing since 6 October 2026 (4 weeks ago)");
  expect(ageWords("2026-07-01", TODAY)).toBe("Worth refreshing since 1 July 2026 (4 months ago)");
  expect(ageWords("2023-11-03", TODAY)).toBe("Worth refreshing since 3 November 2023 (3 years ago)");
  expect(ageWords("2026-11-10", TODAY)).toBe("Comes due on 10 November 2026");
  for (const d of ["2026-11-02", "2026-07-01", "2023-11-03"]) {
    expect(ageWords(d, TODAY)).not.toMatch(/overdue|late|failed/i);
  }
});

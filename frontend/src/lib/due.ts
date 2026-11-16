import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError, type Household } from "@/lib/household";

export type WhatsDue = components["schemas"]["WhatsDueOut"];
export type DueItem = components["schemas"]["DueItemOut"];
export type DueAction = components["schemas"]["DueActionOut"];
export type SnoozedItem = components["schemas"]["SnoozedItemOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export type SnoozeChoice = "week" | "month" | "skip";

export const SNOOZE_CHOICES: { value: SnoozeChoice; label: string }[] = [
  { value: "week", label: "Snooze for one week" },
  { value: "month", label: "Snooze for one month" },
  { value: "skip", label: "Skip this period" },
];

/**
 * An item's link as a router target. A rule may link with a query (`/rule-figures?fy=2027`); the
 * router wants the path and the search apart.
 */
export function linkTarget(link: string): { to: string; search: Record<string, string | number> } {
  const [to, query = ""] = link.split("?");
  // Whole numbers stay numbers, or the router would write them quoted (fy=%222027%22).
  const search = Object.fromEntries(
    [...new URLSearchParams(query)].map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v]),
  );
  return { to, search };
}

export type EffortLevel = "light" | "steady" | "thorough";

/** What each Effort level asks, in a line. It is time against accuracy, never better or worse. */
export const EFFORT_LEVELS: { value: EffortLevel; label: string; description: string }[] = [
  {
    value: "light",
    label: "Light",
    description: "Only the few things that move your numbers most, every few months. Your figures stay roughly right.",
  },
  {
    value: "steady",
    label: "Steady",
    description: "Adds the slower-moving items, a few minutes a month. Your figures are more accurate.",
  },
  {
    value: "thorough",
    label: "Thorough",
    description: "Everything Finance360 can use, as often as it helps. Your figures are the most accurate.",
  },
];

/**
 * The marker a What's due link carries (`/accounts/1?from=due`). It is a plain search param that no
 * destination route has to declare; the app shell reads it to show "Back to What's due".
 */
export const FROM_PARAM = "from";
export const FROM_WHATS_DUE = "due";

export const WHATS_DUE_KEY = ["due"] as const;

export async function fetchWhatsDue(): Promise<WhatsDue> {
  const { data, error, response } = await client.GET("/api/due");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function snoozeDueItem(item: DueItem, snooze: SnoozeChoice): Promise<void> {
  const { error, response } = await client.PUT("/api/due/snoozes", {
    body: { rule_key: item.rule_key, subject_key: item.subject_key, snooze },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

export async function unsnoozeDueItem(snoozeId: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/due/snoozes/{snooze_id}", {
    params: { path: { snooze_id: snoozeId } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** The Household endpoint takes the name with every update, so it is sent back unchanged. */
export async function setEffortLevel(household: Pick<Household, "name">, level: EffortLevel): Promise<Household> {
  const { data, error, response } = await client.PUT("/api/household", {
    body: { name: household.name, effort_level: level },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The sidebar badge: important items only, so what "matters most" is the same at every level. */
export function importantCount(due: Pick<WhatsDue, "items"> | undefined): number {
  return due?.items?.filter((i) => i.importance === "important").length ?? 0;
}

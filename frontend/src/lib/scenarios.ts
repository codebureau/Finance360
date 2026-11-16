import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError, type Member } from "@/lib/household";
import { CATEGORY_KEYS, CATEGORY_LABEL } from "@/lib/projection";

export type Scenario = components["schemas"]["ScenarioOut"];
export type ScenarioProjection = components["schemas"]["ScenarioProjectionOut"];
export type Comparison = components["schemas"]["ComparisonOut"];
export type ComparedScenario = components["schemas"]["ComparedScenarioOut"];
export type Layer = components["schemas"]["LayerOut"];
export type SavedAssumptions = components["schemas"]["AssumptionsSavedOut"];
export type Review = components["schemas"]["ReviewOut"];
export type Provenance = components["schemas"]["ProvenanceOut"];
export type Change = components["schemas"]["ChangeIn"];
export type SpendingLayer = components["schemas"]["SpendingLayerOut"];
export type SpendingSaved = components["schemas"]["SpendingSavedOut"];
export type SpendingPhaseInput = components["schemas"]["SpendingPhaseIn"];
export type SpendingUsed = components["schemas"]["SpendingUsedOut"];
export type SpendingPhaseUsed = components["schemas"]["SpendingPhaseUsedOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchScenarios(): Promise<Scenario[]> {
  const { data, error, response } = await client.GET("/api/scenarios");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addScenario(name: string, copyFrom: number | null): Promise<Scenario> {
  const { data, error, response } = await client.POST("/api/scenarios", {
    body: { name, copy_from: copyFrom },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchLayer(scenarioId: number | null): Promise<Layer> {
  const { data, error, response } = await client.GET("/api/assumptions", {
    params: { query: scenarioId === null ? {} : { scenario_id: scenarioId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function saveAssumptions(
  scenarioId: number | null,
  changes: Change[],
): Promise<SavedAssumptions> {
  const { data, error, response } = await client.PUT("/api/assumptions", {
    body: { scenario_id: scenarioId, changes },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Set a Scenario's whole Drawdown order, or (null) go back to the default order. */
export async function setDrawdownOrder(scenarioId: number, order: string[] | null): Promise<Scenario> {
  const { data, error, response } = await client.PUT("/api/scenarios/{scenario_id}/drawdown-order", {
    params: { path: { scenario_id: scenarioId } },
    body: { order },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** One layer's spending phases: a Scenario's own list, or (null) the Household default list. */
export async function fetchSpending(scenarioId: number | null): Promise<SpendingLayer> {
  const { data, error, response } = await client.GET("/api/spending-phases", {
    params: { query: scenarioId === null ? {} : { scenario_id: scenarioId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Replace one layer's whole list. For a Scenario, null (or empty) goes back to the default list. */
export async function saveSpending(
  scenarioId: number | null,
  phases: SpendingPhaseInput[] | null,
): Promise<SpendingSaved> {
  const { data, error, response } = await client.PUT("/api/spending-phases", {
    body: { scenario_id: scenarioId, phases },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function runScenario(scenarioId: number): Promise<ScenarioProjection> {
  const { data, error, response } = await client.GET("/api/projection/scenarios/{scenario_id}", {
    params: { path: { scenario_id: scenarioId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function compareScenarios(): Promise<Comparison> {
  const { data, error, response } = await client.GET("/api/projection/compare", {});
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** One editable Assumption: where it is stored (key, scope) and how the screen names it. */
export type Slot = {
  /** The form field id, and the field name the server reports errors against. */
  id: string;
  key: string;
  scope_type: "household" | "category" | "member";
  scope_ref: string;
  label: string;
  hint?: string;
  unit: "percent" | "money" | "years" | "choice";
  /** For a `choice` slot: the words it may hold. */
  choices?: string[];
};

export const slotId = (key: string, scopeRef: string) => (scopeRef ? `${key}.${scopeRef}` : key);

/**
 * Keys that were Assumptions when a run was saved and have since been retired. A saved run keeps
 * its stored provenance, so it still renders them. `annual_spending` became spending phases.
 */
export const RETIRED_SLOTS: Record<string, Pick<Slot, "label" | "unit">> = {
  annual_spending: { label: "Annual spending (before spending phases)", unit: "money" },
};

/** Every Assumption a Scenario can state, in the order they are shown. */
export function slotsFor(members: Pick<Member, "id" | "name">[]): Slot[] {
  const slot = (
    key: string,
    scope_type: Slot["scope_type"],
    scope_ref: string,
    label: string,
    unit: Slot["unit"],
    hint?: string,
  ): Slot => ({ id: slotId(key, scope_ref), key, scope_type, scope_ref, label, unit, hint });
  return [
    ...CATEGORY_KEYS.map((c) =>
      slot("return_rate", "category", c, `${CATEGORY_LABEL[c]} return per year (%)`, "percent"),
    ),
    slot("inflation", "household", "", "Inflation per year (%)", "percent"),
    slot("annual_saving", "household", "", "Annual saving ($)", "money", "Until the first Member retires. 0 for none."),
    slot("horizon_age", "household", "", "Horizon age", "years", "The youngest Member's age."),
    slot(
      "real_wage_growth",
      "household",
      "",
      "Real wage growth per year (%)",
      "percent",
      "Pay growth above inflation, for salary Income streams. 0 keeps pay in step with inflation.",
    ),
    {
      ...slot(
        "tax_in_projection",
        "household",
        "",
        "Tax in the projection",
        "choice",
        "On: take-home pay is estimated by the tax engine instead of each Income stream's stated amount. Off when not stated.",
      ),
      choices: ["off", "on"],
    },
    ...members.map((m) => ({
      ...slot(
        "div293_payer",
        "member",
        String(m.id),
        `Division 293 paid by: ${m.name}`,
        "choice",
        "Super: taken from this Member's own super balance a year later. Own: paid from the household's money a year later. Super when not stated.",
      ),
      choices: ["own", "super"],
    })),
    ...members.map((m) =>
      slot("retirement_age", "member", String(m.id), `Retirement age: ${m.name}`, "years"),
    ),
    ...members.map((m) =>
      slot(
        "real_wage_growth",
        "member",
        String(m.id),
        `Real wage growth: ${m.name} (%)`,
        "percent",
        "Leave empty to use the household value.",
      ),
    ),
  ];
}

/** A stored value for display: a percent, a whole number of years, or (rendered as Money) an amount. */
export function shownValue(slot: Pick<Slot, "unit">, value: string): string {
  return slot.unit === "percent" ? `${value}%` : value;
}

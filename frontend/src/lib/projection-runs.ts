import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type RunHeader = components["schemas"]["RunOut"];
export type SavedRun = components["schemas"]["SavedRunOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchRuns(): Promise<RunHeader[]> {
  const { data, error, response } = await client.GET("/api/projection/runs");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchSavedRun(runId: number): Promise<SavedRun> {
  const { data, error, response } = await client.GET("/api/projection/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function saveRun(scenarioId: number, label: string): Promise<RunHeader> {
  const { data, error, response } = await client.POST("/api/projection/runs", {
    body: { scenario_id: scenarioId, label: label.trim() || null },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function rerunRun(runId: number): Promise<RunHeader> {
  const { data, error, response } = await client.POST("/api/projection/runs/{run_id}/rerun", {
    params: { path: { run_id: runId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The headline of a run in one phrase, for lists. */
export function runResult(run: Pick<RunHeader, "lasts_beyond_horizon" | "funds_last_to_age">): string {
  return run.lasts_beyond_horizon ? "Lasts beyond the horizon" : `Funds last to age ${run.funds_last_to_age}`;
}

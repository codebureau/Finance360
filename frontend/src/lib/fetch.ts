import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type FetchStatus = components["schemas"]["FetchStatusOut"];
export type DatasetStatus = components["schemas"]["DatasetStatusOut"];
export type FetchRun = components["schemas"]["FetchRunOut"];
export type FetchRunDetail = components["schemas"]["FetchRunDetailOut"];
export type TargetResult = components["schemas"]["TargetResultOut"];

/** Runs shown on the Reference data page; the detail page holds the rest of a run's story. */
export const RUNS_SHOWN = 10;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchStatus(): Promise<FetchStatus> {
  const { data, error, response } = await client.GET("/api/fetch/status");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchRuns(limit: number = RUNS_SHOWN): Promise<FetchRun[]> {
  const { data, error, response } = await client.GET("/api/fetch/runs", { params: { query: { limit } } });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchRun(id: number): Promise<FetchRunDetail> {
  const { data, error, response } = await client.GET("/api/fetch/runs/{run_id}", {
    params: { path: { run_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** "Fetch now": starts a run per configured dataset and returns at once (202). */
export async function runFetchNow(): Promise<FetchRun[]> {
  const { data, error, response } = await client.POST("/api/fetch/run");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export const DATASET_LABEL: Record<string, string> = { prices: "Prices", fx: "FX rates" };

export const TRIGGER_LABEL: Record<string, string> = {
  scheduled: "Daily",
  "catch-up": "Start-up catch-up",
  manual: "Fetch now",
};

type Variant = "ok" | "info" | "flagged" | "closed";

/** One state, one badge (docs/ui-conventions.md): a run that went wrong is `flagged`. */
export function runBadge(status: string): { variant: Variant; label: string } {
  switch (status) {
    case "success":
      return { variant: "ok", label: "Success" };
    case "partial":
      return { variant: "flagged", label: "Partial" };
    case "failed":
      return { variant: "flagged", label: "Failed" };
    default:
      return { variant: "info", label: "Running" };
  }
}

export function outcomeBadge(outcome: string): { variant: Variant; label: string } {
  if (outcome === "ok") return { variant: "ok", label: "Fetched" };
  if (outcome === "skipped") return { variant: "closed", label: "Skipped" };
  return { variant: "flagged", label: "Error" };
}

/** "12 new Prices, 1 revised" / "Nothing new". Counts only; never a figure. */
export function countsText(run: Pick<FetchRun, "added" | "revised">, noun: string): string {
  const parts = [] as string[];
  if (run.added) parts.push(`${run.added} new ${noun}`);
  if (run.revised) parts.push(`${run.revised} revised`);
  return parts.length ? parts.join(", ") : "Nothing new";
}

export function isRunning(status: FetchStatus | undefined): boolean {
  return !!status?.datasets.some((d) => d.latest?.status === "running");
}

import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type IncomeStream = components["schemas"]["IncomeStreamOut"];
export type IncomeStreamInput = components["schemas"]["IncomeStreamIn"];
export type IncomeUsed = components["schemas"]["IncomeUsedOut"];
export type IncomeStreamUsed = components["schemas"]["IncomeStreamUsedOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchIncomeStreams(): Promise<IncomeStream[]> {
  const { data, error, response } = await client.GET("/api/income-streams");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchIncomeStream(id: number): Promise<IncomeStream> {
  const { data, error, response } = await client.GET("/api/income-streams/{stream_id}", {
    params: { path: { stream_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createIncomeStream(input: IncomeStreamInput): Promise<IncomeStream> {
  const { data, error, response } = await client.POST("/api/income-streams", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateIncomeStream(id: number, input: IncomeStreamInput): Promise<IncomeStream> {
  const { data, error, response } = await client.PUT("/api/income-streams/{stream_id}", {
    params: { path: { stream_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function deleteIncomeStream(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/income-streams/{stream_id}", {
    params: { path: { stream_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** Where a stream starts, in words. Both fields empty means it is paid from now. */
export function startText(s: Pick<IncomeStream, "start_on" | "start_event">): string {
  if (s.start_on) return `From ${s.start_on}`;
  if (s.start_event) return `From ${eventText(s.start_event)}`;
  return "From now";
}

/** Where a stream ends, in words. */
export function endText(s: Pick<IncomeStream, "end_on" | "end_event">): string {
  if (s.end_on) return `Until ${s.end_on}`;
  if (s.end_event) return `Until ${eventText(s.end_event)}`;
  return "To the horizon";
}

const EVENT_TEXT: Record<string, string> = {
  retirement: "retirement",
  preservation_age: "preservation age",
  unrestricted_super_age: "the unrestricted super age",
  age_pension_age: "Age Pension age",
};

const eventText = (key: string) => EVENT_TEXT[key] ?? key;

import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";

export type Household = components["schemas"]["HouseholdOut"];
export type Member = components["schemas"]["MemberOut"];
export type MemberInput = { name: string; date_of_birth: string };

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** A rejected request: `fields` maps a form field to its message; `message` is the general reason. */
export class ApiError extends Error {
  fields: Record<string, string>;
  /** The HTTP status, when the error came from a response (409 means "needs confirmation"). */
  status?: number;
  constructor(message: string, fields: Record<string, string> = {}, status?: number) {
    super(message);
    this.fields = fields;
    this.status = status;
  }
}

/** Fired when any request is refused as signed out, so the auth gate can show the login page. */
export const SIGNED_OUT_EVENT = "finance360:signed-out";

export function toApiError(error: unknown, status: number): ApiError {
  if (status === 401) window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  const body = error as { errors?: { field: string; message: string }[]; message?: string } | undefined;
  if (body?.errors?.length) {
    const fields = Object.fromEntries(body.errors.map((e) => [e.field, e.message]));
    return new ApiError(body.errors.map((e) => e.message).join(" "), fields, status);
  }
  return new ApiError(body?.message ?? `Request failed (${status})`, {}, status);
}

export async function fetchHousehold(): Promise<Household> {
  const { data, error, response } = await client.GET("/api/household");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function renameHousehold(name: string): Promise<Household> {
  const { data, error, response } = await client.PUT("/api/household", { body: { name } });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function setTimeZone(name: string, timeZone: string): Promise<Household> {
  const { data, error, response } = await client.PUT("/api/household", {
    body: { name, time_zone: timeZone },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Today's date (YYYY-MM-DD) in an IANA time zone: the Household's "today", not the browser's. */
export function todayIn(timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export async function addMember(input: MemberInput): Promise<Member> {
  const { data, error, response } = await client.POST("/api/household/members", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateMember(id: number, input: MemberInput): Promise<Member> {
  const { data, error, response } = await client.PUT("/api/household/members/{member_id}", {
    params: { path: { member_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function deleteMember(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/household/members/{member_id}", {
    params: { path: { member_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

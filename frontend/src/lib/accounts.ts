import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Account = components["schemas"]["AccountOut"];
export type AccountInput = components["schemas"]["AccountIn"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchAccounts(includeClosed: boolean): Promise<Account[]> {
  const { data, error, response } = await client.GET("/api/accounts", {
    params: { query: { include_closed: includeClosed } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchAccount(id: number): Promise<Account> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}", {
    params: { path: { account_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchTags(): Promise<string[]> {
  const { data, error, response } = await client.GET("/api/tags");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createAccount(input: AccountInput): Promise<Account> {
  const { data, error, response } = await client.POST("/api/accounts", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateAccount(id: number, input: AccountInput): Promise<Account> {
  const { data, error, response } = await client.PUT("/api/accounts/{account_id}", {
    params: { path: { account_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function closeAccount(
  id: number,
  closedOn: string,
  confirmNonZero = false,
): Promise<Account> {
  const { data, error, response } = await client.POST("/api/accounts/{account_id}/close", {
    params: { path: { account_id: id } },
    body: { closed_on: closedOn, confirm_non_zero: confirmNonZero },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export type DerivedValue = components["schemas"]["DerivedValueOut"];

/** A holdings-based Account's derived value, lines and per-Stream status on a date. */
export async function fetchDerivedValue(
  id: number,
  asAt: string | undefined,
): Promise<DerivedValue> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}/derived-value", {
    params: { path: { account_id: id }, query: asAt ? { as_at: asAt } : {} },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

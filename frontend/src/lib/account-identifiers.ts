import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type AccountIdentifier = components["schemas"]["AccountIdentifierOut"];
export type AccountIdentifierInput = components["schemas"]["AccountIdentifierIn"];

export const KINDS = [
  { key: "bsb_account", label: "BSB and account number" },
  { key: "last4", label: "Last four digits" },
  { key: "member_number", label: "Member number" },
  { key: "policy_number", label: "Policy number" },
  { key: "vendor_account_id", label: "Provider account number" },
] as const;

export const identifierKindLabel = (key: string) => KINDS.find((k) => k.key === key)?.label ?? key;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchAccountIdentifiers(accountId: number): Promise<AccountIdentifier[]> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}/identifiers", {
    params: { path: { account_id: accountId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addAccountIdentifier(
  accountId: number,
  input: AccountIdentifierInput,
): Promise<AccountIdentifier> {
  const { data, error, response } = await client.POST("/api/accounts/{account_id}/identifiers", {
    params: { path: { account_id: accountId } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removeAccountIdentifier(accountId: number, identifierId: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/accounts/{account_id}/identifiers/{identifier_id}", {
    params: { path: { account_id: accountId, identifier_id: identifierId } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** The explicit reveal call: the one way to read the real value. */
export async function revealAccountIdentifier(accountId: number, identifierId: number): Promise<string> {
  const { data, error, response } = await client.POST(
    "/api/accounts/{account_id}/identifiers/{identifier_id}/reveal",
    { params: { path: { account_id: accountId, identifier_id: identifierId } } },
  );
  if (!data) throw toApiError(error, response.status);
  return data.value;
}

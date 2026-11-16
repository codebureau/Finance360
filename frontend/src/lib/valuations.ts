import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Valuation = components["schemas"]["ValuationOut"];
export type ValuationInput = components["schemas"]["ValuationIn"];

/** The sources a person can enter. "derived" Valuations are calculated, never typed in. */
export const ENTERED_SOURCES = ["manual", "statement", "estimate"] as const;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchValuations(accountId: number): Promise<Valuation[]> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}/valuations", {
    params: { path: { account_id: accountId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addValuation(accountId: number, input: ValuationInput): Promise<Valuation> {
  const { data, error, response } = await client.POST("/api/accounts/{account_id}/valuations", {
    params: { path: { account_id: accountId } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function correctValuation(id: number, input: ValuationInput): Promise<Valuation> {
  const { data, error, response } = await client.PUT("/api/valuations/{valuation_id}", {
    params: { path: { valuation_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removeValuation(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/valuations/{valuation_id}", {
    params: { path: { valuation_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** "1234567.5" -> "1,234,567.5". String work only: amounts are never turned into floats. */
export function groupDigits(amount: string): string {
  const negative = amount.startsWith("-");
  const [whole, frac] = amount.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${frac !== undefined ? `.${frac}` : ""}`;
}

export function describeAmount(v: Pick<Valuation, "amount" | "currency">): string {
  return `${v.currency} ${groupDigits(v.amount)}`;
}

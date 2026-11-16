import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Price = components["schemas"]["PriceOut"];
export type PriceInput = components["schemas"]["PriceIn"];
export type PriceLookup = components["schemas"]["PriceLookupOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchPrices(instrumentId: number): Promise<Price[]> {
  const { data, error, response } = await client.GET("/api/instruments/{instrument_id}/prices", {
    params: { path: { instrument_id: instrumentId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addPrice(instrumentId: number, input: PriceInput): Promise<Price> {
  const { data, error, response } = await client.POST("/api/instruments/{instrument_id}/prices", {
    params: { path: { instrument_id: instrumentId } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function correctPrice(
  instrumentId: number,
  priceId: number,
  input: PriceInput,
): Promise<Price> {
  const { data, error, response } = await client.PUT(
    "/api/instruments/{instrument_id}/prices/{price_id}",
    { params: { path: { instrument_id: instrumentId, price_id: priceId } }, body: input },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removePrice(instrumentId: number, priceId: number): Promise<void> {
  const { error, response } = await client.DELETE(
    "/api/instruments/{instrument_id}/prices/{price_id}",
    { params: { path: { instrument_id: instrumentId, price_id: priceId } } },
  );
  if (!response.ok) throw toApiError(error, response.status);
}

export async function priceOn(instrumentId: number, on: string): Promise<PriceLookup> {
  const { data, error, response } = await client.GET("/api/instruments/{instrument_id}/price", {
    params: { path: { instrument_id: instrumentId }, query: { on } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

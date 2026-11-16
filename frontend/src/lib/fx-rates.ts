import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type FxRate = components["schemas"]["FxRateOut"];
export type FxRateInput = components["schemas"]["FxRateIn"];
export type Conversion = components["schemas"]["ConversionOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchFxRates(): Promise<FxRate[]> {
  const { data, error, response } = await client.GET("/api/fx-rates");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addFxRate(input: FxRateInput): Promise<FxRate> {
  const { data, error, response } = await client.POST("/api/fx-rates", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function correctFxRate(id: number, input: FxRateInput): Promise<FxRate> {
  const { data, error, response } = await client.PUT("/api/fx-rates/{fx_rate_id}", {
    params: { path: { fx_rate_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removeFxRate(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/fx-rates/{fx_rate_id}", {
    params: { path: { fx_rate_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

export async function convertOn(
  amount: string,
  currency: string,
  on: string,
): Promise<Conversion> {
  const { data, error, response } = await client.GET("/api/fx-rates/convert", {
    params: { query: { amount, currency, on } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Instrument = components["schemas"]["InstrumentOut"];
export type InstrumentIdentifier = components["schemas"]["IdentifierOut"];
export type InstrumentInput = components["schemas"]["InstrumentIn"];
export type InstrumentCreate = components["schemas"]["InstrumentCreate"];
export type IdentifierInput = components["schemas"]["IdentifierIn"];

export const KINDS = [
  { key: "etf", label: "ETF" },
  { key: "share", label: "Share" },
  { key: "fund", label: "Fund" },
] as const;

export const SCHEMES = [
  { key: "ticker", label: "Ticker" },
  { key: "yahoo", label: "Yahoo symbol" },
  { key: "isin", label: "ISIN" },
  { key: "vendor_id", label: "Vendor ID" },
  { key: "name", label: "Name alias" },
] as const;

export const kindLabel = (key: string) => KINDS.find((k) => k.key === key)?.label ?? key;
export const schemeLabel = (key: string) => SCHEMES.find((s) => s.key === key)?.label ?? key;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchInstruments(): Promise<Instrument[]> {
  const { data, error, response } = await client.GET("/api/instruments");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createInstrument(input: InstrumentCreate): Promise<Instrument> {
  const { data, error, response } = await client.POST("/api/instruments", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateInstrument(id: number, input: InstrumentInput): Promise<Instrument> {
  const { data, error, response } = await client.PUT("/api/instruments/{instrument_id}", {
    params: { path: { instrument_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addIdentifier(id: number, input: IdentifierInput): Promise<Instrument> {
  const { data, error, response } = await client.POST("/api/instruments/{instrument_id}/identifiers", {
    params: { path: { instrument_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removeIdentifier(id: number, identifierId: number): Promise<Instrument> {
  const { data, error, response } = await client.DELETE(
    "/api/instruments/{instrument_id}/identifiers/{identifier_id}",
    { params: { path: { instrument_id: id, identifier_id: identifierId } } },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

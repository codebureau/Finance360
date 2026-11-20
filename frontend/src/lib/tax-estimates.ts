import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";
import { estimateBody, type TaxRequest } from "@/lib/tax";

export type TaxWhatIf = components["schemas"]["TaxWhatIfOut"];
export type TaxWhatIfInput = components["schemas"]["TaxWhatIfIn"];
export type TaxOverrideInput = components["schemas"]["TaxOverrideIn"];
export type TaxOverride = components["schemas"]["TaxOverrideOut"];
export type TaxEstimateHeader = components["schemas"]["TaxEstimateHeaderOut"];
export type TaxEstimateView = components["schemas"]["TaxEstimateViewOut"];
export type TaxSavedEstimate = components["schemas"]["TaxSavedEstimateOut"];
export type TaxComparison = components["schemas"]["TaxComparisonOut"];
export type TaxValue = components["schemas"]["TaxValueOut"];
export type TaxTotal = components["schemas"]["TaxTotalOut"];
export type TaxPrefill = components["schemas"]["TaxPrefillOut"];
export type TaxAppliedOverride = components["schemas"]["TaxAppliedOverrideOut"];
export type TaxBreakEven = components["schemas"]["TaxBreakEvenOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** What is calculated: the base (no what-if) or a Tax what-if of the same year. */
export type EstimateRequest = TaxRequest & { whatifId: number | null };

function runBody(request: EstimateRequest) {
  return { ...estimateBody(request), whatif_id: request.whatifId };
}

export async function fetchWhatIfs(financialYear?: number): Promise<TaxWhatIf[]> {
  const { data, error, response } = await client.GET("/api/tax/whatifs", {
    params: { query: { financial_year: financialYear } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchWhatIf(id: number): Promise<TaxWhatIf> {
  const { data, error, response } = await client.GET("/api/tax/whatifs/{whatif_id}", {
    params: { path: { whatif_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createWhatIf(input: TaxWhatIfInput): Promise<TaxWhatIf> {
  const { data, error, response } = await client.POST("/api/tax/whatifs", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function updateWhatIf(id: number, input: TaxWhatIfInput): Promise<TaxWhatIf> {
  const { data, error, response } = await client.PUT("/api/tax/whatifs/{whatif_id}", {
    params: { path: { whatif_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function deleteWhatIf(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/tax/whatifs/{whatif_id}", {
    params: { path: { whatif_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** The what-if as the form that edits it sends it back (the whole list of overrides). */
export function whatIfInput(whatif: TaxWhatIf, overrides: TaxOverrideInput[]): TaxWhatIfInput {
  return {
    name: whatif.name,
    financial_year: whatif.financial_year,
    notes: whatif.notes,
    overrides,
  };
}

/** An override read back, as the request that would state it again. */
export function overrideInput(o: TaxOverride): TaxOverrideInput {
  return {
    op: o.op,
    member_id: o.member_id,
    label: o.label_key,
    amount: o.value?.amount ?? null,
    days: o.value?.days ?? null,
    flag: o.value?.flag ?? null,
    record_id: o.record_id,
    record: o.record,
  };
}

export async function previewEstimate(request: EstimateRequest): Promise<TaxEstimateView> {
  const { data, error, response } = await client.POST("/api/tax/estimates/preview", {
    body: runBody(request),
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** At what amount of one Member's total for one label is the household net outcome at least the base case? Saves nothing. */
export async function searchBreakEven(
  request: EstimateRequest,
  memberId: number,
  label: string,
): Promise<TaxBreakEven> {
  const { data, error, response } = await client.POST("/api/tax/whatifs/break-even", {
    body: { ...runBody(request), member_id: memberId, label },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function saveEstimate(request: EstimateRequest, label: string): Promise<TaxEstimateHeader> {
  const { data, error, response } = await client.POST("/api/tax/estimates", {
    body: { ...runBody(request), label: label.trim() || null },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchEstimates(financialYear?: number): Promise<TaxEstimateHeader[]> {
  const { data, error, response } = await client.GET("/api/tax/estimates", {
    params: { query: { financial_year: financialYear } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchSavedEstimate(id: number): Promise<TaxSavedEstimate> {
  const { data, error, response } = await client.GET("/api/tax/estimates/{estimate_id}", {
    params: { path: { estimate_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function rerunEstimate(id: number): Promise<TaxEstimateHeader> {
  const { data, error, response } = await client.POST("/api/tax/estimates/{estimate_id}/rerun", {
    params: { path: { estimate_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function compareEstimates(a: number, b: number): Promise<TaxComparison> {
  const { data, error, response } = await client.GET("/api/tax/estimates/compare", {
    params: { query: { a, b } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** `/tax/whatifs?fy=2026` and `/tax/estimates?fy=2026`: the year is a URL search param. */
export function parseYearSearch(search: Record<string, unknown>): { fy?: number } {
  const fy = Number(search.fy);
  return Number.isInteger(fy) && fy >= 1990 && fy <= 2200 ? { fy } : {};
}

/** `/tax/estimates/compare?a=1&b=2`: the two estimates are URL search params. */
export function parseCompareSearch(search: Record<string, unknown>): { a?: number; b?: number } {
  const a = Number(search.a);
  const b = Number(search.b);
  return {
    ...(Number.isInteger(a) && a > 0 ? { a } : {}),
    ...(Number.isInteger(b) && b > 0 ? { b } : {}),
  };
}

/** A Label total, flag or days in words, or an amount as its exact text (shown with `<Money>`). */
export function valueWords(value: TaxValue, kind: string): string | null {
  if (kind === "flag") return value.flag ? "Yes" : "No";
  if (kind === "days") return `${value.days ?? 0} ${value.days === 1 ? "day" : "days"}`;
  return null;
}

/** The ways to state an override, in the order the picker offers them. */
export const OVERRIDE_OPS = [
  { key: "set", label: "State a total" },
  { key: "exclude_label", label: "Leave out a total" },
  { key: "exclude_record", label: "Leave out a record" },
  { key: "add_record", label: "Add a hypothetical record" },
] as const;

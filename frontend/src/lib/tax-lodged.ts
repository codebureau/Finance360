import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type LodgedResult = components["schemas"]["LodgedResultOut"];
export type LodgedResultInput = components["schemas"]["LodgedResultIn"];
export type ReturnWorksheet = components["schemas"]["WorksheetOut"];
export type WorksheetLine = components["schemas"]["WorksheetLineOut"];

/** The Link kind a notice's documents use (ADR-0026). */
export const LODGED_KIND = "tax_lodged_result";

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchWorksheet(
  memberId: number,
  financialYear: number,
  dependants: number,
): Promise<ReturnWorksheet> {
  const { data, error, response } = await client.GET("/api/tax/lodged-results/worksheet", {
    params: { query: { member_id: memberId, financial_year: financialYear, dependants } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function enterLodgedResult(input: LodgedResultInput): Promise<LodgedResult> {
  const { data, error, response } = await client.POST("/api/tax/lodged-results", { body: input });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** An amended notice: supersedes the current one, which is kept. */
export async function amendLodgedResult(id: number, input: LodgedResultInput): Promise<LodgedResult> {
  const { data, error, response } = await client.PUT("/api/tax/lodged-results/{lodged_id}", {
    params: { path: { lodged_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** `/tax/lodged?member=1&fy=2026`: the Member and the financial year are URL search params. */
export function parseLodgedSearch(search: Record<string, unknown>): { member?: number; fy?: number } {
  const member = Number(search.member);
  const fy = Number(search.fy);
  return {
    ...(Number.isInteger(member) && member > 0 ? { member } : {}),
    ...(Number.isInteger(fy) && fy >= 1990 && fy <= 2200 ? { fy } : {}),
  };
}

/** The badge text for a worksheet line's status. */
export const STATUS_TEXT: Record<string, string> = {
  matched: "Matched",
  rounding: "Within rounding",
  differs: "Differs",
};

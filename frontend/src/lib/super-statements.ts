import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type SuperStatement = components["schemas"]["SuperStatementOut"];
export type SuperStatementInput = components["schemas"]["SuperStatementIn"];
export type SuperCheck = components["schemas"]["SuperCheckOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** The Link kinds (ADR-0026): the keys the Library stores for documents on these records. */
export const SUPER_STATEMENT_KIND = "super_statement";
export const ACCOUNT_KIND = "account";

/** The account type a Super statement belongs to (ADR-0025). */
export const SUPER_ACCOUNT_TYPE = "super_fund_account";

/** The balance roll, in the order the fund prints it. All required. */
export const ROLL_FIELDS = [
  { key: "opening", label: "Opening balance" },
  { key: "contributions_in", label: "Contributions in" },
  { key: "investment_returns", label: "Investment returns" },
  { key: "withdrawals", label: "Withdrawals (including tax, premiums and fees)" },
  { key: "closing", label: "Closing balance" },
] as const;

/** The optional outflow breakdown. "Fees inside the investment" is stated but not part of the sum. */
export const OUTFLOW_FIELDS = [
  { key: "contributions_tax", label: "Contributions tax" },
  { key: "fees_before_rebate", label: "Fees before the tax rebate" },
  { key: "rebate", label: "Tax rebate on fees" },
  { key: "fees_inside_investment", label: "Fees taken inside the investment" },
  { key: "division_293_released", label: "Division 293 released" },
  { key: "other_withdrawals", label: "Other withdrawals" },
  { key: "insurance_premiums", label: "Insurance premiums" },
] as const;

export const RECEIVED_FIELDS = [
  { key: "concessional_received", label: "Concessional contributions received" },
  { key: "non_concessional_received", label: "Non-concessional contributions received" },
] as const;

export const CHECK_LABELS: Record<string, string> = {
  roll: "Balance roll",
  outflows: "Outflow breakdown",
  options: "Investment options",
};

export async function fetchSuperStatements(accountId: number): Promise<SuperStatement[]> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}/super-statements", {
    params: { path: { account_id: accountId } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** One statement as it stands now (an id replaced by a correction returns its successor). */
export async function fetchSuperStatement(id: number): Promise<SuperStatement> {
  const { data, error, response } = await client.GET("/api/super-statements/{statement_id}", {
    params: { path: { statement_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function checkSuperStatement(
  accountId: number,
  input: SuperStatementInput,
): Promise<SuperCheck[]> {
  const { data, error, response } = await client.POST(
    "/api/accounts/{account_id}/super-statements/checks",
    { params: { path: { account_id: accountId } }, body: input },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addSuperStatement(
  accountId: number,
  input: SuperStatementInput,
): Promise<SuperStatement> {
  const { data, error, response } = await client.POST("/api/accounts/{account_id}/super-statements", {
    params: { path: { account_id: accountId } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function correctSuperStatement(
  id: number,
  input: SuperStatementInput,
): Promise<SuperStatement> {
  const { data, error, response } = await client.PUT("/api/super-statements/{statement_id}", {
    params: { path: { statement_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function removeSuperStatement(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/super-statements/{statement_id}", {
    params: { path: { statement_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

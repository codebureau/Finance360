import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type ImportSummary = components["schemas"]["ImportOut"];
export type ImportDetail = components["schemas"]["ImportDetailOut"];
export type UnmappedType = components["schemas"]["UnmappedTypeOut"];
export type StageResult = components["schemas"]["StageOut"];
export type Detection = components["schemas"]["DetectOut"];
export type StagedRow = components["schemas"]["StagedRowOut"];
export type RowsPage = components["schemas"]["RowsOut"];
export type UnresolvedReference = components["schemas"]["UnresolvedOut"];
export type ImportDiff = components["schemas"]["DiffOut"];
export type RemovedPage = components["schemas"]["TransactionsOut"];

/** Rows per page on the Import detail page. */
export const ROWS_PER_PAGE = 25;
/** Removed Transactions per page in the diff. */
export const REMOVED_PER_PAGE = 10;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** The file travels as the raw request body (see the API's `FILE_BODY`). */
const OCTET = { "Content-Type": "application/octet-stream" };
const asBody = (file: File) => file as unknown as string;
const rawBody = (body: unknown) => body as BodyInit;

export async function detectImport(file: File): Promise<Detection> {
  const { data, error, response } = await client.POST("/api/imports/detect", {
    body: asBody(file),
    bodySerializer: rawBody,
    headers: OCTET,
    params: { query: { file_name: file.name } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function stageImport(
  file: File,
  accountId: number,
  saveIdentifier: boolean,
): Promise<StageResult> {
  const { data, error, response } = await client.POST("/api/imports", {
    params: {
      query: { account_id: accountId, file_name: file.name, save_identifier: saveIdentifier },
    },
    body: asBody(file),
    bodySerializer: rawBody,
    headers: OCTET,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchImports(): Promise<ImportSummary[]> {
  const { data, error, response } = await client.GET("/api/imports");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchImport(id: number): Promise<ImportDetail> {
  const { data, error, response } = await client.GET("/api/imports/{import_id}", {
    params: { path: { import_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchImportRows(id: number, page: number): Promise<RowsPage> {
  const { data, error, response } = await client.GET("/api/imports/{import_id}/rows", {
    params: {
      path: { import_id: id },
      query: { offset: (page - 1) * ROWS_PER_PAGE, limit: ROWS_PER_PAGE },
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function discardImport(id: number): Promise<ImportDetail> {
  const { data, error, response } = await client.POST("/api/imports/{import_id}/discard", {
    params: { path: { import_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchImportDiff(id: number): Promise<ImportDiff> {
  const { data, error, response } = await client.GET("/api/imports/{import_id}/diff", {
    params: { path: { import_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The current Transactions committing would supersede, a page at a time. */
export async function fetchRemoved(id: number, page: number): Promise<RemovedPage> {
  const { data, error, response } = await client.GET("/api/imports/{import_id}/diff/removed", {
    params: {
      path: { import_id: id },
      query: { offset: (page - 1) * REMOVED_PER_PAGE, limit: REMOVED_PER_PAGE },
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function commitImport(id: number): Promise<ImportDetail> {
  const { data, error, response } = await client.POST("/api/imports/{import_id}/commit", {
    params: { path: { import_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function mapReference(
  id: number,
  ref: { scheme: string; ref: string },
  instrumentId: number | null,
): Promise<ImportDetail> {
  const { data, error, response } = await client.POST("/api/imports/{import_id}/references/map", {
    params: { path: { import_id: id } },
    body: { ...ref, instrument_id: instrumentId },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function mapImportType(id: number, rawType: string, kind: string): Promise<ImportDetail> {
  const { data, error, response } = await client.POST("/api/imports/{import_id}/types/map", {
    params: { path: { import_id: id } },
    body: { raw_type: rawType, kind },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function createReferenceInstrument(
  id: number,
  input: { scheme: string; ref: string; name: string; kind: string; currency: string; notes?: string | null },
): Promise<ImportDetail> {
  const { data, error, response } = await client.POST("/api/imports/{import_id}/references/create", {
    params: { path: { import_id: id } },
    body: input,
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export const STREAM_LABELS: Record<string, string> = { trade: "Trade", cash: "Cash" };
export const streamLabel = (stream: string) => STREAM_LABELS[stream] ?? stream;

export const KIND_LABELS: Record<string, string> = {
  buy: "Buy",
  sell: "Sell",
  opening_balance: "Opening balance",
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  trade_settlement: "Trade settlement",
  distribution: "Distribution",
  interest: "Interest",
  fee: "Fee",
  unrecognised: "Unrecognised type",
  ignored: "Ignored",
};
export const rowKindLabel = (kind: string) => KIND_LABELS[kind] ?? kind;

/** "03 Aug 2026" from an ISO date, with no time-zone shift. */
export function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
  return `${String(d).padStart(2, "0")} ${month} ${y}`;
}

export const coverageText = (from: string, to: string) =>
  from === to ? formatDate(from) : `${formatDate(from)} to ${formatDate(to)}`;

/** Status badge variant: the same state shows the same badge everywhere. */
export function importBadge(imp: Pick<ImportSummary, "status" | "commit_blocked">): {
  variant: "ok" | "info" | "flagged" | "closed";
  label: string;
} {
  if (imp.status === "committed") return { variant: "ok", label: "Committed" };
  if (imp.status === "discarded") return { variant: "closed", label: "Discarded" };
  return imp.commit_blocked
    ? { variant: "flagged", label: "Needs matching" }
    : { variant: "info", label: "Staged" };
}

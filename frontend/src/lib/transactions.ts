import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Transaction = components["schemas"]["TransactionOut"];
export type AccountTransactions =
  components["schemas"]["AccountTransactionsOut"];

export type Holdings = components["schemas"]["HoldingsOut"];
export type OpeningPositionInput = components["schemas"]["OpeningPositionIn"];

export type Stream = "trade" | "cash";

/** Rows per page in a Transactions list. */
export const TRANSACTIONS_PER_PAGE = 10;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchTransactions(
  accountId: number,
  stream: Stream,
  page: number,
  includeSuperseded: boolean,
): Promise<AccountTransactions> {
  const { data, error, response } = await client.GET(
    "/api/accounts/{account_id}/transactions",
    {
      params: {
        path: { account_id: accountId },
        query: {
          stream,
          include_superseded: includeSuperseded,
          offset: (page - 1) * TRANSACTIONS_PER_PAGE,
          limit: TRANSACTIONS_PER_PAGE,
        },
      },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchHoldings(
  accountId: number,
  asAt: string | undefined,
): Promise<Holdings> {
  const { data, error, response } = await client.GET(
    "/api/accounts/{account_id}/holdings",
    {
      params: {
        path: { account_id: accountId },
        query: asAt ? { as_at: asAt } : {},
      },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function addOpeningPosition(
  accountId: number,
  input: OpeningPositionInput,
): Promise<Transaction> {
  const { data, error, response } = await client.POST(
    "/api/accounts/{account_id}/opening-positions",
    {
      params: { path: { account_id: accountId } },
      body: input,
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export const STREAM_TABS: { key: Stream; label: string }[] = [
  { key: "trade", label: "Trades" },
  { key: "cash", label: "Cash" },
];

/** The Account page's Transactions section state, kept in the URL so views are linkable. */
export type TransactionsSearch = {
  stream?: Stream;
  txpage?: number;
  superseded?: true;
  /** The Holdings as-at date (`YYYY-MM-DD`); absent means the latest. */
  asat?: string;
  /** The look-through Dimension (a key from the Exposure vocabulary); absent means asset class. */
  dimension?: string;
};

export function parseTransactionsSearch(
  search: Record<string, unknown>,
): TransactionsSearch {
  const page = Number(search.txpage);
  return {
    stream:
      search.stream === "cash" || search.stream === "trade"
        ? search.stream
        : undefined,
    txpage: Number.isInteger(page) && page > 1 ? page : undefined,
    superseded:
      search.superseded === true || search.superseded === "true"
        ? true
        : undefined,
    asat:
      typeof search.asat === "string" && /^\d{4}-\d{2}-\d{2}$/.test(search.asat)
        ? search.asat
        : undefined,
    dimension:
      typeof search.dimension === "string" && /^[a-z_]{1,30}$/.test(search.dimension)
        ? search.dimension
        : undefined,
  };
}

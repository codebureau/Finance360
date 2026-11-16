import { useQuery } from "@tanstack/react-query";

import { Pager, TransactionsTable } from "@/components/transactions-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchTransactions,
  STREAM_TABS,
  TRANSACTIONS_PER_PAGE,
  type Stream,
  type TransactionsSearch,
} from "@/lib/transactions";

type Account = { id: number; name: string };

/**
 * An Account's Transactions, one Stream at a time (Trades or Cash), from committed Imports. The
 * section stays out of the way of Accounts that have none. The Stream, page and "show superseded"
 * are search params, so a view is linkable and survives a refresh.
 */
export function TransactionsPanel({
  account,
  search,
  onSearch,
}: {
  account: Account;
  search: TransactionsSearch;
  onSearch: (patch: Partial<TransactionsSearch>) => void;
}) {
  const page = search.txpage ?? 1;
  const superseded = !!search.superseded;
  const overview = useQuery({
    queryKey: ["transactions", account.id, "overview"],
    queryFn: () => fetchTransactions(account.id, "trade", 1, false),
  });
  // Land on the Stream that has rows when the owner has not chosen one.
  const fallback: Stream =
    overview.data && overview.data.current_trade === 0 && overview.data.current_cash > 0 ? "cash" : "trade";
  const stream = search.stream ?? fallback;
  const list = useQuery({
    queryKey: ["transactions", account.id, stream, page, superseded],
    queryFn: () => fetchTransactions(account.id, stream, page, superseded),
    enabled: overview.isSuccess,
  });

  if (!overview.data) return null;
  const { current_trade, current_cash } = overview.data;
  if (current_trade + current_cash === 0 && !list.data?.total) return null;
  const counts: Record<Stream, number> = { trade: current_trade, cash: current_cash };

  return (
    <Card aria-label="Transactions">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Transactions</CardTitle>
        <div className="flex gap-2" role="group" aria-label="Stream">
          {STREAM_TABS.map((tab) => (
            <Button
              key={tab.key}
              type="button"
              variant="outline"
              aria-pressed={stream === tab.key}
              className={stream === tab.key ? "bg-accent" : ""}
              onClick={() => onSearch({ stream: tab.key, txpage: undefined })}
            >
              {tab.label} ({counts[tab.key]})
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <label className="flex w-fit items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={superseded}
            onChange={(e) => onSearch({ superseded: e.target.checked ? true : undefined, txpage: undefined })}
          />
          Show superseded rows
        </label>
        {list.error && <p className="text-destructive">Could not load the Transactions: {list.error.message}</p>}
        {list.data && list.data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No {stream === "trade" ? "trade" : "cash"} Transactions yet.
          </p>
        )}
        {list.data && list.data.items.length > 0 && (
          <>
            <TransactionsTable
              items={list.data.items}
              stream={stream}
              label={stream === "trade" ? "Trade Transactions" : "Cash Transactions"}
            />
            <Pager
              total={list.data.total}
              perPage={TRANSACTIONS_PER_PAGE}
              page={page}
              noun="Transaction"
              onPage={(next) => onSearch({ txpage: next > 1 ? next : undefined })}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

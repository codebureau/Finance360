import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { rowKindLabel } from "@/lib/imports";
import type { Stream, Transaction } from "@/lib/transactions";

const dash = <span className="text-muted-foreground">—</span>;

/**
 * Transactions of one Stream, newest first. Trade rows show units, unit price and the brokerage fee
 * (informational: it never moves cash); cash rows show the signed cash effect. At phone width the
 * secondary columns fold under the date and instrument so nothing runs together or scrolls.
 */
export function TransactionsTable({
  items,
  stream,
  label,
}: {
  items: Transaction[];
  stream: Stream;
  label: string;
}) {
  const trade = stream === "trade";
  return (
    <Table aria-label={label}>
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead>
          <TableHead className="hidden md:table-cell">Kind</TableHead>
          <TableHead>Instrument</TableHead>
          {trade && (
            <>
              <TableHead className="hidden text-right md:table-cell">Units</TableHead>
              <TableHead className="hidden text-right md:table-cell">Unit price</TableHead>
              <TableHead className="hidden text-right md:table-cell">Fee</TableHead>
            </>
          )}
          {!trade && <TableHead className="text-right">Amount</TableHead>}
          <TableHead className="hidden lg:table-cell">From</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((t) => (
          <TableRow key={t.id} data-superseded={t.superseded_at ? "true" : undefined}>
            <TableCell className="whitespace-nowrap">
              {t.occurred_on}
              <p className="text-xs text-muted-foreground md:hidden">{rowKindLabel(t.kind)}</p>
              {t.superseded_at && (
                <p className="md:hidden">
                  <Badge variant="superseded">Superseded</Badge>
                </p>
              )}
            </TableCell>
            <TableCell className="hidden whitespace-nowrap md:table-cell">
              {rowKindLabel(t.kind)}{" "}
              {t.superseded_at && <Badge variant="superseded">Superseded</Badge>}
            </TableCell>
            <TableCell>
              {t.instrument_name ? <span className="break-words">{t.instrument_name}</span> : dash}
              {trade && t.quantity && (
                <p className="font-mono text-xs text-muted-foreground tabular-nums md:hidden">
                  {t.quantity}
                  {t.unit_price ? ` @ ${t.unit_price}` : ""}
                  {t.fee && (
                    <>
                      {" "}
                      · fee <Money amount={t.fee.amount} currency={t.fee.currency} />
                    </>
                  )}
                </p>
              )}
            </TableCell>
            {trade && (
              <>
                <TableCell className="hidden text-right font-mono tabular-nums md:table-cell">
                  {t.quantity ?? dash}
                </TableCell>
                <TableCell className="hidden text-right font-mono tabular-nums md:table-cell">
                  {t.unit_price ?? dash}
                </TableCell>
                <TableCell className="hidden text-right md:table-cell">
                  {t.fee ? <Money amount={t.fee.amount} currency={t.fee.currency} /> : dash}
                </TableCell>
              </>
            )}
            {!trade && (
              <TableCell className="text-right">
                {t.amount ? <Money amount={t.amount.amount} currency={t.amount.currency} /> : dash}
              </TableCell>
            )}
            <TableCell className="hidden break-words text-xs text-muted-foreground lg:table-cell">
              {t.file_name ?? "Entered by hand"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** "14 rows · page 2 of 3" with Previous and Next, shown only when there is more than one page. */
export function Pager({
  total,
  perPage,
  page,
  noun,
  onPage,
}: {
  total: number;
  perPage: number;
  page: number;
  noun: string;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
      <span>
        {total} {total === 1 ? noun : `${noun}s`} · page {page} of {pages}
      </span>
      {pages > 1 && (
        <div className="flex gap-2">
          <Button variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Previous page
          </Button>
          <Button variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
            Next page
          </Button>
        </div>
      )}
    </div>
  );
}

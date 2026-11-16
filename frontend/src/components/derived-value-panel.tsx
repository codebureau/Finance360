import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchDerivedValue } from "@/lib/accounts";

type Account = { id: number; name: string; currency: string };

/**
 * A holdings-based Account's value derived from its Holdings, Prices and FX (ADR-0013 section 3),
 * with each line's inputs, the status of each Stream and the reconciliation against the best
 * recorded Valuation. Shown only when the Account's Behaviour derives. The as-at date shares the
 * Holdings panel's search param, so a view is linkable; without one it is the latest date a value
 * can exist for. A stale Price is flagged, not refused; a missing input says what is missing.
 */
export function DerivedValuePanel({
  account,
  asAt,
  onAsAt,
}: {
  account: Account;
  asAt: string | undefined;
  onAsAt: (asAt: string | undefined) => void;
}) {
  const derived = useQuery({
    queryKey: ["derived-value", account.id, asAt ?? "latest"],
    queryFn: () => fetchDerivedValue(account.id, asAt),
    placeholderData: keepPreviousData,
  });
  const data = derived.data;
  if (derived.error) {
    return (
      <p className="text-sm text-muted-foreground">
        Could not load the derived value: {derived.error.message}
      </p>
    );
  }
  if (!data?.applies) return null;

  return (
    <Card aria-label="Derived value" role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Derived value</CardTitle>
        <label className="flex items-center gap-2 text-sm">
          As at
          <input
            aria-label="Derived value as at"
            className={inputClass}
            type="date"
            value={asAt ?? data.as_at}
            onChange={(e) => onAsAt(e.target.value || undefined)}
          />
        </label>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ul className="flex flex-col gap-1 text-sm" aria-label="Stream status">
          {data.streams.map((s) => (
            <li key={s.stream} className="flex flex-wrap items-center gap-2">
              <span className="capitalize">{s.stream} Stream</span>
              {s.status === "ok" && (
                <Badge variant="ok">Coverage to {s.coverage_to}</Badge>
              )}
              {s.status === "stale" && (
                <Badge variant="stale">
                  Stale: Coverage ends {s.coverage_to}
                </Badge>
              )}
              {s.status === "missing" && (
                <Badge variant="flagged">Missing: nothing imported</Badge>
              )}
            </li>
          ))}
        </ul>

        {data.total ? (
          <div className="flex flex-wrap items-baseline gap-2">
            <Money
              amount={data.total.amount}
              currency={data.total.currency}
              className="text-2xl font-semibold"
            />
            <span className="text-sm text-muted-foreground">
              as at {data.as_at}
            </span>
            {data.stale && (
              <Badge variant="stale">
                Stale Price (over {data.stale_after_days} days old)
              </Badge>
            )}
          </div>
        ) : (
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm break-words">
            No derived value as at {data.as_at}. {data.reason}
          </p>
        )}

        {data.lines.length > 0 && (
          <Table aria-label="Derived value lines">
            <TableHeader>
              <TableRow>
                <TableHead>Line</TableHead>
                <TableHead className="text-right">Units</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">FX rate</TableHead>
                <TableHead className="text-right">Value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.lines.map((line) => (
                <TableRow
                  key={`${line.kind}-${line.instrument_id ?? line.currency}`}
                >
                  <TableCell className="break-words">
                    {line.kind === "holding"
                      ? line.instrument_name
                      : `Cash (${line.currency})`}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {line.units ?? ""}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {line.price && (
                      <>
                        {line.price} {line.currency}
                        <span className="block text-xs text-muted-foreground">
                          as at {line.price_as_at} ({line.price_source})
                          {line.stale && (
                            <Badge variant="stale" className="ml-1">
                              Stale
                            </Badge>
                          )}
                        </span>
                      </>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {line.rate && (
                      <>
                        {line.rate}
                        <span className="block text-xs text-muted-foreground">
                          as at {line.rate_as_at} ({line.rate_source})
                        </span>
                      </>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Money
                      amount={line.value.amount}
                      currency={line.value.currency}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {data.observed && (
          <p
            className="text-sm text-muted-foreground"
            aria-label="Reconciliation"
          >
            Recorded {data.observed.source} Valuation as at{" "}
            {data.observed.as_at}:{" "}
            <Money
              amount={data.observed.amount.amount}
              currency={data.observed.amount.currency}
            />
            {data.difference && (
              <>
                . Difference (recorded minus derived):{" "}
                <Money
                  amount={data.difference.amount}
                  currency={data.difference.currency}
                />
              </>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchLookThrough, type LookThrough, type LookThroughLine } from "@/lib/exposure";

type Account = { id: number; name: string; currency: string };

/** The categorical chart tokens, one per asset class in a fixed order, so a colour means one
 * thing everywhere; a label without one (alternatives, other) takes the muted tone. */
const SEGMENT_OF: Record<string, string> = {
  australian_shares: "bg-chart-1",
  international_shares: "bg-chart-2",
  property: "bg-chart-3",
  infrastructure: "bg-chart-4",
  fixed_income: "bg-chart-5",
  cash: "bg-chart-6",
};

/** A simple stacked bar of the lines that have a positive share; the table beside it has the
 * exact figures (colour is never the only cue). */
function Bar({ lines }: { lines: LookThroughLine[] }) {
  const shown = lines.filter((l) => l.percent !== null && l.percent !== "0.00" && !l.percent.startsWith("-"));
  return (
    <div
      role="img"
      aria-label="Share of the Account's value by line"
      className="flex h-3 w-full overflow-hidden rounded-full bg-muted"
    >
      {shown.map((line, i) => (
        <div
          key={`${line.kind}-${line.key ?? line.instrument_id ?? i}`}
          className={
            line.kind === "label"
              ? (SEGMENT_OF[line.key ?? ""] ?? "bg-muted-foreground/40")
              : line.kind === "no_profile"
                ? "bg-warning/50"
                : "bg-muted-foreground/40"
          }
          style={{ width: `${line.percent}%` }}
          title={`${line.name} ${line.percent}%`}
        />
      ))}
    </div>
  );
}

function LineRow({ line, flagged }: { line: LookThroughLine; flagged: boolean }) {
  const muted = line.kind === "not_classified" || (line.kind === "unallocated" && !flagged);
  return (
    <TableRow data-kind={line.kind} className={muted ? "text-muted-foreground" : undefined}>
      <TableCell className="break-words">
        {line.kind === "no_profile" && line.instrument_id !== null ? (
          <>
            {line.name}{" "}
            <Link
              to="/instruments/$instrumentId"
              params={{ instrumentId: String(line.instrument_id) }}
              className="whitespace-nowrap text-primary underline underline-offset-2"
            >
              Enter a profile
            </Link>
          </>
        ) : (
          line.name
        )}
        {line.kind === "unallocated" && flagged && (
          <Badge variant="flagged" className="ml-2">
            Gap beyond tolerance
          </Badge>
        )}
      </TableCell>
      <TableCell className="text-right font-mono tabular-nums">
        {line.percent === null ? "" : `${line.percent}%`}
      </TableCell>
      <TableCell className="text-right">
        <Money amount={line.amount.amount} currency={line.amount.currency} />
      </TableCell>
    </TableRow>
  );
}

function notes(data: LookThrough): string[] {
  const out: string[] = [];
  for (const part of data.parts) {
    if (part.dated_after)
      out.push(`${part.name}: the profile is dated after ${data.as_at} (${part.profile_as_at}), used because none is earlier.`);
    if (part.shortfall_percent !== "0")
      out.push(`${part.name}: its profile is ${part.shortfall_percent} points short of 100%, shown as Not classified.`);
    for (const t of part.trimmed)
      out.push(`${part.name}: ${t.percent} points over 100% trimmed from ${t.label_key.replace(/_/g, " ")}, its largest weight.`);
  }
  return out;
}

/**
 * An investment Account's value split by asset class (ADR-0030), from each Holding's Price x
 * quantity and the Instrument's Exposure profile. The lines include No profile (linking to enter
 * one), Not classified and Unallocated, and add to the Account's value. The as-at date is the one
 * the Derived value panel shows. Shown only when the Account's value is built from Holdings.
 */
export function LookThroughPanel({ account, asAt }: { account: Account; asAt: string | undefined }) {
  const look = useQuery({
    queryKey: ["look-through", account.id, asAt ?? "latest"],
    queryFn: () => fetchLookThrough(account.id, asAt),
    placeholderData: keepPreviousData,
  });
  const data = look.data;
  if (look.error) {
    return <p className="text-sm text-muted-foreground">Could not load the look-through: {look.error.message}</p>;
  }
  if (!data?.applies) return null;

  return (
    <Card aria-label="Look-through" role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Look-through by {data.dimension_name.toLowerCase()}</CardTitle>
        <span className="text-sm text-muted-foreground">as at {data.as_at}</span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {data.status === "not_analysed" || !data.total ? (
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm break-words">
            Not analysed. {data.reason}
          </p>
        ) : (
          <>
            <p className="flex flex-wrap items-baseline gap-2 text-sm text-muted-foreground">
              Account value{" "}
              <Money amount={data.total.amount} currency={data.total.currency} className="text-lg font-semibold text-foreground" />
            </p>
            <Bar lines={data.lines} />
            <Table aria-label="Look-through lines">
              <TableHeader>
                <TableRow>
                  <TableHead>{data.dimension_name}</TableHead>
                  <TableHead className="text-right">Share</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.lines.map((line) => (
                  <LineRow
                    key={`${line.kind}-${line.key ?? line.instrument_id ?? ""}`}
                    line={line}
                    flagged={data.flagged}
                  />
                ))}
              </TableBody>
            </Table>
            {notes(data).map((n) => (
              <p key={n} className="text-sm text-muted-foreground">
                {n}
              </p>
            ))}
          </>
        )}
      </CardContent>
    </Card>
  );
}

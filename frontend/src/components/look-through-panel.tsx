import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  DEFAULT_DIMENSION,
  fetchExposureVocabulary,
  fetchLookThrough,
  type LookThrough,
  type LookThroughLine,
} from "@/lib/exposure";

type Account = { id: number; name: string; currency: string };

/** The categorical chart tokens, one per asset class in a fixed order, so a colour means one
 * thing everywhere; a label without one (alternatives, other) takes the muted tone. The other
 * Dimensions colour their labels by position, cycling these tokens; the table beside the bar has
 * the exact figures. */
const SEGMENT_OF: Record<string, string> = {
  australian_shares: "bg-chart-1",
  international_shares: "bg-chart-2",
  property: "bg-chart-3",
  infrastructure: "bg-chart-4",
  fixed_income: "bg-chart-5",
  cash: "bg-chart-6",
};
const CYCLE = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5", "bg-chart-6"];

/** A simple stacked bar of the lines that have a positive share; the table beside it has the
 * exact figures (colour is never the only cue). */
function Bar({ lines, dimension }: { lines: LookThroughLine[]; dimension: string }) {
  const labelIndex = new Map(lines.filter((l) => l.kind === "label").map((l, i) => [l.key, i]));
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
              ? line.key === "other"
                ? "bg-muted-foreground/40"
                : dimension === DEFAULT_DIMENSION
                  ? (SEGMENT_OF[line.key ?? ""] ?? "bg-muted-foreground/40")
                  : CYCLE[(labelIndex.get(line.key) ?? 0) % CYCLE.length]
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
  const dimension = data.dimension_name.toLowerCase();
  for (const part of data.parts) {
    if (part.omitted)
      out.push(`${part.name}: its profile gives no ${dimension} weights, so all of it shows as Not classified.`);
    if (part.dated_after)
      out.push(`${part.name}: the profile is dated after ${data.as_at} (${part.profile_as_at}), used because none is earlier.`);
    if (part.shortfall_percent !== "0")
      out.push(`${part.name}: its profile is ${part.shortfall_percent} points short of 100%, shown as Not classified.`);
    for (const t of part.trimmed)
      out.push(`${part.name}: ${t.percent} points over 100% trimmed from ${t.label_key.replace(/_/g, " ")}, its largest weight.`);
  }
  return out;
}

/** The Dimension switcher: the selected one lives in the URL, so a view is linkable. */
function DimensionSwitcher({
  selected,
  onSelect,
}: {
  selected: string;
  onSelect: (key: string) => void;
}) {
  const vocab = useQuery({ queryKey: ["exposure-vocabulary"], queryFn: fetchExposureVocabulary });
  if (!vocab.data || vocab.data.length < 2) return null;
  return (
    <div role="tablist" aria-label="Dimension" className="flex flex-wrap gap-1">
      {vocab.data.map((d) => (
        <Button
          key={d.key}
          role="tab"
          variant={d.key === selected ? "secondary" : "outline"}
          aria-selected={d.key === selected}
          onClick={() => onSelect(d.key)}
        >
          {d.name}
        </Button>
      ))}
    </div>
  );
}

/**
 * An investment Account's value split by one Dimension (ADR-0030: asset class, region, sector or
 * exposure currency), from each Holding's Price x quantity and the Instrument's Exposure profile.
 * The lines include No profile (linking to enter one), Not classified and Unallocated, and add to
 * the Account's value in every Dimension. The as-at date is the one the Derived value panel
 * shows. Shown only when the Account's value is built from Holdings.
 */
export function LookThroughPanel({
  account,
  asAt,
  dimension,
  onDimension,
}: {
  account: Account;
  asAt: string | undefined;
  dimension: string | undefined;
  onDimension: (key: string | undefined) => void;
}) {
  const selected = dimension ?? DEFAULT_DIMENSION;
  const look = useQuery({
    queryKey: ["look-through", account.id, asAt ?? "latest", selected],
    queryFn: () => fetchLookThrough(account.id, asAt, dimension),
    placeholderData: keepPreviousData,
  });
  const data = look.data;
  if (look.error) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">Could not load the look-through: {look.error.message}</p>
        {dimension && <DimensionSwitcher selected={selected} onSelect={(k) => onDimension(k === DEFAULT_DIMENSION ? undefined : k)} />}
      </div>
    );
  }
  if (!data?.applies) return null;

  return (
    <Card aria-label="Look-through" role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Look-through by {data.dimension_name.toLowerCase()}</CardTitle>
        <span className="text-sm text-muted-foreground">as at {data.as_at}</span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <DimensionSwitcher selected={selected} onSelect={(k) => onDimension(k === DEFAULT_DIMENSION ? undefined : k)} />
        {data.dimension_key === "exposure_currency" && (
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Holding currency {account.currency}</span> is what this
            Account is valued in. The table is its <span className="font-medium text-foreground">Exposure currency</span>:
            the currencies the underlying assets are actually exposed to.
          </p>
        )}
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
            <Bar lines={data.lines} dimension={data.dimension_key} />
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
            {data.lines.some((l) => l.kind === "not_classified") && (
              <p className="text-sm text-muted-foreground">
                Not classified is value with no {data.dimension_name.toLowerCase()} weights: a profile that leaves
                this Dimension out, a shortfall in the weights, or cash where cash has no{" "}
                {data.dimension_name.toLowerCase()}.
              </p>
            )}
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

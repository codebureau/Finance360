import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Fragment, useMemo, useState } from "react";

import { ShareChart } from "@/components/share-chart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { headline, lookThroughRows } from "@/lib/exposure-chart";
import {
  DEFAULT_DIMENSION,
  fetchExposureVocabulary,
  fetchLookThrough,
  type LookThrough,
  type LookThroughLine,
} from "@/lib/exposure";

type Account = { id: number; name: string; currency: string };

/** The route through the funds, from the held fund down to the one that gave. */
const routeText = (path: string[]) => path.join(" › ");

/**
 * A look-through line. One that an underlying fund gave to is marked, and "Show underlying" lists
 * which fund gave what (the routes add up to the line).
 */
function LineRow({ line, flagged }: { line: LookThroughLine; flagged: boolean }) {
  const [open, setOpen] = useState(false);
  const muted = line.kind === "not_classified" || (line.kind === "unallocated" && !flagged);
  return (
    <Fragment>
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
          {line.through_fund && (
            <>
              <Badge variant="info" className="mx-2">
                Through a fund
              </Badge>
              <Button
                variant="link"
                aria-expanded={open}
                aria-label={`${open ? "Hide" : "Show"} underlying: ${line.name}`}
                onClick={() => setOpen((v) => !v)}
              >
                {open ? "Hide underlying" : "Show underlying"}
              </Button>
            </>
          )}
        </TableCell>
        <TableCell className="text-right font-mono tabular-nums">
          {line.percent === null ? "" : `${line.percent}%`}
        </TableCell>
        <TableCell className="text-right">
          <Money amount={line.amount.amount} currency={line.amount.currency} />
        </TableCell>
      </TableRow>
      {open &&
        line.contributions.map((c) => (
          <TableRow key={routeText(c.path)} data-kind="contribution" className="text-sm text-muted-foreground">
            <TableCell className="pl-6 break-words">{routeText(c.path)}</TableCell>
            <TableCell />
            <TableCell className="text-right">
              <Money amount={c.amount.amount} currency={c.amount.currency} />
            </TableCell>
          </TableRow>
        ))}
    </Fragment>
  );
}

type ProfileNotes = {
  omitted: boolean;
  dated_after: boolean;
  profile_as_at: string | null;
  shortfall_percent: string;
  trimmed: { label_key: string; percent: string }[];
};

function notes(data: LookThrough): string[] {
  const out: string[] = [];
  const dimension = data.dimension_name.toLowerCase();
  const about = (name: string, n: ProfileNotes) => {
    if (n.omitted)
      out.push(`${name}: its profile gives no ${dimension} weights, so all of it shows as Not classified.`);
    if (n.dated_after)
      out.push(`${name}: the profile is dated after ${data.as_at} (${n.profile_as_at}), used because none is earlier.`);
    if (n.shortfall_percent !== "0")
      out.push(`${name}: its profile is ${n.shortfall_percent} points short of 100%, shown as Not classified.`);
    for (const t of n.trimmed)
      out.push(`${name}: ${t.percent} points over 100% trimmed from ${t.label_key.replace(/_/g, " ")}, its largest weight.`);
  };
  for (const part of data.parts) {
    about(part.name, part);
    for (const u of part.underlying) about(routeText(u.path), u);
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
 * A fund-of-funds Holding resolves through its underlying funds: the lines it gave to are marked
 * and can show which fund gave what. The lines include No profile (linking to enter one), Not
 * classified and Unallocated, and add to the Account's value in every Dimension. The as-at date is
 * the one the Derived value panel shows. Shown only when the Account's value is built from
 * Holdings.
 *
 * A `share` view (ADR-0031): a donut by default, switchable, remembered per Dimension under
 * `lookthrough.<dimension>`. Beyond six parts the smallest fold into one Other for the picture
 * (the kit's fold rule); the table keeps every line.
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
  const vocab = useQuery({ queryKey: ["exposure-vocabulary"], queryFn: fetchExposureVocabulary });
  const data = look.data;
  const rows = useMemo(() => (data?.applies && data.total ? lookThroughRows(data, vocab.data ?? []) : []), [data, vocab.data]);
  const select = (key: string) => onDimension(key === DEFAULT_DIMENSION ? undefined : key);
  if (look.error) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">Could not load the look-through: {look.error.message}</p>
        {dimension && <DimensionSwitcher selected={selected} onSelect={select} />}
      </div>
    );
  }
  if (!data?.applies) return null;
  const name = data.dimension_name.toLowerCase();

  if (data.status === "not_analysed" || !data.total) {
    return (
      <Card aria-label="Look-through" role="region">
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Look-through by {name}</CardTitle>
          <span className="text-sm text-muted-foreground">as at {data.as_at}</span>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <DimensionSwitcher selected={selected} onSelect={select} />
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm break-words">
            Not analysed. {data.reason}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <ShareChart
      viewKey={`lookthrough.${data.dimension_key.replace(/_/g, "-")}`}
      title={`Look-through by ${name}`}
      headline={headline(data)}
      rows={rows}
      chartLabel={`The Account's value by ${name}.`}
      toolbar={<DimensionSwitcher selected={selected} onSelect={select} />}
      figures={
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
              <LineRow key={`${line.kind}-${line.key ?? line.instrument_id ?? ""}`} line={line} flagged={data.flagged} />
            ))}
          </TableBody>
        </Table>
      }
      after={
        <>
          <p className="flex flex-wrap items-baseline gap-2 text-sm text-muted-foreground">
            Account value{" "}
            <Money amount={data.total.amount} currency={data.total.currency} className="text-lg font-semibold text-foreground" />{" "}
            as at {data.as_at}
          </p>
          {data.dimension_key === "exposure_currency" && (
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Holding currency {account.currency}</span> is what this
              Account is valued in. The chart is its <span className="font-medium text-foreground">Exposure currency</span>:
              the currencies the underlying assets are actually exposed to.
            </p>
          )}
          {data.lines.some((l) => l.kind === "not_classified") && (
            <p className="text-sm text-muted-foreground">
              Not classified is value with no {name} weights: a profile that leaves this Dimension out, a shortfall in the
              weights, or cash where cash has no {name}.
            </p>
          )}
          {data.lines.some((l) => l.through_fund) && (
            <p className="text-sm text-muted-foreground">
              A line marked Through a fund includes value that a fund-of-funds holds in other funds. Under Show figures,
              Show underlying says which fund gave which part.
            </p>
          )}
          {notes(data).map((n) => (
            <p key={n} className="text-sm text-muted-foreground">
              {n}
            </p>
          ))}
        </>
      }
    />
  );
}

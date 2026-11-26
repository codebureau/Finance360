import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useMemo } from "react";

import { ComparisonChart } from "@/components/comparison-chart";
import { GeneralError } from "@/components/form-fields";
import { SeriesChart } from "@/components/series-chart";
import { StatTile } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { ViewCard } from "@/components/view-card";
import {
  balancesHeadline,
  balancesModel,
  drawnSet,
  incomeHeadline,
  incomeModel,
  lastsText,
  type DrawnSet,
} from "@/lib/compare";
import { toneBg } from "@/lib/entity-colour";
import { cn } from "@/lib/utils";
import { phaseStart } from "@/lib/projection";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchHousehold } from "@/lib/household";
import {
  compareScenarios,
  slotId,
  scenarioTone,
  slotsFor,
  type ComparedScenario,
  type Comparison,
  type Slot,
} from "@/lib/scenarios";

function Result({ s }: { s: ComparedScenario }) {
  if (!s.resolved) {
    return (
      <div className="flex flex-col gap-1" data-testid="unresolved">
        <Badge variant="flagged">Not projected</Badge>
        {s.errors.map((e) => (
          <span key={e.field} className="text-xs text-muted-foreground">
            {e.message}
          </span>
        ))}
        <Link
          to="/projection"
          search={{ scenario: s.scenario.id }}
          className="text-xs text-primary underline-offset-4 hover:underline"
        >
          Fix this in {s.scenario.name}
        </Link>
      </div>
    );
  }
  return s.lasts_beyond_horizon ? (
    <div className="flex flex-col gap-1">
      <Badge variant="ok">Beyond the horizon</Badge>
      <span className="text-xs text-muted-foreground">Spending met to age {s.horizon_age}</span>
    </div>
  ) : (
    <div className="flex flex-col gap-1">
      <span className="font-medium">Funds last to age {s.funds_last_to_age}</span>
      <Badge variant="flagged">Short after that</Badge>
    </div>
  );
}

function Sustainable({ s }: { s: ComparedScenario }) {
  if (!s.sustainable) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="whitespace-nowrap">
      {s.sustainable.capped && <span className="mr-1 text-xs text-muted-foreground">At least</span>}
      <Money amount={s.sustainable.annual_spending} />
      <span className="text-xs text-muted-foreground"> a year</span>
    </span>
  );
}

/** The first year an Age Pension is paid, or why none is. */
function AgePensionCell({ s }: { s: ComparedScenario }) {
  const a = s.age_pension;
  if (!a) return <span className="text-muted-foreground">—</span>;
  if (a.status !== "calculated") return <Badge variant="flagged">Not calculated</Badge>;
  if (a.first_year === null) return <span className="text-xs text-muted-foreground">None paid in the projection</span>;
  return (
    <div className="flex flex-col gap-1">
      <span className="whitespace-nowrap">
        <Money amount={a.first_amount} />
        <span className="text-xs text-muted-foreground"> a year</span>
      </span>
      <span className="text-xs text-muted-foreground">First paid in year {a.first_year}, at age {a.first_age}</span>
    </div>
  );
}

function valueOf(s: ComparedScenario, slot: Slot) {
  const x = s.provenance.find((p) => slotId(p.key, p.scope_ref) === slot.id);
  if (!x) return <span className="text-muted-foreground">—</span>;
  const shown = slot.unit === "money" ? <Money amount={x.value} /> : slot.unit === "percent" ? `${x.value}%` : x.value;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className="font-mono tabular-nums">{shown}</span>
      {x.layer === "scenario" && <Badge variant="info">Own</Badge>}
    </span>
  );
}

/** One Scenario's spending phases as a short list, for the row that shows them side by side. */
function SpendingCell({ s }: { s: ComparedScenario }) {
  if (!s.spending) return <span className="text-muted-foreground">-</span>;
  return (
    <div className="flex flex-col gap-1">
      <ul className="flex flex-col gap-1">
        {s.spending.phases.map((p) => (
          <li key={`${p.anchor}-${p.offset_years}`}>
            <Money amount={p.annual_amount} /> <span className="text-muted-foreground">a year</span>
            <span className="block text-muted-foreground">{phaseStart(p.anchor, p.offset_years)}</span>
          </li>
        ))}
      </ul>
      {s.spending.layer === "scenario" && <Badge variant="info">Own</Badge>}
    </div>
  );
}

function ComparisonTable({ data, slots, ids }: { data: Comparison; slots: Slot[]; ids: number[] }) {
  const differing = data.differing
    .map((d) => slots.find((s) => s.id === slotId(d.key, d.scope_ref)))
    .filter((s): s is Slot => s !== undefined);
  return (
    <Table data-testid="comparison-table">
      <caption className="sr-only">Scenarios side by side: the result, sustainable retirement income and the Assumptions that differ</caption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">&nbsp;</TableHead>
          {data.scenarios.map((s) => (
            <TableHead key={s.scenario.id} scope="col">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn("inline-block size-2.5 shrink-0 rounded-full", toneBg(scenarioTone(ids, s.scenario.id)))}
                />
                <Link
                  to="/projection"
                  search={{ scenario: s.scenario.id }}
                  className="text-sm text-primary underline-offset-4 hover:underline"
                >
                  {s.scenario.name}
                </Link>
              </span>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">Result</TableHead>
          {data.scenarios.map((s) => (
            <TableCell key={s.scenario.id} className="align-top" data-testid={`result-${s.scenario.id}`}>
              <Result s={s} />
            </TableCell>
          ))}
        </TableRow>
        <TableRow>
          <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">Sustainable retirement income</TableHead>
          {data.scenarios.map((s) => (
            <TableCell key={s.scenario.id} data-testid={`sustainable-${s.scenario.id}`}>
              <Sustainable s={s} />
            </TableCell>
          ))}
        </TableRow>
        <TableRow>
          <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">Age Pension, first year paid</TableHead>
          {data.scenarios.map((s) => (
            <TableCell key={s.scenario.id} className="align-top" data-testid={`age-pension-${s.scenario.id}`}>
              <AgePensionCell s={s} />
            </TableCell>
          ))}
        </TableRow>
        {data.drawdown_differs && (
          <TableRow data-testid="drawdown-row">
            <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">Drawdown order</TableHead>
            {data.scenarios.map((s) => (
              <TableCell key={s.scenario.id} className="align-top text-xs">
                {s.resolved ? (
                  <ol className="list-decimal pl-4">
                    {s.drawdown_order.map((c) => (
                      <li key={c.key}>{c.name}</li>
                    ))}
                  </ol>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
            ))}
          </TableRow>
        )}
        {data.spending_differs && (
          <TableRow data-testid="spending-row">
            <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">Spending phases</TableHead>
            {data.scenarios.map((s) => (
              <TableCell key={s.scenario.id} className="align-top text-xs">
                <SpendingCell s={s} />
              </TableCell>
            ))}
          </TableRow>
        )}
        {differing.map((slot) => (
          <TableRow key={slot.id} data-testid="differing-row">
            <TableHead scope="row" className="max-w-32 text-left font-normal whitespace-normal">{slot.label}</TableHead>
            {data.scenarios.map((s) => (
              <TableCell key={s.scenario.id}>{valueOf(s, slot)}</TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Each year's total closing balance per drawn Scenario: the text equivalent of the lines. */
function BalancesTable({ set }: { set: DrawnSet }) {
  const model = balancesModel(set);
  return (
    <div
      className="max-h-64 min-w-0 overflow-auto"
      tabIndex={0}
      role="region"
      aria-label="Total balance by Scenario table"
      data-testid="balances-table"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">Total balance at the end of each year, by Scenario</caption>
        <thead className="sticky top-0 bg-card text-muted-foreground">
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1 pr-4 text-xs font-medium">
              End of
            </th>
            {model.lines.map((l) => (
              <th key={l.key} scope="col" className="px-2 py-1 text-right text-xs font-medium">
                {l.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {model.labels.map((year, i) => (
            <tr key={year}>
              <th scope="row" className="py-1 pr-4 text-left font-normal whitespace-nowrap">
                {year}
              </th>
              {model.lines.map((l) => (
                <td key={l.key} className="px-2 py-1 text-right whitespace-nowrap">
                  {l.values[i] === null ? <span className="text-muted-foreground">—</span> : <Money amount={l.values[i]} />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What is not in the pictures, and why: more than six Scenarios, or one that could not be projected. */
function NotDrawn({ set }: { set: DrawnSet }) {
  if (set.folded.length === 0 && set.unresolved.length === 0) return null;
  return (
    <p className="text-xs text-muted-foreground" data-testid="not-drawn">
      {set.folded.length > 0 &&
        `Six Scenarios are drawn; ${set.folded.map((s) => s.scenario.name).join(", ")} ${set.folded.length === 1 ? "is" : "are"} in the comparison table only. `}
      {set.unresolved.length > 0 &&
        `Not projected, so not drawn: ${set.unresolved.map((s) => s.scenario.name).join(", ")}. The comparison table says why.`}
    </p>
  );
}

/** Every Scenario projected side by side: the range of outcomes and what makes them differ. */
export function ComparePage() {
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const comparison = useQuery({ queryKey: ["projection", "compare"], queryFn: compareScenarios });
  const slots = slotsFor(household.data?.members ?? []);
  const set = useMemo(() => (comparison.data ? drawnSet(comparison.data) : null), [comparison.data]);
  const balances = useMemo(() => (set ? balancesModel(set) : null), [set]);
  const income = useMemo(() => (set ? incomeModel(set) : null), [set]);
  const table = comparison.data && set && (
    <div className="flex flex-col gap-2">
      <ComparisonTable data={comparison.data} slots={slots} ids={set.ids} />
      <p className="text-xs text-muted-foreground">
        Only the Assumptions that differ between Scenarios are listed. Own marks a value the Scenario states itself;
        the rest are the Household defaults.
      </p>
    </div>
  );
  return (
    <div className="flex flex-col gap-4">
      <Link
        to="/projection"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={14} />
        Projection
      </Link>
      <PageHeader
        title="Compare Scenarios"
        description="Each Scenario projected from today's Position, in today's dollars. Open one to see its year-by-year detail."
      />
      <GeneralError error={comparison.error ?? household.error} />
      {(comparison.isPending || household.isPending) && <p className="text-sm text-muted-foreground">Loading…</p>}
      {comparison.data && household.data && set && balances && income && (
        <>
          {set.drawn.length > 0 ? (
            <>
              <ViewCard
                title="Total balance by Scenario"
                headline={balancesHeadline(set)}
                figures={<BalancesTable set={set} />}
                figuresName="total balance by Scenario"
              >
                <SeriesChart
                  model={balances}
                  what="total balance at the end of each year, by Scenario"
                  headline={balancesHeadline(set)}
                  testId="balances-chart"
                />
              </ViewCard>
              <ViewCard
                title="Headline results"
                headline={incomeHeadline(set)}
                figures={table}
                figuresName="comparison table"
              >
                <ComparisonChart model={income} headline={incomeHeadline(set)} testId="income-chart" />
                <div className="grid grid-cols-2 gap-2 lg:grid-cols-3" data-testid="lasts-tiles">
                  {set.drawn.map((s) => (
                    <StatTile
                      key={s.scenario.id}
                      flat
                      compact
                      accent={scenarioTone(set.ids, s.scenario.id)}
                      label={`${s.scenario.name}: funds last to`}
                      hero={lastsText(s)}
                      supporting={[]}
                    />
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  &ldquo;Beyond age&rdquo; means spending is met in full to the horizon; a plain age means spending is
                  not met in full after that age.
                </p>
              </ViewCard>
            </>
          ) : (
            table
          )}
          <NotDrawn set={set} />
        </>
      )}
    </div>
  );
}

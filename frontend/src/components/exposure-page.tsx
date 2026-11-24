import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId, useMemo } from "react";

import { DimensionSwitcher } from "@/components/look-through-panel";
import { FORM_OPTIONS, ShareBody, ShareChart } from "@/components/share-chart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ViewCard } from "@/components/view-card";
import { SHARE_FORMS, shareChartLabel, type ShareForm, type ShareRow } from "@/lib/charts/share";
import { useDisplayPreference } from "@/lib/display-preferences";
import { DEFAULT_DIMENSION, fetchExposureVocabulary, fetchLookThroughRollup, type Rollup } from "@/lib/exposure";
import {
  entitiesOf,
  entityRows,
  hasChart,
  householdHeadline,
  levelHeadline,
  LEVELS,
  notAnalysedGroups,
  type Entity,
  type ExposureSearch,
  type Level,
} from "@/lib/exposure-rollup";

type Vocab = Awaited<ReturnType<typeof fetchExposureVocabulary>>;

/** The level switcher: the selected one lives in the URL, so a view is linkable. */
function LevelSwitcher({ selected, onSelect }: { selected: Level; onSelect: (level: Level) => void }) {
  return (
    <div role="tablist" aria-label="Level" className="flex flex-wrap gap-1">
      {LEVELS.map((l) => (
        <Button
          key={l.key}
          role="tab"
          variant={l.key === selected ? "secondary" : "outline"}
          aria-selected={l.key === selected}
          onClick={() => onSelect(l.key)}
        >
          {l.label}
        </Button>
      ))}
    </div>
  );
}

function AccountLink({ id, name, rollup, dimension }: { id: number; name: string; rollup: Rollup; dimension: string | undefined }) {
  return (
    <Link
      to="/accounts/$accountId"
      params={{ accountId: String(id) }}
      search={{ ...(dimension ? { dimension } : {}), asat: rollup.as_at }}
      className="text-primary underline underline-offset-2"
    >
      {name}
    </Link>
  );
}

/**
 * The exact figures behind a level: each entity's lines, what is Not analysed in it, and its total,
 * then the Household's total, which is the Position's figure.
 */
function FiguresTable({
  rollup,
  level,
  entities,
  dimension,
}: {
  rollup: Rollup;
  level: Level;
  entities: Entity[];
  dimension: string | undefined;
}) {
  const heading = level === "household" ? "Household" : LEVELS.find((l) => l.key === level)?.label;
  return (
    <Table aria-label={`Exposure figures by ${heading}`}>
      <TableHeader>
        <TableRow>
          <TableHead>{rollup.dimension_name}</TableHead>
          <TableHead className="text-right">Share</TableHead>
          <TableHead className="text-right">Value</TableHead>
        </TableRow>
      </TableHeader>
      {entities.map((e) => (
        <TableBody key={e.key} aria-label={e.name} className="border-t border-border">
          {level !== "household" && (
            <TableRow className="bg-muted/30">
              <TableHead scope="rowgroup" colSpan={2} className="text-left break-words whitespace-normal text-foreground">
                {e.accountId !== undefined ? (
                  <AccountLink id={e.accountId} name={e.name} rollup={rollup} dimension={dimension} />
                ) : (
                  e.name
                )}
              </TableHead>
              <TableHead className="text-right text-foreground">
                <Money amount={e.figures.total.amount} currency={e.figures.total.currency} />
              </TableHead>
            </TableRow>
          )}
          {e.figures.lines.map((line) => (
            <TableRow
              key={`${line.kind}-${line.key ?? ""}`}
              data-kind={line.kind}
              className={line.kind === "not_classified" || line.kind === "unallocated" ? "text-muted-foreground" : undefined}
            >
              <TableCell className="break-words">{line.name}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">{line.percent === null ? "" : `${line.percent}%`}</TableCell>
              <TableCell className="text-right">
                <Money amount={line.amount.amount} currency={line.amount.currency} />
              </TableCell>
            </TableRow>
          ))}
          {e.figures.not_analysed.amount_minor !== 0 && (
            <TableRow data-kind="not_analysed" className="text-muted-foreground">
              <TableCell className="break-words">Not analysed</TableCell>
              <TableCell />
              <TableCell className="text-right">
                <Money amount={e.figures.not_analysed.amount} currency={e.figures.not_analysed.currency} />
              </TableCell>
            </TableRow>
          )}
          {level === "household" && (
            <TableRow className="font-medium">
              <TableCell>Household total</TableCell>
              <TableCell />
              <TableCell className="text-right">
                <Money amount={e.figures.total.amount} currency={e.figures.total.currency} />
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      ))}
      {level !== "household" && (
        <TableBody className="border-t border-border">
          <TableRow className="font-medium">
            <TableCell colSpan={2}>Household total</TableCell>
            <TableCell className="text-right">
              <Money amount={rollup.household.total.amount} currency={rollup.household.total.currency} />
            </TableCell>
          </TableRow>
        </TableBody>
      )}
    </Table>
  );
}

/** One compact card: the entity's own small chart and its analysed value (the whole of the chart). */
function EntityCard({
  entity,
  rows,
  form,
  rollup,
  dimension,
  headline,
}: {
  entity: Entity;
  rows: ShareRow[];
  form: ShareForm;
  rollup: Rollup;
  dimension: string | undefined;
  headline: string;
}) {
  const { figures } = entity;
  return (
    <section
      aria-label={`${entity.name} by ${rollup.dimension_name.toLowerCase()}`}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="flex min-w-0 items-center gap-2 text-sm font-medium break-words">
          {entity.accountId !== undefined ? (
            <AccountLink id={entity.accountId} name={entity.name} rollup={rollup} dimension={dimension} />
          ) : (
            entity.name
          )}
        </h3>
        <Money amount={figures.analysed.amount} currency={figures.analysed.currency} className="text-sm font-semibold" />
      </div>
      {figures.not_analysed.amount_minor !== 0 && (
        <p className="text-xs text-muted-foreground">
          Not analysed <Money amount={figures.not_analysed.amount} currency={figures.not_analysed.currency} />, so the total is{" "}
          <Money amount={figures.total.amount} currency={figures.total.currency} />
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing to chart.</p>
      ) : (
        <ShareBody
          form={form}
          rows={rows}
          stacked
          label={`${shareChartLabel(form, headline)} ${entity.name} by ${rollup.dimension_name.toLowerCase()}.`}
          legendLabel={`${entity.name}: legend`}
        />
      )}
    </section>
  );
}

/**
 * Members, Categories or Accounts as small multiples: one compact card each, every chart in the one
 * form the single switch chooses (`exposure.<level>`).
 */
function SmallMultiples({
  rollup,
  level,
  vocab,
  dimension,
}: {
  rollup: Rollup;
  level: Exclude<Level, "household">;
  vocab: Vocab;
  dimension: string | undefined;
}) {
  const groupId = useId();
  const pref = useDisplayPreference<ShareForm>(`exposure.${level}`, "share", SHARE_FORMS);
  const entities = useMemo(() => entitiesOf(rollup, level), [rollup, level]);
  const charted = useMemo(() => entities.filter(hasChart), [entities]);
  const rows = useMemo(() => new Map(charted.map((e) => [e.key, entityRows(e, rollup, vocab)])), [charted, rollup, vocab]);
  const headline = levelHeadline(rollup, level);
  const plain = LEVELS.find((l) => l.key === level)?.label ?? level;
  // A Member or Category whose value is all Not analysed has no chart; say so (an Account that is
  // Not analysed is in the list below, so it needs no note).
  const unanalysed = level === "account" ? [] : entities.filter((e) => !hasChart(e) && e.figures.total.amount_minor !== 0);
  return (
    <ViewCard
      title={`Exposure by ${plain}, by ${rollup.dimension_name.toLowerCase()}`}
      headline={headline}
      toolbar={
        <>
          <SegmentedControl
            label="Chart type"
            name={`exposure-${level}-form-${groupId}`}
            value={pref.form}
            options={FORM_OPTIONS}
            onChange={pref.choose}
          />
          {pref.canUseAsDefault && (
            <Button variant="outline" onClick={pref.makeDefault}>
              Use as my default
            </Button>
          )}
        </>
      }
      figures={<FiguresTable rollup={rollup} level={level} entities={entities} dimension={dimension} />}
    >
      {!pref.ready ? (
        <p className="text-sm text-muted-foreground">Loading chart</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {charted.map((e) => (
            <EntityCard
              key={e.key}
              entity={e}
              rows={rows.get(e.key) ?? []}
              form={pref.form}
              rollup={rollup}
              dimension={dimension}
              headline={headline}
            />
          ))}
        </div>
      )}
      {unanalysed.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing analysed in {unanalysed.map((e) => e.name).join(", ")}: all of it is Not analysed (below).
        </p>
      )}
    </ViewCard>
  );
}

function HouseholdView({ rollup, vocab, dimension }: { rollup: Rollup; vocab: Vocab; dimension: string | undefined }) {
  const [household] = entitiesOf(rollup, "household");
  const rows = useMemo(() => entityRows(household, rollup, vocab), [household, rollup, vocab]);
  const name = rollup.dimension_name.toLowerCase();
  const figures = <FiguresTable rollup={rollup} level="household" entities={[household]} dimension={dimension} />;
  if (rows.length === 0) {
    return (
      <ViewCard title={`Household exposure by ${name}`} headline={householdHeadline(rollup)} figures={figures}>
        <p className="text-sm text-muted-foreground">Nothing is analysed yet, so there is nothing to chart.</p>
      </ViewCard>
    );
  }
  return (
    <ShareChart
      viewKey={`exposure.household.${rollup.dimension_key.replace(/_/g, "-")}`}
      title={`Household exposure by ${name}`}
      headline={householdHeadline(rollup)}
      rows={rows}
      chartLabel={`The analysed part of the Household's Position by ${name}.`}
      figures={figures}
    />
  );
}

/** What is Not analysed, so the analysed figures plus this are the Position, exactly. */
function NotAnalysedCard({ rollup, dimension }: { rollup: Rollup; dimension: string | undefined }) {
  const { household, not_analysed: listed } = rollup;
  return (
    <Card>
      <section aria-labelledby="not-analysed-title" className="flex flex-col gap-3 p-4">
        <div className="flex flex-col gap-1">
          <h2 id="not-analysed-title" className="text-base font-semibold">
            Not analysed
          </h2>
          <p className="text-sm text-muted-foreground">
            Accounts with no look-through (cash, property, vehicles, loans and so on) are not split, but they count in the
            Position. Analysed <Money amount={household.analysed.amount} currency={household.analysed.currency} /> plus Not
            analysed <Money amount={household.not_analysed.amount} currency={household.not_analysed.currency} /> is{" "}
            <Money amount={household.total.amount} currency={household.total.currency} />, the Position{" "}
            <Link to="/position" search={{ on: rollup.as_at }} className="text-primary underline underline-offset-2">
              as at {rollup.as_at}
            </Link>
            .
          </p>
        </div>
        {listed.length === 0 ? (
          <p className="text-sm text-muted-foreground">Every Account is analysed.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {notAnalysedGroups(rollup).map((g) => (
              <section key={g.key} aria-labelledby={`not-analysed-${g.key}`} className="flex flex-col gap-1">
                <h3 id={`not-analysed-${g.key}`} className="text-sm font-medium">
                  {g.name}
                </h3>
                {g.reason && <p className="text-xs text-muted-foreground">{g.reason}</p>}
                <ul aria-label={`${g.name} not analysed`} className="flex flex-col divide-y divide-border">
                  {g.accounts.map((n) => (
                    <li key={n.account_id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2">
                      <span className="min-w-0 break-words text-sm">
                        <AccountLink id={n.account_id} name={n.name} rollup={rollup} dimension={dimension} />
                        {n.value === null && (
                          <Badge variant="flagged" className="ml-2">
                            Not counted
                          </Badge>
                        )}
                      </span>
                      {n.value !== null && <Money amount={n.value.amount} currency={n.value.currency} />}
                      {n.reason && <span className="basis-full text-xs text-muted-foreground">{n.reason}</span>}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </section>
    </Card>
  );
}

/**
 * Exposure: the whole Household's Position split by one Dimension (ADR-0030 section 8), as the
 * Household, by Member (a joint Account apportioned by ownership share), by Category or by Account.
 * The view leads with a headline and a chart; the exact figures sit behind Show figures, and what
 * is Not analysed is listed so the figures reconcile to the Position.
 */
export function ExposurePage({
  search,
  onSearch,
}: {
  search: ExposureSearch;
  onSearch: (patch: Partial<ExposureSearch>) => void;
}) {
  const dimension = search.dimension;
  const level = search.level ?? "household";
  const vocab = useQuery({ queryKey: ["exposure-vocabulary"], queryFn: fetchExposureVocabulary });
  const rollup = useQuery({
    queryKey: ["look-through-rollup", search.asat ?? "latest", dimension ?? DEFAULT_DIMENSION],
    queryFn: () => fetchLookThroughRollup(search.asat, dimension),
    placeholderData: keepPreviousData,
  });
  const selected = dimension ?? DEFAULT_DIMENSION;
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Exposure"
        description={
          rollup.data
            ? `What the Household's Position is made of, as at ${rollup.data.as_at}.`
            : "What the Household's Position is made of."
        }
      />
      <div className="flex flex-col gap-2">
        <DimensionSwitcher selected={selected} onSelect={(key) => onSearch({ dimension: key === DEFAULT_DIMENSION ? undefined : key })} />
        <LevelSwitcher selected={level} onSelect={(next) => onSearch({ level: next === "household" ? undefined : next })} />
      </div>
      {rollup.error ? (
        <p className="text-sm text-muted-foreground">Could not load the exposure: {rollup.error.message}</p>
      ) : !rollup.data || !vocab.data ? (
        <p className="text-sm text-muted-foreground">Loading</p>
      ) : (
        <>
          {level === "household" ? (
            <HouseholdView rollup={rollup.data} vocab={vocab.data} dimension={dimension} />
          ) : (
            <SmallMultiples rollup={rollup.data} level={level} vocab={vocab.data} dimension={dimension} />
          )}
          <NotAnalysedCard rollup={rollup.data} dimension={dimension} />
        </>
      )}
    </div>
  );
}

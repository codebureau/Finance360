import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";

import { AgePensionCard, AgePensionCell } from "@/components/age-pension";
import { AddScenarioDialog, AssumptionsSheet, DrawdownOrderLine } from "@/components/assumptions-editor";
import { GeneralError } from "@/components/form-fields";
import { SaveRunDialog } from "@/components/save-run-dialog";
import { SpendingPhasesUsed, SpendingSheet } from "@/components/spending-editor";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError, fetchHousehold, type Member } from "@/lib/household";
import { CATEGORY_LABEL, DEBIT_REASON_LABEL, formatAge, LIFE_EVENT_LABEL, SIMPLIFICATIONS } from "@/lib/projection";
import type { RunHeader } from "@/lib/projection-runs";
import {
  fetchScenarios,
  runScenario,
  RETIRED_SLOTS,
  slotId,
  slotsFor,
  type Review,
  type Scenario,
  type ScenarioProjection,
  type Slot,
} from "@/lib/scenarios";

export type ProjectionSearch = { scenario?: number };

/** The Projection's linkable state: `/projection?scenario=2`. Absent means Likely. */
export function parseProjectionSearch(raw: Record<string, unknown>): ProjectionSearch {
  const id = Number(raw.scenario);
  return Number.isInteger(id) && id > 0 ? { scenario: id } : {};
}

export function Headline({ p }: { p: ScenarioProjection }) {
  const youngest = p.reference_member;
  const horizonAge = youngest.age_now + p.years;
  return (
    <Card data-testid="headline">
      <CardContent className="flex flex-col gap-2 pt-6">
        {p.lasts_beyond_horizon ? (
          <>
            <p className="flex flex-wrap items-center gap-2 text-2xl font-semibold">
              Funds last beyond the horizon
              <Badge variant="ok">Spending met to age {horizonAge}</Badge>
            </p>
            <p className="text-sm text-muted-foreground">
              Every year to age {horizonAge} ({youngest.name}, the youngest Member) is covered in full.
            </p>
          </>
        ) : (
          <>
            <p className="flex flex-wrap items-center gap-2 text-2xl font-semibold">
              Funds last to age {p.funds_last_to_age}
              <Badge variant="flagged">Short after that</Badge>
            </p>
            <p className="text-sm text-muted-foreground">
              Spending is met in full up to the year {youngest.name} (the youngest Member) is{" "}
              {p.funds_last_to_age}. From then the funds in these Categories run out.
            </p>
          </>
        )}
        <div className="mt-2 border-t border-border pt-3" data-testid="sustainable">
          <p className="text-sm text-muted-foreground">Sustainable retirement income</p>
          <p className="flex flex-wrap items-baseline gap-x-2 text-2xl font-semibold">
            {p.sustainable.capped && <span className="text-base font-normal">At least</span>}
            <Money amount={p.sustainable.annual_spending} />
            <span className="text-base font-normal text-muted-foreground">a year</span>
          </p>
          <p className="text-sm text-muted-foreground">
            {p.sustainable.capped
              ? "No limit was found: even the search ceiling is met in full to the horizon."
              : `The most that can be spent each year, steady in today's dollars, from the year every Member has retired, and still be met in full to age ${horizonAge}.`}{" "}
            It ignores the spending amounts entered, keeping the proportions between the phases from the last
            retirement.
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Scenario {p.scenario.name}. From the Position as at {p.as_at}, in today&apos;s dollars. Computed by engine v
          {p.engine_version}.
        </p>
      </CardContent>
    </Card>
  );
}

export function Simplifications({ p }: { p: ScenarioProjection }) {
  return (
    <section aria-label="Simplifications" data-testid="simplifications">
      <Card>
        <CardHeader>
          <CardTitle>What this projection assumes</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {SIMPLIFICATIONS.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          {p.flagged.length > 0 && (
            <div data-testid="flagged-accounts" className="text-sm">
              <p className="font-medium text-warning">
                {p.flagged.length} {p.flagged.length === 1 ? "Account is" : "Accounts are"} not in the starting
                balances:
              </p>
              <ul className="list-disc pl-5">
                {p.flagged.map((f) => (
                  <li key={f.account_id}>
                    {f.name}{" "}
                    <span className="text-muted-foreground">
                      ({CATEGORY_LABEL[f.category_key] ?? f.category_key},{" "}
                      {f.status === "no_valuation" ? "no Valuation" : "no FX rate"} on or before {p.as_at})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

/** Balances at the end of each year by Category, with spending and any shortfall. */
export function YearTable({ p }: { p: ScenarioProjection }) {
  const first = p.rows[0];
  const hasPension = p.rows.some((r) => r.age_pension !== null);
  return (
    <section aria-label="Year by year">
      <h2 className="text-base font-semibold">Year by year</h2>
      <p className="mb-2 text-xs text-muted-foreground">
        Balances are at the end of each year. The age is the youngest Member&apos;s at the start of it. The
        first row marked Short is the year spending is no longer met in full. The Age Pension is a year&apos;s
        total; open its working for the year.
      </p>
      <div
        className="max-h-[32rem] overflow-auto rounded-lg border border-border bg-card"
        tabIndex={0}
        role="region"
        aria-label="Projection table"
        data-testid="projection-table"
      >
        <table className="w-full text-sm">
          <caption className="sr-only">
            Balance at the end of each year by Category, with the year&apos;s spending and any shortfall
          </caption>
          <thead className="sticky top-0 bg-card text-xs text-muted-foreground">
            <tr className="border-b border-border text-right">
              <th scope="col" className="px-[var(--cell-px)] py-1 text-left font-medium">
                Year
              </th>
              <th scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                Age at start of year
              </th>
              {p.categories.map((c) => (
                <th key={c.key} scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                  {c.name}
                </th>
              ))}
              <th scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                Total
              </th>
              {hasPension && (
                <th scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                  Age Pension
                </th>
              )}
              <th scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                Spending
              </th>
              <th scope="col" className="px-[var(--cell-px)] py-1 font-medium">
                Shortfall
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-border bg-muted/30" data-testid="start-row">
              <th scope="row" className="px-[var(--cell-px)] py-[var(--cell-py)] text-left font-medium whitespace-nowrap">
                Start ({first.starts_on})
              </th>
              <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right">{first.age}</td>
              {first.categories.map((c) => (
                <td key={c.category_key} className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                  <Money amount={c.opening} />
                </td>
              ))}
              <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                <Money amount={first.opening} />
              </td>
              {hasPension && <td />}
              <td />
              <td />
            </tr>
            {p.rows.map((r) => {
              const short = r.shortfall !== "0.00";
              return (
                <tr key={r.t} className="border-b border-border last:border-0" data-short={short || undefined}>
                  <th scope="row" className="px-[var(--cell-px)] py-[var(--cell-py)] text-left font-normal whitespace-nowrap">
                    Year {r.t + 1}
                    <span className="text-xs text-muted-foreground"> to {Number(r.starts_on.slice(0, 4)) + 1}</span>
                  </th>
                  <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right">{r.age}</td>
                  {r.categories.map((c) => (
                    <td key={c.category_key} className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                      <Money amount={c.closing} />
                    </td>
                  ))}
                  <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                    <Money amount={r.closing} />
                  </td>
                  {hasPension && (
                    <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                      <AgePensionCell p={p} row={r} />
                    </td>
                  )}
                  <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                    <Money amount={r.need} />
                  </td>
                  <td className="px-[var(--cell-px)] py-[var(--cell-py)] text-right whitespace-nowrap">
                    {short ? (
                      <>
                        <Badge variant="flagged" className="mr-1">
                          Short
                        </Badge>
                        <Money amount={r.shortfall} />
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <LineDebits p={p} />
    </section>
  );
}

/**
 * Regular amounts taken from a line (Division 293 paid by the fund), each under its year with what was asked,
 * what the line paid and what was paid personally. Nothing is shown when a run has none (ADR-0028).
 */
function LineDebits({ p }: { p: ScenarioProjection }) {
  const withDebits = p.rows.filter((r) => r.debits.length > 0);
  if (withDebits.length === 0) return null;
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  const label = (d: { reason: string; ref: string }) => `${DEBIT_REASON_LABEL[d.reason] ?? d.reason} (${d.ref})`;
  const unpaid = withDebits.flatMap((r) => r.debits.filter((d) => d.unpaid !== "0.00").map((d) => ({ r, d })));
  return (
    <div className="mt-3 flex flex-col gap-2" data-testid="line-debits">
      <h3 className="text-sm font-medium">Taken from a line</h3>
      <p className="text-xs text-muted-foreground">
        Each is paid from its own line at the end of the year, before any drawdown, and never takes the line below zero.
        What a line cannot pay is paid personally: it is added to that year&apos;s Spending and met by drawdown.
      </p>
      {unpaid.map(({ r, d }) => (
        <p
          key={`${r.t}-${d.member_id}-${d.reason}-${d.ref}`}
          role="status"
          data-testid="debit-unpaid"
          className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm break-words"
        >
          {DEBIT_REASON_LABEL[d.reason] ?? d.reason} could not be fully paid from{" "}
          {CATEGORY_LABEL[d.category_key]?.toLowerCase() ?? d.category_key} in year {r.t + 1} ({memberName(d.member_id)}):{" "}
          <Money amount={d.unpaid} /> paid personally.
        </p>
      ))}
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="line-debit-rows">
        {withDebits.flatMap((r) =>
          r.debits.map((d) => (
            <li key={`${r.t}-${d.member_id}-${d.reason}-${d.ref}`} className="px-3 py-[var(--cell-py)]">
              <p className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="font-medium">
                  {label(d)} <span className="font-normal text-muted-foreground">({memberName(d.member_id)})</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  Year {r.t + 1}, to {Number(r.starts_on.slice(0, 4)) + 1}
                </span>
              </p>
              <p className="text-xs text-muted-foreground">
                Requested <Money amount={d.requested} />, paid from {CATEGORY_LABEL[d.category_key]?.toLowerCase() ?? d.category_key}{" "}
                <Money amount={d.paid} />, paid personally <Money amount={d.unpaid} />.
              </p>
            </li>
          )),
        )}
      </ul>
    </div>
  );
}

/** The Scenarios as links, so the chosen one is in the URL and survives a refresh. */
function ScenarioSwitcher({
  scenarios,
  selected,
  onAdded,
}: {
  scenarios: Scenario[];
  selected: Scenario;
  onAdded: (added: Scenario) => void;
}) {
  return (
    <section aria-label="Scenario" className="flex flex-wrap items-center gap-2">
      {scenarios.map((s) => (
        <Link
          key={s.id}
          to="/projection"
          search={{ scenario: s.id }}
          aria-current={s.id === selected.id ? "page" : undefined}
          className={buttonVariants({ variant: s.id === selected.id ? "default" : "outline" })}
        >
          {s.name}
        </Link>
      ))}
      <Link to="/projection/compare" className={buttonVariants({ variant: "outline" })}>
        Compare Scenarios
      </Link>
      <AddScenarioDialog
        copyFrom={selected}
        onAdded={onAdded}
        trigger={
          <Button variant="ghost">
            <Plus size={16} aria-hidden /> Add Scenario
          </Button>
        }
      />
    </section>
  );
}

function EditButtons({
  scenario,
  members,
  onSaved,
}: {
  scenario: Scenario;
  members: Member[];
  onSaved: (review: Review[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <AssumptionsSheet
        scenario={scenario}
        members={members}
        onSaved={onSaved}
        trigger={<Button>Edit {scenario.name}</Button>}
      />
      <AssumptionsSheet
        scenario={null}
        members={members}
        onSaved={onSaved}
        trigger={<Button variant="outline">Edit Household defaults</Button>}
      />
    </div>
  );
}

/** The two ways to change the spending plan: this Scenario's own phases, or the Household default. */
function SpendingButtons({ scenario, onSaved }: { scenario: Scenario; onSaved: (review: Review[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <SpendingSheet
        scenario={scenario}
        onSaved={onSaved}
        trigger={<Button variant="outline">Edit {scenario.name} spending</Button>}
      />
      <SpendingSheet
        scenario={null}
        onSaved={onSaved}
        trigger={<Button variant="outline">Edit Household spending</Button>}
      />
    </div>
  );
}

/** The spending phases the projection used, with the editing beside them. */
function SpendingCard({
  p,
  scenario,
  onSaved,
}: {
  p: ScenarioProjection;
  scenario: Scenario;
  onSaved: (review: Review[]) => void;
}) {
  return (
    <section aria-label="Spending" data-testid="spending-used">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <CardTitle>Spending in {scenario.name}</CardTitle>
            <SpendingButtons scenario={scenario} onSaved={onSaved} />
          </div>
          <p className="text-sm text-muted-foreground">
            {scenario.spending_is_default
              ? `${scenario.name} states no spending phases of its own: it uses the Household default list.`
              : `${scenario.name} states its own spending phases; they replace the Household default list as a whole.`}
          </p>
        </CardHeader>
        <CardContent>
          <SpendingPhasesUsed
            spending={p.spending}
            referenceAge={p.reference_member.age_now}
            years={p.years}
            scenarioName={scenario.name}
          />
        </CardContent>
      </Card>
    </section>
  );
}

/** Each Assumption a projection used, and whether the Scenario or the Household default set it. */
export function ProvenanceList({
  p,
  slots,
  scenarioName,
}: {
  p: ScenarioProjection;
  slots: Slot[];
  scenarioName: string;
}) {
  const byId = Object.fromEntries(slots.map((s) => [s.id, s]));
  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
      {p.provenance.map((x) => {
        const slot = byId[slotId(x.key, x.scope_ref)] ?? RETIRED_SLOTS[x.key];
        return (
          <li
            key={slotId(x.key, x.scope_ref)}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]"
          >
            <span>{slot?.label ?? x.key}</span>
            <span className="flex items-center gap-2">
              <span className="font-mono tabular-nums">
                {slot?.unit === "money" ? (
                  <Money amount={x.value} />
                ) : slot?.unit === "percent" ? (
                  `${x.value}%`
                ) : (
                  x.value
                )}
              </span>
              {x.layer === "scenario" ? (
                <Badge variant="info">{scenarioName}</Badge>
              ) : (
                <Badge variant="closed">Household default</Badge>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

const RATE_LABEL: Record<string, string> = {
  sg_rate: "Superannuation Guarantee rate",
  contributions_tax_rate: "Contributions tax rate",
};

/** The income streams a projection used as stored, and the Rule pack rates applied to employer super. */
export function IncomeUsedList({ p, scenarioName }: { p: ScenarioProjection; scenarioName: string }) {
  const used = p.income;
  if (!used) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="income-used">
        This run was saved before income streams were recorded, so it has none.
      </p>
    );
  }
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  const startsOn = (t: number) => p.rows[t]?.starts_on;
  return (
    <div className="flex flex-col gap-2" data-testid="income-used">
      <h3 className="text-sm font-medium">Income streams</h3>
      {used.streams.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          There are no income streams, so only the annual saving adds to the Position.{" "}
          <Link to="/income-streams" className="text-primary underline-offset-4 hover:underline">
            Add a salary
          </Link>
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
          {used.streams.map((s) => {
            const ended = s.until_year !== null && s.until_year <= s.from_year;
            const until = s.until_year === null || s.until_year >= p.years ? null : startsOn(s.until_year);
            return (
              <li key={s.id} className="px-3 py-[var(--cell-py)]" data-testid={`income-stream-${s.id}`}>
                <p className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <span className="font-medium">
                    {s.name} <span className="font-normal text-muted-foreground">({memberName(s.member_id)})</span>
                  </span>
                  <span>
                    <Money amount={s.take_home_annual} /> <span className="text-muted-foreground">take-home a year</span>
                  </span>
                </p>
                <p className="text-xs text-muted-foreground">
                  Gross <Money amount={s.gross_annual} /> a year. Real wage growth {s.real_wage_growth}%.{" "}
                  {ended
                    ? "Not paid: the Member has already retired."
                    : `Paid from ${startsOn(s.from_year) ?? "the start"} ${until ? `until ${until}` : "to the horizon"}.`}
                </p>
              </li>
            );
          })}
        </ul>
      )}
      {used.rates.length > 0 && (
        <>
          <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="income-rates">
            {used.rates.map((r) => (
              <li
                key={`${r.key}-${r.from_year}`}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]"
              >
                <span>
                  {RATE_LABEL[r.key] ?? r.key} <span className="text-muted-foreground">from {r.financial_year}</span>
                </span>
                <span className="flex items-center gap-2">
                  <span className="font-mono tabular-nums">{r.value}%</span>
                  {r.layer === "scenario" && <Badge variant="info">{scenarioName}</Badge>}
                  {r.layer === "household" && <Badge variant="closed">Household default</Badge>}
                </span>
              </li>
            ))}
          </ul>
          <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground" data-testid="income-rate-sources">
            {[...new Map(used.rates.map((r) => [r.key, r])).values()].map((r) => (
              <li key={r.key}>
                {RATE_LABEL[r.key] ?? r.key}: {r.source}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        Employer super is added to the owner&apos;s Superannuation on top of the salary: gross pay times the Super
        Guarantee rate, less the contributions tax. These rates come from the Rule pack and, like the amounts above,
        are kept with a saved run.
      </p>
    </div>
  );
}

const PAYER_LABEL: Record<string, string> = { own: "Paid by the household", super: "Paid from super" };

/** Group consecutive years that took their figures from the same published year. */
function carrySpans(years: NonNullable<ScenarioProjection["tax"]>["years"]) {
  const spans: { from: string; to: string; source: string; carried: boolean; eroded: number }[] = [];
  for (const y of years) {
    const last = spans[spans.length - 1];
    if (last && last.source === y.source_financial_year && last.carried === y.carried) {
      last.to = y.financial_year;
      last.eroded = Math.max(last.eroded, y.frozen_eroded);
    } else {
      spans.push({ from: y.financial_year, to: y.financial_year, source: y.source_financial_year, carried: y.carried, eroded: y.frozen_eroded });
    }
  }
  return spans;
}

/** How take-home pay and Division 293 were reached: the tax estimate behind a run, kept with a saved run. */
export function TaxUsedList({ p }: { p: ScenarioProjection }) {
  const tax = p.tax;
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  if (!tax) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="tax-used">
        Tax is off: take-home pay is each Income stream&apos;s stated amount. Switch on Tax in the projection in the
        Assumptions to estimate it.
      </p>
    );
  }
  const startsOn = (t: number | null) => (t === null ? undefined : p.rows[t]?.starts_on);
  return (
    <div className="flex flex-col gap-2" data-testid="tax-used">
      <h3 className="text-sm font-medium">Tax in the projection</h3>
      <p className="text-sm">
        Take-home pay is estimated by the tax engine (version {tax.tax_engine_version}, Rule pack {tax.rule_pack_version}),
        in place of the amounts stated on the Income streams.
      </p>
      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground" data-testid="tax-carry">
        {carrySpans(tax.years).map((s) => (
          <li key={`${s.from}-${s.source}`}>
            {s.from === s.to ? `FY ${s.from}` : `FY ${s.from} to ${s.to}`}{" "}
            {s.carried
              ? `uses the figures of FY ${s.source} carried forward. Rates and tax brackets are held; thresholds the pack marks frozen fall in real terms by inflation each year (${s.eroded} of them here by the last year).`
              : `uses the published figures of FY ${s.source}.`}
          </li>
        ))}
      </ul>
      <details className="rounded-lg border border-border bg-card text-sm" data-testid="tax-working">
        <summary className="cursor-pointer px-3 py-[var(--cell-py)] font-medium">Show each year&apos;s working</summary>
        <ul className="divide-y divide-border">
          {tax.years.flatMap((y) =>
            y.members.map((m) => (
              <li
                key={`${y.t}-${m.member_id}`}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]"
              >
                <span>
                  FY {y.financial_year} <span className="text-muted-foreground">({memberName(m.member_id)})</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  Gross <Money amount={m.gross} /> less tax <Money amount={m.tax} /> = take-home <Money amount={m.take_home} />
                </span>
              </li>
            )),
          )}
        </ul>
      </details>
      {tax.payments.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="div293-payments">
          {tax.payments.map((d) => {
            const paid = startsOn(d.paid_year);
            return (
              <li
                key={`${d.member_id}-${d.for_financial_year}-${d.estimated_from_year_0}`}
                className="px-3 py-[var(--cell-py)]"
              >
                <p className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <span className="font-medium">
                    Division 293, FY {d.for_financial_year}{" "}
                    <span className="font-normal text-muted-foreground">({memberName(d.member_id)})</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <Money amount={d.amount} />
                    <Badge variant={d.modelled ? "info" : "closed"}>{PAYER_LABEL[d.payer] ?? d.payer}</Badge>
                  </span>
                </p>
                <p className="text-xs text-muted-foreground">
                  {!d.modelled
                    ? "This run was computed before paying from super was modelled, so nothing was taken for it. "
                    : paid
                      ? d.payer === "super"
                        ? `Taken from ${memberName(d.member_id)}'s super in the year starting ${paid}, a year after the year it is for. `
                        : `Paid in the year starting ${paid}, a year after the year it is for. `
                      : "Falls after the horizon, so it is not paid. "}
                  {d.estimated_from_year_0 && "The year just ended, estimated from year 0 figures. "}
                  Taxable income <Money amount={d.taxable_income} /> plus low tax contributions{" "}
                  <Money amount={d.low_tax_contributions} /> is Division 293 income of <Money amount={d.div293_income} />, over the{" "}
                  <Money amount={d.threshold} /> threshold at {d.rate}%.
                </p>
              </li>
            );
          })}
        </ul>
      )}
      {tax.div293_unavailable && (
        <p className="text-xs text-muted-foreground">The Rule pack has no Division 293 figures, so none was estimated.</p>
      )}
      <p className="text-xs text-muted-foreground">
        Only salary is entered: deductions, HELP, other income, salary sacrifice and dependants are not modelled. Division 293
        counts taxable income and employer super only.
      </p>
    </div>
  );
}

/** Each Member's ages from the Rule pack, with the pack version and where each comes from. */
export function RulesUsed({ p, scenarioName }: { p: ScenarioProjection; scenarioName: string }) {
  const rules = p.rules;
  if (!rules) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="rules-used">
        This run was saved before Rule packs were recorded, so it has no ages.
      </p>
    );
  }
  const label = Object.fromEntries(rules.parameters.map((x) => [x.key, x.label]));
  return (
    <div className="flex flex-col gap-2" data-testid="rules-used">
      <h3 className="text-sm font-medium">
        Rule pack ages <span className="font-mono text-xs text-muted-foreground">{rules.pack_version}</span>
      </h3>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
        {p.members.map((m) => (
          <li key={m.id} className="px-3 py-[var(--cell-py)]" data-testid={`rules-member-${m.id}`}>
            <p className="font-medium">{m.name}</p>
            <ul className="mt-1 flex flex-col gap-1">
              {rules.values
                .filter((v) => v.member_id === m.id)
                .map((v) => (
                  <li key={v.key} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <span>{label[v.key] ?? v.key}</span>
                    <span className="flex items-center gap-2">
                      <span className="font-mono tabular-nums">{formatAge(v.years, v.months)}</span>
                      {v.layer === "scenario" && <Badge variant="info">{scenarioName}</Badge>}
                      {v.layer === "household" && <Badge variant="closed">Household default</Badge>}
                    </span>
                  </li>
                ))}
            </ul>
          </li>
        ))}
      </ul>
      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
        {rules.parameters.map((x) => (
          <li key={x.key}>
            {x.label}: {x.source}
          </li>
        ))}
        <li>These ages set when Superannuation can be drawn: see the Life events below.</li>
      </ul>
    </div>
  );
}

/** Each Member's Life events in date order, and when their Superannuation opens (ADR-0017). */
export function LifeEventsTimeline({ p, scenarioName }: { p: ScenarioProjection; scenarioName: string }) {
  const events = p.life_events;
  return (
    <div className="flex flex-col gap-2" data-testid="life-events">
      <h3 className="text-sm font-medium">Life events</h3>
      {!events && (
        <p className="text-sm text-muted-foreground">
          This run was saved before Life events were recorded, so it has no dates.
        </p>
      )}
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
        {p.members.map((m) => {
          const access = p.super_access.find((a) => a.member_id === m.id);
          const retirement = events?.find((e) => e.member_id === m.id && e.kind === "retirement");
          const gap = access && retirement ? access.year - retirement.year : 0;
          return (
            <li key={m.id} className="px-3 py-[var(--cell-py)]" data-testid={`life-events-member-${m.id}`}>
              <p className="font-medium">{m.name}</p>
              <ol className="mt-1 flex flex-col gap-1">
                {(events ?? [])
                  .filter((e) => e.member_id === m.id)
                  .map((e) => (
                    <li key={e.kind} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <span>
                        {LIFE_EVENT_LABEL[e.kind] ?? e.kind}{" "}
                        <span className="text-muted-foreground">at {formatAge(e.age_years, e.age_months)}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="font-mono tabular-nums">{e.on}</span>
                        {e.kind !== "retirement" && e.source === "scenario" && (
                          <Badge variant="info">{scenarioName}</Badge>
                        )}
                        {e.source === "household" && <Badge variant="closed">Household default</Badge>}
                      </span>
                    </li>
                  ))}
                {access && (
                  <li
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border pt-1"
                    data-testid={`super-opens-${m.id}`}
                  >
                    <span className="font-medium">Superannuation can be drawn from</span>
                    <span className="flex items-center gap-2">
                      <span className="font-mono tabular-nums">{access.on}</span>
                      {access.year >= p.years && <Badge variant="closed">After the horizon</Badge>}
                    </span>
                  </li>
                )}
              </ol>
              {gap > 0 && (
                <p className="mt-1 flex flex-wrap items-center gap-2 text-warning" data-testid={`super-gap-${m.id}`}>
                  <Badge variant="flagged">Gap</Badge>
                  {m.name} retires {gap} {gap === 1 ? "year" : "years"} before Superannuation can be drawn. Spending in
                  between comes from the other Categories.
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-muted-foreground">
        Superannuation opens at the later of retirement and preservation age, and at the latest at the unrestricted
        super age. Dates are birthdays; each takes effect from the first projection year starting on or after it.
      </p>
    </div>
  );
}

function AssumptionsUsed({
  p,
  slots,
  scenario,
  members,
  onSaved,
}: {
  p: ScenarioProjection;
  slots: Slot[];
  scenario: Scenario;
  members: Member[];
  onSaved: (review: Review[]) => void;
}) {
  return (
    <section aria-label="Assumptions used" data-testid="assumptions-used">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <CardTitle>Assumptions in {scenario.name}</CardTitle>
            <EditButtons scenario={scenario} members={members} onSaved={onSaved} />
          </div>
          <p className="text-sm text-muted-foreground">
            {scenario.override_count === 0
              ? `${scenario.name} states nothing of its own: every value is the Household default.`
              : `${scenario.name} states ${scenario.override_count} ${scenario.override_count === 1 ? "value" : "values"} of its own; the rest are the Household defaults.`}{" "}
            The starting rates are illustrative placeholders, not advice: change them to your own view.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <DrawdownOrderLine categories={p.categories} />
          <ProvenanceList p={p} slots={slots} scenarioName={scenario.name} />
          <IncomeUsedList p={p} scenarioName={scenario.name} />
          <TaxUsedList p={p} />
          <RulesUsed p={p} scenarioName={scenario.name} />
          <LifeEventsTimeline p={p} scenarioName={scenario.name} />
        </CardContent>
      </Card>
    </section>
  );
}

/** A Household default changed while Scenarios state their own value for it: ask for a review. */
function ReviewNotice({
  review,
  slots,
  scenarios,
  onDismiss,
}: {
  review: Review[];
  slots: Slot[];
  scenarios: Scenario[];
  onDismiss: () => void;
}) {
  const label = (r: Review) =>
    r.key === "spending_phases"
      ? "Household spending phases"
      : (slots.find((s) => s.id === slotId(r.key, r.scope_ref))?.label ?? r.key);
  const idOf = (name: string) => scenarios.find((s) => s.name === name)?.id;
  return (
    <div
      role="status"
      data-testid="review-notice"
      className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm"
    >
      <p className="font-medium text-warning">Review these Scenarios</p>
      <p className="mb-2 text-muted-foreground">
        Values are absolute, so a Scenario that states its own value did not move with the Household default.
      </p>
      <ul className="list-disc pl-5">
        {review.map((r) => (
          <li key={slotId(r.key, r.scope_ref)}>
            {label(r)}:{" "}
            {r.scenarios.map((name, i) => (
              <span key={name}>
                {i > 0 && ", "}
                <Link
                  to="/projection"
                  search={{ scenario: idOf(name) }}
                  className="text-primary underline-offset-4 hover:underline"
                >
                  {name}
                </Link>
              </span>
            ))}
          </li>
        ))}
      </ul>
      <Button variant="ghost" className="mt-2" onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  );
}

/** An Assumption has no value anywhere: say which, and offer the place to set it. */
function Incomplete({
  error,
  scenario,
  members,
  onSaved,
}: {
  error: unknown;
  scenario: Scenario;
  members: Member[];
  onSaved: (review: Review[]) => void;
}) {
  const messages =
    error instanceof ApiError && Object.keys(error.fields).length > 0 ? Object.values(error.fields) : [];
  // A salary with no spending phase from the start is a missing-value error on the spending plan.
  const needsWorkingYears = error instanceof ApiError && "spending_phases" in error.fields;
  return (
    <Card data-testid="incomplete">
      <CardHeader>
        <CardTitle>Assumptions are not complete</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {messages.length > 0 ? (
          <ul className="list-disc pl-5 text-sm">
            {messages.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        ) : (
          <GeneralError error={error} />
        )}
        {needsWorkingYears && (
          <p className="text-sm text-muted-foreground" data-testid="working-years-hint">
            To fix this, open the spending below and add a phase that starts at the projection start (choose
            &ldquo;The projection start&rdquo; with 0 years after). It is the household&apos;s spending while the
            salary is paid.
          </p>
        )}
        <EditButtons scenario={scenario} members={members} onSaved={onSaved} />
        <SpendingButtons scenario={scenario} onSaved={onSaved} />
      </CardContent>
    </Card>
  );
}

/** The Projection: pick a Scenario and see whether the funds last (ADR-0015, ADR-0016). */
export function ProjectionPage({
  search,
  onSelect,
}: {
  search: ProjectionSearch;
  onSelect: (scenarioId: number) => void;
}) {
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: fetchScenarios });
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Projection"
        description="Do the household's funds last? A Scenario's Assumptions run forward from today's Position."
      />
      <GeneralError error={household.error ?? scenarios.error} />
      {(household.isPending || scenarios.isPending) && <p className="text-sm text-muted-foreground">Loading…</p>}
      {household.data &&
        scenarios.data &&
        (household.data.members.length === 0 ? (
          <p className="text-sm text-muted-foreground">Add a Member on the Household page to run a projection.</p>
        ) : (
          <Runner
            members={household.data.members}
            scenarios={scenarios.data}
            selectedId={search.scenario}
            onSelect={onSelect}
          />
        ))}
    </div>
  );
}

function Runner({
  members,
  scenarios,
  selectedId,
  onSelect,
}: {
  members: Member[];
  scenarios: Scenario[];
  selectedId: number | undefined;
  onSelect: (scenarioId: number) => void;
}) {
  const [review, setReview] = useState<Review[]>([]);
  const [savedRun, setSavedRun] = useState<RunHeader | null>(null);
  const queryClient = useQueryClient();
  const selected =
    scenarios.find((s) => s.id === selectedId) ?? scenarios.find((s) => s.key === "likely") ?? scenarios[0];
  const result = useQuery({
    queryKey: ["projection", selected.id],
    queryFn: () => runScenario(selected.id),
    retry: false,
  });
  const slots = slotsFor(members);
  const saved = (found: Review[]) => {
    setReview(found);
    void queryClient.invalidateQueries({ queryKey: ["projection"] });
  };
  const p = result.data;
  return (
    <>
      <ScenarioSwitcher scenarios={scenarios} selected={selected} onAdded={(added) => onSelect(added.id)} />
      {review.length > 0 && (
        <ReviewNotice review={review} slots={slots} scenarios={scenarios} onDismiss={() => setReview([])} />
      )}
      {result.isPending && <p className="text-sm text-muted-foreground">Running…</p>}
      {result.error && <Incomplete error={result.error} scenario={selected} members={members} onSaved={saved} />}
      {p && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <SaveRunDialog scenario={selected} onSaved={setSavedRun} />
            <Link to="/projection/runs" className={buttonVariants({ variant: "outline" })}>
              Saved runs
            </Link>
          </div>
          {savedRun && (
            <div role="status" data-testid="run-saved" className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm">
              Run saved.{" "}
              <Link
                to="/projection/runs/$runId"
                params={{ runId: String(savedRun.id) }}
                className="text-primary underline-offset-4 hover:underline"
              >
                Open the saved run
              </Link>
            </div>
          )}
          <Headline p={p} />
          <SpendingCard p={p} scenario={selected} onSaved={saved} />
          <AssumptionsUsed p={p} slots={slots} scenario={selected} members={members} onSaved={saved} />
          <AgePensionCard p={p} />
          <Simplifications p={p} />
          <YearTable p={p} />
        </>
      )}
    </>
  );
}

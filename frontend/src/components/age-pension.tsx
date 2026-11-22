import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Money } from "@/components/ui/money";
import { CATEGORY_LABEL } from "@/lib/projection";
import type { ScenarioProjection } from "@/lib/scenarios";

type Used = NonNullable<ScenarioProjection["age_pension"]>;
type YearWorking = NonNullable<ScenarioProjection["rows"][number]["age_pension"]>;
type Row = ScenarioProjection["rows"][number];

const FACT_LABEL: Record<string, string> = {
  couple_status: "Couple status",
  homeowner: "Homeowner",
  principal_home: "Principal home",
};

const STATUS_TEXT: Record<string, string> = {
  needs_home_choice: "Choose the principal home on Household",
  not_modelled: "Not modelled for this household",
  no_figures: "No Age Pension figures in the Rule pack",
};

const BOUND_TEXT: Record<string, string> = {
  income: "The income test bound",
  assets: "The assets test bound",
  none: "Neither test reduced the maximum",
};

/** Rule keys that are percentages (tapers and deeming rates); every other key is a dollar amount. */
const isPercent = (key: string) => key.includes("taper") || key.includes(".rate_");

function RuleValue({ rule }: { rule: Used["rules"][number] }) {
  return isPercent(rule.key) ? (
    <span className="font-mono tabular-nums">{rule.value}%</span>
  ) : (
    <Money amount={rule.value} />
  );
}

function RuleRow({ rule }: { rule: Used["rules"][number] }) {
  return (
    <li className="flex flex-col gap-1 px-3 py-[var(--cell-py)]" data-testid={`age-pension-rule-${rule.key}`}>
      <p className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="font-mono text-xs break-all">{rule.key}</span>
        <RuleValue rule={rule} />
      </p>
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        FY {rule.financial_year}
        <Badge variant={rule.origin === "captured" ? "info" : "closed"}>
          {rule.origin === "captured" ? "Captured" : "Shipped"}
        </Badge>
        {rule.provisional && <Badge variant="flagged">Provisional</Badge>}
        {rule.provisional_note && <span>{rule.provisional_note}</span>}
      </p>
    </li>
  );
}

/** What the Age Pension calculation used: the facts, eligibility, assets left in or out, and the simplifications. */
export function AgePensionPanel({ p }: { p: ScenarioProjection }) {
  const used = p.age_pension;
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  if (!used) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="age-pension-used">
        This run was saved before the Age Pension was calculated, so it has none. Re-run with the current engine to
        include it.
      </p>
    );
  }
  const calculated = used.status === "calculated";
  const assessed = used.assets.filter((a) => a.role === "assessed");
  const exempt = used.assets.filter((a) => a.role !== "assessed");
  return (
    <div className="flex flex-col gap-2" data-testid="age-pension-used">
      {!calculated && (
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          <Badge variant="flagged">{STATUS_TEXT[used.status] ?? used.status}</Badge>
          No Age Pension is paid in this run.
        </p>
      )}
      {!calculated && (
        <ul className="list-disc pl-5 text-sm" data-testid="age-pension-reasons">
          {used.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="age-pension-facts">
        {used.facts.map((f) => (
          <li key={f.key} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]">
            <span>{FACT_LABEL[f.key] ?? f.key}</span>
            <span className="flex items-center gap-2">
              <span className="font-mono tabular-nums">{factValue(used, f)}</span>
              <Badge variant={f.source === "entered" ? "info" : "closed"}>{f.label}</Badge>
            </span>
          </li>
        ))}
      </ul>
      {used.eligibility.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="age-pension-eligibility">
          {used.eligibility.map((e) => (
            <li key={e.member_id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]">
              <span>
                {memberName(e.member_id)} <span className="text-muted-foreground">reaches Age Pension age</span>
              </span>
              <span className="flex items-center gap-2">
                <span className="font-mono tabular-nums">{e.on}</span>
                <span className="text-xs text-muted-foreground">paid from year {e.year + 1}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {calculated && (
        <>
          <AssetList title="Assessed" assets={assessed} testId="age-pension-assessed" />
          <AssetList title="Exempt" assets={exempt} testId="age-pension-exempt" note="Valued but not counted." />
          {used.left_out.length > 0 && (
            <div data-testid="age-pension-omitted" className="text-sm">
              <p className="font-medium text-warning">Left out (not counted)</p>
              <ul className="list-disc pl-5">
                {used.left_out.map((x) => (
                  <li key={x.account_id}>
                    {x.name}{" "}
                    <span className="text-muted-foreground">
                      ({CATEGORY_LABEL[x.category_key] ?? x.category_key},{" "}
                      {x.status === "no_valuation" ? "no Valuation" : "no FX rate"})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {used.rules.length > 0 && (
        <details className="rounded-lg border border-border bg-card text-sm" data-testid="age-pension-rules">
          <summary className="cursor-pointer px-3 py-[var(--cell-py)] font-medium">Show the Rule pack figures used</summary>
          <ul className="divide-y divide-border">
            {used.rules.map((r) => (
              <RuleRow key={`${r.key}-${r.from_year}`} rule={r} />
            ))}
          </ul>
        </details>
      )}
      {used.simplifications.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground" data-testid="age-pension-simplifications">
          {used.simplifications.map((s) => (
            <li key={s.key}>{s.text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function factValue(used: Used, f: Used["facts"][number]): string {
  if (f.key === "principal_home") return used.assets.find((a) => a.role === "exempt_home")?.name ?? f.value;
  if (f.key === "homeowner") return f.value === "yes" ? "Yes" : "No";
  return f.value;
}

function AssetList({
  title,
  assets,
  testId,
  note,
}: {
  title: string;
  assets: Used["assets"];
  testId: string;
  note?: string;
}) {
  if (assets.length === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
      <p className="text-sm font-medium">
        {title} Property and Vehicles
        {note && <span className="font-normal text-muted-foreground"> {note}</span>}
      </p>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
        {assets.map((a) => (
          <li key={a.account_id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]">
            <span>
              {a.name} <span className="text-xs text-muted-foreground">Valuation of {a.valued_on}</span>
            </span>
            <Money amount={a.value} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What a year pays in total to the Members, a year. */
export function agePensionPaid(row: Row): string | null {
  const w = row.age_pension;
  if (!w) return null;
  const cents = w.paid.reduce((sum, x) => sum + BigInt(x.amount.replace(".", "")), 0n);
  const text = cents.toString().padStart(3, "0");
  return `${text.slice(0, -2)}.${text.slice(-2)}`;
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-3 py-[var(--cell-py)]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Working({ p, row, w }: { p: ScenarioProjection; row: Row; w: YearWorking }) {
  const memberName = (id: number) => p.members.find((m) => m.id === id)?.name ?? "";
  // The rule values in force this year: the latest of each key that applies from this year or earlier.
  const inForce = new Map<string, Used["rules"][number]>();
  for (const r of [...(p.age_pension?.rules ?? [])].sort((a, b) => a.from_year - b.from_year)) {
    if (r.from_year <= row.t) inForce.set(r.key, r);
  }
  return (
    <div className="flex flex-col gap-3 text-sm" data-testid="age-pension-working">
      <p>
        <Badge variant="info">{BOUND_TEXT[w.bound] ?? w.bound}</Badge>
      </p>
      <h3 className="font-medium">Assets read ({w.couple ? "couple" : "single"}, {w.homeowner ? "homeowner" : "non-homeowner"})</h3>
      <dl className="divide-y divide-border rounded-lg border border-border bg-card">
        {w.categories.map((c) => (
          <Line key={c.category_key} label={CATEGORY_LABEL[c.category_key] ?? c.category_key}>
            <Money amount={c.amount} />
          </Line>
        ))}
        <Line label="Property (not the home)">
          <Money amount={w.property_assets} />
        </Line>
        <Line label="Vehicles">
          <Money amount={w.vehicle_assets} />
        </Line>
        <Line label="Assessable assets">
          <Money amount={w.assets} className="font-semibold" />
        </Line>
      </dl>
      <h3 className="font-medium">Income read, a year</h3>
      <dl className="divide-y divide-border rounded-lg border border-border bg-card">
        <Line label="Deemed on financial assets">
          <Money amount={w.deemed_income} />
        </Line>
        <Line label="Gross pay of Income streams">
          <Money amount={w.other_income} />
        </Line>
      </dl>
      <h3 className="font-medium">The tests, a year</h3>
      <dl className="divide-y divide-border rounded-lg border border-border bg-card">
        <Line label="Maximum">
          <Money amount={w.maximum} />
        </Line>
        <Line label="Income test pays">
          <Money amount={w.income_test} />
        </Line>
        <Line label="Assets test pays">
          <Money amount={w.assets_test} />
        </Line>
        <Line label="The lower, for the household">
          <Money amount={w.total} className="font-semibold" />
        </Line>
        {w.paid.map((x) => (
          <Line key={x.member_id} label={`Paid to ${memberName(x.member_id)}`}>
            <Money amount={x.amount} />
          </Line>
        ))}
      </dl>
      <h3 className="font-medium">Rule pack figures in force</h3>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card" data-testid="age-pension-working-rules">
        {[...inForce.values()].map((r) => (
          <RuleRow key={r.key} rule={r} />
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Assessed on the balances the year opens with, in today&apos;s dollars. The pension is credited to each
        Member&apos;s Cash before any drawdown.
      </p>
    </div>
  );
}

/** A year's pension with a way to open the working behind it. */
export function AgePensionCell({ p, row }: { p: ScenarioProjection; row: Row }) {
  const w = row.age_pension;
  const paid = agePensionPaid(row);
  if (!w || paid === null) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="flex items-center justify-end gap-2">
      <Money amount={paid} />
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="link" aria-label={`Age Pension working, year ${row.t + 1}`}>
            Working
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Age Pension, year {row.t + 1}</DialogTitle>
            <DialogDescription>
              The year starting {row.starts_on}: what the means test read and which test bound.
            </DialogDescription>
          </DialogHeader>
          <Working p={p} row={row} w={w} />
        </DialogContent>
      </Dialog>
    </span>
  );
}

/** The Age Pension card for run screens, beside the other "what was used" lists. */
export function AgePensionCard({ p }: { p: ScenarioProjection }) {
  return (
    <section aria-label="Age Pension" data-testid="age-pension-card">
      <Card>
        <CardHeader>
          <CardTitle>Age Pension</CardTitle>
          <p className="text-sm text-muted-foreground">
            Calculated each year from the Rule pack&apos;s figures and your Household facts, never entered. Open a
            year&apos;s working from the year-by-year table.
          </p>
        </CardHeader>
        <CardContent>
          <AgePensionPanel p={p} />
        </CardContent>
      </Card>
    </section>
  );
}

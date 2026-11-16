import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";

import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { GeneralError } from "@/components/form-fields";
import { TaxEstimateBody, ValueText } from "@/components/tax-estimate-body";
import { firstName } from "@/components/tax-estimate-view";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatWhen } from "@/lib/audit";
import { fetchHousehold } from "@/lib/household";
import {
  compareEstimates,
  fetchEstimates,
  fetchSavedEstimate,
  rerunEstimate,
  type TaxEstimateHeader,
} from "@/lib/tax-estimates";

const ESTIMATES_KEY = ["tax-estimates"];

function BackLink({ children = "Saved estimates" }: { children?: string }) {
  return (
    <Link to="/tax/estimates" className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}>
      <ArrowLeft size={16} aria-hidden />
      {children}
    </Link>
  );
}

/** What an estimate is of, in a phrase: the base, or a Tax what-if by name. */
const basis = (h: Pick<TaxEstimateHeader, "whatif_name">) => h.whatif_name ?? "The base (your records)";

function EngineBadge({ header }: { header: TaxEstimateHeader }) {
  return (
    <>
      v{header.engine_version}
      {!header.current_engine && (
        <Badge variant="stale" className="ml-2">
          Older engine
        </Badge>
      )}
    </>
  );
}

// --- the list ----------------------------------------------------------------------------

/** `/tax/estimates`: every saved estimate, newest first. Tick two of one year to compare them. */
export function TaxEstimatesPage({ fy, onYear }: { fy: number | undefined; onYear: (year: number | undefined) => void }) {
  const estimates = useQuery({ queryKey: [...ESTIMATES_KEY, fy ?? "all"], queryFn: () => fetchEstimates(fy) });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const zone = household.data?.time_zone;
  const navigate = useNavigate();
  const [picked, setPicked] = useState<number[]>([]);

  const chosen = (estimates.data ?? []).filter((e) => picked.includes(e.id));
  const sameYear = chosen.length === 2 && chosen[0].financial_year === chosen[1].financial_year;
  const hint =
    picked.length < 2
      ? "Tick two saved estimates of the same year to compare them."
      : chosen.length === 2 && !sameYear
        ? "Only estimates of the same financial year can be compared."
        : null;
  const toggle = (id: number) =>
    setPicked((now) => (now.includes(id) ? now.filter((x) => x !== id) : [...now, id].slice(-2)));

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <PageHeader
        title="Saved estimates"
        description="Tax estimates kept exactly as calculated: the totals they used, the Rule figures, the result and the engine. Open one to see how it was worked out."
        action={
          <Button
            disabled={!sameYear}
            onClick={() =>
              void navigate({
                to: "/tax/estimates/compare",
                search: { a: chosen[0].id, b: chosen[1].id },
              })
            }
          >
            Compare
          </Button>
        }
      />
      <label className="flex w-full max-w-xs flex-col gap-1 text-sm">
        Financial year
        <FinancialYearSelect value={fy} onChange={onYear} years={financialYears([fy])} allLabel="All years" />
      </label>
      {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
      <GeneralError error={estimates.error ?? household.error} />
      {(estimates.isPending || (household.isPending && !household.error)) && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {estimates.data && zone && estimates.data.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="no-estimates">
          Nothing saved yet. Use Save estimate on the{" "}
          <Link to="/tax" className="text-primary underline-offset-4 hover:underline">
            Tax estimate
          </Link>{" "}
          or on a{" "}
          <Link to="/tax/whatifs" className="text-primary underline-offset-4 hover:underline">
            Tax what-if
          </Link>
          .
        </p>
      )}
      {estimates.data && zone && estimates.data.length > 0 && (
        <Table aria-label="Saved estimates">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <span className="sr-only">Compare</span>
              </TableHead>
              <TableHead>Saved</TableHead>
              <TableHead>Label</TableHead>
              <TableHead>Year</TableHead>
              <TableHead>Of</TableHead>
              <TableHead>Engine</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {estimates.data.map((e) => (
              <TableRow key={e.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`Compare estimate ${e.id}`}
                    checked={picked.includes(e.id)}
                    onChange={() => toggle(e.id)}
                  />
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  <Link
                    to="/tax/estimates/$estimateId"
                    params={{ estimateId: String(e.id) }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {formatWhen(e.created_at, zone)}
                  </Link>
                </TableCell>
                <TableCell>{e.label ?? <span className="text-muted-foreground">No label</span>}</TableCell>
                <TableCell className="whitespace-nowrap">FY {e.financial_year_label}</TableCell>
                <TableCell>
                  {basis(e)}
                  {e.incomplete && (
                    <Badge variant="flagged" className="ml-2">
                      Incomplete
                    </Badge>
                  )}
                  {e.has_provisional && (
                    <Badge variant="flagged" className="ml-2">
                      Provisional
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  <EngineBadge header={e} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

// --- one saved estimate -------------------------------------------------------------------

/** `/tax/estimates/$estimateId`: a saved estimate, rendered from its stored snapshot alone. */
export function TaxEstimatePage({ estimateId }: { estimateId: string }) {
  const id = Number(estimateId);
  const saved = useQuery({
    queryKey: [...ESTIMATES_KEY, "detail", id],
    queryFn: () => fetchSavedEstimate(id),
    enabled: Number.isInteger(id),
    retry: false,
  });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const rerun = useMutation({
    mutationFn: () => rerunEstimate(id),
    onSuccess: async (made) => {
      await queryClient.invalidateQueries({ queryKey: ESTIMATES_KEY });
      void navigate({ to: "/tax/estimates/$estimateId", params: { estimateId: String(made.id) } });
    },
  });

  if (saved.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (saved.error || !saved.data) {
    return (
      <div role="alert" className="flex flex-col gap-3">
        <BackLink />
        <p className="text-destructive">Could not load this estimate: {saved.error?.message ?? "not found"}</p>
      </div>
    );
  }
  const { estimate: header, view, reproduces } = saved.data;
  const zone = household.data?.time_zone ?? "UTC";
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <BackLink />
      <PageHeader
        title={header.label ?? `Saved estimate ${header.id}`}
        description={`FY ${header.financial_year_label}, ${basis(header)}. Saved ${formatWhen(header.created_at, zone)}. Computed by engine v${header.engine_version}, Rule pack ${header.rule_pack_version}.`}
        action={
          !header.current_engine && (
            <Button onClick={() => rerun.mutate()} disabled={rerun.isPending}>
              Re-run with current engine
            </Button>
          )
        }
      />
      <GeneralError error={rerun.error} />
      {!header.current_engine && (
        <div role="status" data-testid="engine-notice" className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm">
          <p className="font-medium text-warning">Computed by an older engine</p>
          <p className="text-muted-foreground">
            This is exactly what engine v{header.engine_version} computed, and it is shown unchanged. Re-running
            calculates the same inputs with the current engine and saves a new estimate; this one stays.
          </p>
        </div>
      )}
      {reproduces === true && (
        <p role="status" data-testid="reproduces" className="text-sm text-muted-foreground">
          Calculating the saved inputs again gives exactly this result.
        </p>
      )}
      {view.rerun_of !== null && (
        <p className="text-sm text-muted-foreground">
          This re-ran{" "}
          <Link
            to="/tax/estimates/$estimateId"
            params={{ estimateId: String(view.rerun_of) }}
            className="text-primary underline-offset-4 hover:underline"
          >
            estimate {view.rerun_of}
          </Link>{" "}
          with the current engine.
        </p>
      )}
      <TaxEstimateBody view={view} />
    </div>
  );
}

// --- comparing two ------------------------------------------------------------------------

function Delta({ amount }: { amount: string }) {
  const zero = /^-?0+(\.0+)?$/.test(amount);
  return (
    <span className={zero ? "text-muted-foreground" : undefined}>
      {!amount.startsWith("-") && !zero && <span aria-hidden>+</span>}
      <Money amount={amount} />
      <span className="sr-only">{zero ? " no change" : amount.startsWith("-") ? " less" : " more"}</span>
    </span>
  );
}

const TIGHT = "px-1.5 text-xs sm:px-[var(--cell-px)] sm:text-sm";

/** `/tax/estimates/compare?a=1&b=2`: per-label and per-result deltas, b less a. */
export function TaxComparePage({ a, b }: { a: number | undefined; b: number | undefined }) {
  const compared = useQuery({
    queryKey: [...ESTIMATES_KEY, "compare", a, b],
    queryFn: () => compareEstimates(a ?? 0, b ?? 0),
    enabled: a !== undefined && b !== undefined,
    retry: false,
  });
  if (a === undefined || b === undefined) {
    return (
      <div className="flex flex-col gap-3">
        <BackLink />
        <p className="text-sm text-muted-foreground">Choose two saved estimates to compare on Saved estimates.</p>
      </div>
    );
  }
  if (compared.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (compared.error || !compared.data) {
    return (
      <div role="alert" className="flex flex-col gap-3">
        <BackLink />
        <p className="text-destructive">Could not compare: {compared.error?.message ?? "not found"}</p>
      </div>
    );
  }
  const c = compared.data;
  const name = (id: number) => c.members.find((m) => m.member_id === id)?.name ?? "";
  const groups: { key: string; title: string; member: number | null }[] = [
    { key: "household", title: "Household", member: null },
    ...c.members.map((m) => ({ key: String(m.member_id), title: firstName(m.name), member: m.member_id })),
  ];
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <BackLink />
      <PageHeader
        title={`Compare FY ${c.financial_year_label}`}
        description="The change from the first estimate to the second: second less first. A negative net outcome change means more tax payable or less refund."
      />
      <div className="grid gap-3 sm:grid-cols-2">
        {[
          { tag: "First", h: c.a },
          { tag: "Second", h: c.b },
        ].map(({ tag, h }) => (
          <Card key={tag}>
            <CardHeader>
              <CardTitle>
                {tag}:{" "}
                <Link
                  to="/tax/estimates/$estimateId"
                  params={{ estimateId: String(h.id) }}
                  className="text-primary underline-offset-4 hover:underline"
                >
                  {h.label ?? `Estimate ${h.id}`}
                </Link>
              </CardTitle>
              <p className="text-sm text-muted-foreground">
                {basis(h)}. Engine <EngineBadge header={h} />
              </p>
            </CardHeader>
          </Card>
        ))}
      </div>
      {!c.same_engine && (
        <p role="status" data-testid="engine-differs" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          These were computed by different engines (v{c.a.engine_version} and v{c.b.engine_version}), so part of
          any change may be the engine, not your figures.
        </p>
      )}
      {c.rule_pack_differs && (
        <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          The two used different Rule figures, so part of any change may be the rules, not your figures.
        </p>
      )}
      {c.dependants_a !== c.dependants_b && (
        <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          They count different numbers of dependent children ({c.dependants_a} and {c.dependants_b}).
        </p>
      )}

      <Card data-testid="label-deltas">
        <CardHeader>
          <CardTitle>What changed in the totals</CardTitle>
        </CardHeader>
        <CardContent>
          {c.labels.length === 0 ? (
            <p className="text-sm text-muted-foreground">The two used the same Label totals.</p>
          ) : (
            <Table aria-label="Label totals that differ">
              <TableHeader>
                <TableRow>
                  <TableHead>Total</TableHead>
                  <TableHead className="text-right">First</TableHead>
                  <TableHead className="text-right">Second</TableHead>
                  <TableHead className="text-right">Change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {c.labels.map((l) => (
                  <TableRow key={`${l.member_id}-${l.label_key}`}>
                    <TableCell className={TIGHT}>
                      {firstName(name(l.member_id))}: {l.label_name}
                    </TableCell>
                    <TableCell className={`${TIGHT} text-right`}>
                      <ValueText value={l.a} kind={l.kind} />
                    </TableCell>
                    <TableCell className={`${TIGHT} text-right`}>
                      <ValueText value={l.b} kind={l.kind} />
                    </TableCell>
                    <TableCell className={`${TIGHT} text-right`}>
                      {l.delta === null ? (
                        "Changed"
                      ) : l.kind === "days" ? (
                        `${(l.delta.days ?? 0) > 0 ? "+" : ""}${l.delta.days} days`
                      ) : (
                        <Delta amount={l.delta.amount ?? "0.00"} />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {groups.map((g) => (
        <Card key={g.key} data-testid={`result-deltas-${g.key}`}>
          <CardHeader>
            <CardTitle>{g.title}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table aria-label={`${g.title} result`}>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <span className="sr-only">Item</span>
                  </TableHead>
                  <TableHead className="text-right">First</TableHead>
                  <TableHead className="text-right">Second</TableHead>
                  <TableHead className="text-right">Change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {c.results
                  .filter((r) => r.member_id === g.member)
                  .map((r) => (
                    <TableRow key={r.key} className={r.key === "net_outcome" ? "font-semibold" : undefined}>
                      <TableCell className={`${TIGHT} w-20 sm:w-auto`}>{r.name}</TableCell>
                      <TableCell className={`${TIGHT} text-right`}>
                        <Money amount={r.a} />
                      </TableCell>
                      <TableCell className={`${TIGHT} text-right`}>
                        <Money amount={r.b} />
                      </TableCell>
                      <TableCell className={`${TIGHT} text-right`}>
                        <Delta amount={r.delta} />
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

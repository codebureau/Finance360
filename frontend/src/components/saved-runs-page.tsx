import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { AgePensionCard } from "@/components/age-pension";
import { GeneralError } from "@/components/form-fields";
import { DrawdownOrderLine } from "@/components/assumptions-editor";
import {
  Headline,
  IncomeUsedList,
  TaxUsedList,
  LifeEventsTimeline,
  ProvenanceList,
  RulesUsed,
  Simplifications,
  YearTable,
} from "@/components/projection-page";
import { SpendingPhasesUsed } from "@/components/spending-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatWhen } from "@/lib/audit";
import { fetchHousehold } from "@/lib/household";
import { fetchRuns, fetchSavedRun, rerunRun, runResult } from "@/lib/projection-runs";
import { slotsFor } from "@/lib/scenarios";

function BackLink({ to, children }: { to: "/projection" | "/projection/runs"; children: string }) {
  return (
    <Link to={to} className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft size={14} />
      {children}
    </Link>
  );
}

/** Every saved run, newest first: the headline only, never the stored results. */
export function SavedRunsPage() {
  const runs = useQuery({ queryKey: ["projection-runs"], queryFn: fetchRuns });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const zone = household.data?.time_zone;

  return (
    <div className="flex flex-col gap-4">
      <BackLink to="/projection">Projection</BackLink>
      <PageHeader
        title="Saved runs"
        description="Projections kept exactly as computed. Open one to see its inputs, result and engine version."
      />
      <GeneralError error={runs.error ?? household.error} />
      {(runs.isPending || (household.isPending && !household.error)) && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {runs.data && zone && runs.data.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="no-runs">
          Nothing saved yet. Use Save run on the{" "}
          <Link to="/projection" className="text-primary underline-offset-4 hover:underline">
            Projection
          </Link>{" "}
          page.
        </p>
      )}
      {runs.data && zone && runs.data.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Saved</TableHead>
              <TableHead>Label</TableHead>
              <TableHead>Scenario</TableHead>
              <TableHead>Result</TableHead>
              <TableHead>Engine</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="whitespace-nowrap">
                  <Link
                    to="/projection/runs/$runId"
                    params={{ runId: String(r.id) }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {formatWhen(r.created_at, zone)}
                  </Link>
                </TableCell>
                <TableCell>{r.label ?? <span className="text-muted-foreground">No label</span>}</TableCell>
                <TableCell>{r.scenario_name}</TableCell>
                <TableCell>
                  <Badge variant={r.lasts_beyond_horizon ? "ok" : "flagged"}>{runResult(r)}</Badge>
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  v{r.engine_version}
                  {!r.current_engine && (
                    <Badge variant="stale" className="ml-2">
                      Older engine
                    </Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

/** One saved run, rendered from its stored snapshot alone. */
export function SavedRunPage({ runId }: { runId: string }) {
  const id = Number(runId);
  const run = useQuery({
    queryKey: ["projection-run", id],
    queryFn: () => fetchSavedRun(id),
    enabled: Number.isInteger(id),
    retry: false,
  });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const rerun = useMutation({
    mutationFn: () => rerunRun(id),
    onSuccess: async (made) => {
      await queryClient.invalidateQueries({ queryKey: ["projection-runs"] });
      void navigate({ to: "/projection/runs/$runId", params: { runId: String(made.id) } });
    },
  });

  if (run.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (run.error || !run.data) {
    return (
      <div role="alert" className="flex flex-col gap-3">
        <BackLink to="/projection/runs">Saved runs</BackLink>
        <p className="text-destructive">Could not load this run: {run.error?.message ?? "not found"}</p>
      </div>
    );
  }
  const p = run.data;
  const header = p.run;
  const zone = household.data?.time_zone ?? "UTC";
  const slots = slotsFor(p.members);

  return (
    <div className="flex flex-col gap-4">
      <BackLink to="/projection/runs">Saved runs</BackLink>
      <PageHeader
        title={header.label ?? `Saved run ${header.id}`}
        description={`${header.scenario_name}, saved ${formatWhen(header.created_at, zone)}. Computed by engine v${header.engine_version}.`}
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
            projects the same inputs with the current engine and saves a new run; this one stays.
          </p>
        </div>
      )}
      <Headline p={p} />
      <section aria-label="Assumptions used" data-testid="assumptions-used">
        <Card>
          <CardHeader>
            <CardTitle>Assumptions used in this run</CardTitle>
            <p className="text-sm text-muted-foreground">
              The values {header.scenario_name} resolved to when this run was saved. Changing an Assumption later does
              not change them.
            </p>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <DrawdownOrderLine categories={p.categories} />
            <SpendingPhasesUsed
              spending={p.spending}
              referenceAge={p.reference_member.age_now}
              years={p.years}
              scenarioName={header.scenario_name}
            />
            <ProvenanceList p={p} slots={slots} scenarioName={header.scenario_name} />
            <IncomeUsedList p={p} scenarioName={header.scenario_name} />
            <TaxUsedList p={p} />
            <RulesUsed p={p} scenarioName={header.scenario_name} />
            <LifeEventsTimeline p={p} scenarioName={header.scenario_name} />
          </CardContent>
        </Card>
      </section>
      <AgePensionCard p={p} />
      <Simplifications p={p} />
      <YearTable p={p} />
    </div>
  );
}

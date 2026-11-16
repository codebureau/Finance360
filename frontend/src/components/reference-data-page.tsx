import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { FetchNowButton } from "@/components/fetch-now-button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ago } from "@/lib/backup";
import {
  countsText,
  DATASET_LABEL,
  fetchRuns,
  fetchStatus,
  isRunning,
  runBadge,
  RUNS_SHOWN,
  TRIGGER_LABEL,
  type DatasetStatus,
  type FetchRun,
} from "@/lib/fetch";

const SETTING: Record<string, string> = {
  prices: "FINANCE360_PRICE_PROVIDER",
  fx: "FINANCE360_FX_PROVIDER",
};
const NOUN: Record<string, string> = { prices: "Prices", fx: "FX rates" };

function RunLine({ label, run, noun }: { label: string; run: FetchRun | null; noun: string }) {
  if (!run) {
    return (
      <>
        <dt className="text-muted-foreground">{label}</dt>
        <dd>None yet</dd>
      </>
    );
  }
  const badge = runBadge(run.status);
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="flex flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2">
          <Link
            to="/reference-data/runs/$runId"
            params={{ runId: String(run.id) }}
            className="hover:underline"
            title={`${run.started_at} UTC`}
          >
            {ago(run.finished_at ?? run.started_at)}
          </Link>
          <Badge variant={badge.variant}>{badge.label}</Badge>
          <span className="text-muted-foreground">{countsText(run, noun)}</span>
        </span>
        {run.error && label === "Last error" && (
          <span className="text-destructive" role="status">
            {run.error}
          </span>
        )}
      </dd>
    </>
  );
}

function DatasetCard({ data, fetchTime }: { data: DatasetStatus; fetchTime: string }) {
  const noun = NOUN[data.dataset];
  return (
    <Card aria-label={data.label}>
      <CardHeader>
        <CardTitle>{data.label}</CardTitle>
        <p className="text-sm text-muted-foreground">
          {data.configured
            ? `Fetched daily at ${fetchTime} (Household time) from ${data.provider}.`
            : `Not set up: set ${SETTING[data.dataset]} to fetch ${noun}. Until then, enter them by hand.`}
        </p>
      </CardHeader>
      <CardContent>
        {data.configured && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <RunLine label="Last success" run={data.last_success} noun={noun} />
            <RunLine label="Last error" run={data.last_error} noun={noun} />
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: FetchRun }) {
  const navigate = useNavigate();
  const badge = runBadge(run.status);
  const open = () => void navigate({ to: "/reference-data/runs/$runId", params: { runId: String(run.id) } });
  const noun = NOUN[run.dataset];
  return (
    <TableRow className="cursor-pointer" onClick={open} aria-label={`${DATASET_LABEL[run.dataset]} run ${run.id}`}>
      <TableCell>
        <Link
          to="/reference-data/runs/$runId"
          params={{ runId: String(run.id) }}
          className="font-medium hover:underline"
          title={`${run.started_at} UTC`}
          onClick={(e) => e.stopPropagation()}
        >
          {DATASET_LABEL[run.dataset]}
        </Link>
        <p className="text-xs text-muted-foreground md:hidden">
          {ago(run.started_at)} · {TRIGGER_LABEL[run.trigger]} · {countsText(run, noun)}
        </p>
      </TableCell>
      <TableCell className="hidden whitespace-nowrap md:table-cell" title={`${run.started_at} UTC`}>
        {ago(run.started_at)}
      </TableCell>
      <TableCell className="hidden md:table-cell">{TRIGGER_LABEL[run.trigger]}</TableCell>
      <TableCell className="hidden md:table-cell">{countsText(run, noun)}</TableCell>
      <TableCell>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </TableCell>
    </TableRow>
  );
}

/** Reference data: the daily fetch of Prices and FX rates, its last success and last error. */
export function ReferenceDataPage() {
  const status = useQuery({
    queryKey: ["fetch-status"],
    queryFn: fetchStatus,
    refetchInterval: (query) => (isRunning(query.state.data) ? 2000 : false),
  });
  const runs = useQuery({
    queryKey: ["fetch-runs"],
    queryFn: () => fetchRuns(RUNS_SHOWN),
    refetchInterval: () => (isRunning(status.data) ? 2000 : false),
  });
  const stale = status.data?.stale_instruments ?? [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Reference data"
        description="Prices and FX rates fetched from outside providers. A Price or FX rate you enter yourself always wins over a fetched one for the same date."
        action={<FetchNowButton />}
      />
      {status.isPending && <p className="text-muted-foreground">Loading…</p>}
      {status.error && (
        <p className="text-destructive">Could not load the fetch status: {status.error.message}</p>
      )}
      {status.data?.sample_data && (
        <p className="rounded-md border border-info/30 bg-info/10 p-3 text-sm" role="note">
          <Badge variant="info">Sample data</Badge> This instance fetches invented data with no network, so
          it can be tried safely.
        </p>
      )}
      {status.data && (
        <div className="grid gap-4 md:grid-cols-2">
          {status.data.datasets.map((d) => (
            <DatasetCard key={d.dataset} data={d} fetchTime={status.data.fetch_time} />
          ))}
        </div>
      )}
      {status.data && status.data.datasets.some((d) => d.configured) && stale.length > 0 && (
        <Card aria-label="Stale Prices">
          <CardHeader>
            <CardTitle>
              {stale.length} {stale.length === 1 ? "Instrument has" : "Instruments have"} no recent Price
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              Newest Price older than {status.data.stale_after_days} days, or none at all.
            </p>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-1 text-sm">
              {stale.map((s) => (
                <li key={s.instrument_id} className="flex flex-wrap items-center gap-2">
                  <Link
                    to="/instruments/$instrumentId"
                    params={{ instrumentId: String(s.instrument_id) }}
                    className="hover:underline"
                  >
                    {s.name}
                  </Link>
                  <Badge variant="stale">{s.latest_as_at ? `Last Price ${s.latest_as_at}` : "No Price yet"}</Badge>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      <section aria-label="Recent runs" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Recent runs</h2>
        {runs.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Nothing has been fetched yet. Press Fetch now, or wait for the daily run.
          </p>
        )}
        {runs.data && runs.data.length > 0 && (
          <>
            <Table aria-label="Fetch runs">
              <TableHeader>
                <TableRow>
                  <TableHead>Data</TableHead>
                  <TableHead className="hidden md:table-cell">Started</TableHead>
                  <TableHead className="hidden md:table-cell">How</TableHead>
                  <TableHead className="hidden md:table-cell">Result</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data.map((run) => (
                  <RunRow key={run.id} run={run} />
                ))}
              </TableBody>
            </Table>
            <p className="text-xs text-muted-foreground">Showing the latest {RUNS_SHOWN} runs.</p>
          </>
        )}
      </section>
    </div>
  );
}

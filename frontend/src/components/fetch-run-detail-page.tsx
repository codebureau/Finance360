import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  countsText,
  DATASET_LABEL,
  fetchRun,
  outcomeBadge,
  runBadge,
  TRIGGER_LABEL,
} from "@/lib/fetch";

function BackLink() {
  return (
    <Link
      to="/reference-data"
      className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft size={14} />
      Reference data
    </Link>
  );
}

/** One fetch run: its outcome, and what happened to each Instrument or currency. */
export function FetchRunDetailPage({ runId }: { runId: string }) {
  const id = Number(runId);
  const run = useQuery({
    queryKey: ["fetch-run", id],
    queryFn: () => fetchRun(id),
    enabled: Number.isInteger(id),
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });

  if (run.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (run.error || !run.data) {
    return (
      <div role="alert" className="flex flex-col gap-3">
        <BackLink />
        <p className="text-destructive">Could not load this run: {run.error?.message ?? "not found"}</p>
      </div>
    );
  }
  const r = run.data;
  const badge = runBadge(r.status);
  const noun = r.dataset === "prices" ? "Prices" : "FX rates";
  const subject = r.dataset === "prices" ? "Instrument" : "Currency";

  return (
    <div className="flex flex-col gap-4">
      <BackLink />
      <PageHeader
        title={`${DATASET_LABEL[r.dataset]} run`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant={badge.variant}>{badge.label}</Badge>
            <span>{countsText(r, noun)}</span>
          </span>
        }
      />
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Started</dt>
        <dd>{r.started_at.slice(0, 19).replace("T", " ")} UTC</dd>
        <dt className="text-muted-foreground">Finished</dt>
        <dd>{r.finished_at ? `${r.finished_at.slice(0, 19).replace("T", " ")} UTC` : "Still running"}</dd>
        <dt className="text-muted-foreground">How</dt>
        <dd>{TRIGGER_LABEL[r.trigger]}</dd>
        <dt className="text-muted-foreground">Provider</dt>
        <dd>{r.provider}</dd>
        {r.error && (
          <>
            <dt className="text-muted-foreground">Error</dt>
            <dd className="text-destructive">{r.error}</dd>
          </>
        )}
      </dl>
      {r.results.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {r.status === "running" ? "Waiting for the first result…" : `No ${subject.toLowerCase()}s were fetched.`}
        </p>
      ) : (
        <Table aria-label="Results">
          <TableHeader>
            <TableRow>
              <TableHead>{subject}</TableHead>
              <TableHead className="hidden md:table-cell">Symbol</TableHead>
              <TableHead>Result</TableHead>
              <TableHead className="text-right">New</TableHead>
              <TableHead className="hidden text-right md:table-cell">Revised</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {r.results.map((t) => {
              const o = outcomeBadge(t.outcome);
              return (
                <TableRow key={t.target}>
                  <TableCell>
                    <span className="font-medium">{t.target}</span>
                    {t.message && (
                      <p className={t.outcome === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
                        {t.message}
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="hidden font-mono md:table-cell">{t.symbol ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant={o.variant}>{o.label}</Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{t.added}</TableCell>
                  <TableCell className="hidden text-right font-mono tabular-nums md:table-cell">
                    {t.revised}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

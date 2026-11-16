import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatWhen } from "@/lib/audit";
import { AI_LOG_KEY, costText, fetchAiLog, outcomeBadge, type AiLogEntry } from "@/lib/ask";
import { fetchHousehold } from "@/lib/household";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</h3>
      {children}
    </div>
  );
}

/** Text exactly as stored: tokens and all, so what is read here is what left (or came back to) this instance. */
function Verbatim({ text }: { text: string }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-md border border-border bg-muted p-3 font-mono text-xs break-words whitespace-pre-wrap">
      {text}
    </pre>
  );
}

function EntrySheet({ entry, timeZone, onClose }: { entry: AiLogEntry | undefined; timeZone: string; onClose: () => void }) {
  return (
    <Sheet open={entry !== undefined} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="sm:max-w-xl">
        {entry && (
          <>
            <SheetTitle className="text-lg font-semibold">{entry.why}</SheetTitle>
            <SheetDescription className="text-sm text-muted-foreground">
              {formatWhen(entry.occurred_at, timeZone)} · {entry.provider} · {entry.model}
            </SheetDescription>
            <div className="flex flex-col gap-4">
              <Field label="Sent">
                <p className="text-xs text-muted-foreground">
                  The question and the wording of What&apos;s due, as they left Finance360. A placeholder such as
                  [MEMBER_1] stands for a name that was removed.
                </p>
                <Verbatim text={entry.sent} />
              </Field>
              <Field label="Public parts">
                <p className="text-xs text-muted-foreground">
                  Product text sent as it is, such as the Guide: listed by name and fingerprint, not repeated here.
                </p>
                <Verbatim text={entry.public_parts} />
              </Field>
              <Field label="Received">
                {entry.received ? (
                  <Verbatim text={entry.received} />
                ) : (
                  <p className="text-sm text-muted-foreground">Nothing came back.</p>
                )}
              </Field>
              <p className="text-sm text-muted-foreground">
                {entry.input_tokens} tokens in, {entry.output_tokens} out, {entry.cache_read_tokens} read from cache.
                Cost {costText(entry.cost_usd_micros)}
                {entry.cost_estimated ? " (estimated)" : ""}.
              </p>
            </div>
            <div className="flex justify-end">
              <SheetClose asChild>
                <Button variant="outline">Cancel</Button>
              </SheetClose>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** The AI log (ADR-0021 section 7): every call, newest first, read-only. */
export function AiLogPage({ entry, onEntry }: { entry: number | undefined; onEntry: (id: number | undefined) => void }) {
  const log = useQuery({ queryKey: AI_LOG_KEY, queryFn: () => fetchAiLog() });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const timeZone = household.data?.time_zone ?? DEFAULT_TIME_ZONE;
  const rows = log.data ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Link to="/settings" className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft size={14} aria-hidden />
        Settings
      </Link>
      <PageHeader
        title="AI log"
        description={
          <>
            Every question sent to the AI provider, exactly as it left Finance360, and what came back. It is read-only.{" "}
            <Link to="/guide/ask" className="text-primary underline underline-offset-4">
              About Ask
            </Link>
          </>
        }
      />
      {log.isPending && <p className="text-sm text-muted-foreground">Loading&hellip;</p>}
      {log.isError && (
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load the AI log.
        </p>
      )}
      {log.data && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">Nothing yet. A question asked in the Ask panel appears here.</p>
      )}
      {rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead className="hidden sm:table-cell">Model</TableHead>
              <TableHead className="hidden sm:table-cell">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              const badge = outcomeBadge(r.outcome);
              return (
                <TableRow key={r.id}>
                  <TableCell>
                    <Link
                      to="/settings/ai-log"
                      search={{ entry: r.id }}
                      className="text-primary underline underline-offset-4"
                    >
                      {formatWhen(r.occurred_at, timeZone)}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge variant={badge.variant}>{badge.label}</Badge>
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">{r.model}</TableCell>
                  <TableCell className="hidden sm:table-cell">
                    {costText(r.cost_usd_micros)}
                    {r.cost_estimated ? " (estimated)" : ""}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      <EntrySheet entry={rows.find((r) => r.id === entry)} timeZone={timeZone} onClose={() => onEntry(undefined)} />
    </div>
  );
}

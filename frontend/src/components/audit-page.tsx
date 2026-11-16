import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { GeneralError, inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  AUTH_KINDS,
  ENTITY_TYPES,
  PAGE_SIZE,
  fetchAudit,
  fetchAuthEvents,
  formatWhen,
  type AuditEntry,
  type AuditSearch,
  type AuthEntry,
} from "@/lib/audit";
import { fetchHousehold } from "@/lib/household";
import { cn } from "@/lib/utils";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";

type Update = (prev: AuditSearch) => AuditSearch;
type Props = { search: AuditSearch; onSearch: (update: Update) => void };

/** Set one param (or clear it with ""), going back to the first page. */
const withParam = (key: keyof AuditSearch, value: string): Update => (search) => {
  const next = { ...search, offset: undefined, [key]: value || undefined };
  return Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined)) as AuditSearch;
};

function Pager({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  if (total === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Button variant="outline" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>
        Newer
      </Button>
      <Button variant="outline" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>
        Older
      </Button>
      <span className="text-muted-foreground">
        {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
      </span>
    </div>
  );
}

function DateFilters({ search, onSearch }: Props) {
  return (
    <>
      <label className="flex flex-col gap-1 text-sm">
        From
        <input
          className={inputClass}
          type="date"
          value={search.date_from ?? ""}
          onChange={(e) => onSearch(withParam("date_from", e.target.value))}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        To
        <input
          className={inputClass}
          type="date"
          value={search.date_to ?? ""}
          onChange={(e) => onSearch(withParam("date_to", e.target.value))}
        />
      </label>
    </>
  );
}

function EntryRow({ entry, timeZone }: { entry: AuditEntry; timeZone: string }) {
  return (
    <TableRow data-entity={entry.entity_type} className="align-top">
      <TableCell className="w-32 text-xs whitespace-normal text-muted-foreground sm:w-48">
        <div className="text-foreground">{formatWhen(entry.occurred_at, timeZone)}</div>
        {entry.actor} · {entry.source}
      </TableCell>
      <TableCell className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="info">{entry.action}</Badge>
          <span className="font-medium break-words">{entry.summary}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          {entry.entity_type} #{entry.entity_id}
        </p>
        {entry.changes.length > 0 && (
          <ul aria-label="Changes" className="mt-1 flex flex-col gap-0.5 text-sm">
            {entry.changes.map((c) => (
              <li key={c.field} className="break-words">
                <span className="text-muted-foreground">{c.field}:</span>{" "}
                {c.before !== null && <span className="line-through">{c.before}</span>}
                {c.before !== null && c.after !== null && " → "}
                {c.after !== null ? <span>{c.after}</span> : <span className="italic">removed</span>}
              </li>
            ))}
          </ul>
        )}
      </TableCell>
    </TableRow>
  );
}

function ChangesView({ search, onSearch, timeZone }: Props & { timeZone: string }) {
  const offset = search.offset ?? 0;
  const filter = {
    entity_type: search.entity_type,
    entity_id: search.entity_id,
    account_id: search.account_id,
    date_from: search.date_from,
    date_to: search.date_to,
  };
  const trail = useQuery({ queryKey: ["audit", filter, offset], queryFn: () => fetchAudit(filter, offset) });
  const scoped = search.account_id !== undefined || search.entity_id !== undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Type
          <select
            className={inputClass}
            value={search.entity_type ?? ""}
            onChange={(e) => onSearch(withParam("entity_type", e.target.value))}
          >
            <option value="">All types</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <DateFilters search={search} onSearch={onSearch} />
      </div>
      {scoped && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          Showing the trail of{" "}
          {search.account_id !== undefined
            ? `Account #${search.account_id} and its Valuations`
            : `${search.entity_type} #${search.entity_id}`}
          .
          <Link
            to="/audit"
            search={{ date_from: search.date_from, date_to: search.date_to }}
            className={buttonVariants({ variant: "outline" })}
          >
            Show everything
          </Link>
        </p>
      )}
      <GeneralError error={trail.error} />
      {trail.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {trail.data && trail.data.total === 0 && (
        <p className="text-sm text-muted-foreground">No changes match these filters.</p>
      )}
      {trail.data && trail.data.total > 0 && (
        <Table aria-label="Audit trail entries">
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trail.data.items.map((e) => (
              <EntryRow key={e.id} entry={e} timeZone={timeZone} />
            ))}
          </TableBody>
        </Table>
      )}
      <Pager
        offset={offset}
        total={trail.data?.total ?? 0}
        onChange={(o) => onSearch((s) => ({ ...s, offset: o || undefined }))}
      />
    </div>
  );
}

function SignInRow({ entry, timeZone }: { entry: AuthEntry; timeZone: string }) {
  const facts = Object.entries(entry.detail)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k.replace("_", " ")}: ${String(v)}`);
  return (
    <TableRow className="align-top">
      <TableCell className="w-32 text-xs whitespace-normal sm:w-48">{formatWhen(entry.occurred_at, timeZone)}</TableCell>
      <TableCell>
        <span className="font-medium">{entry.label}</span>
        {facts.length > 0 && <p className="text-xs break-words text-muted-foreground">{facts.join(" · ")}</p>}
      </TableCell>
    </TableRow>
  );
}

function SignInsView({ search, onSearch, timeZone }: Props & { timeZone: string }) {
  const offset = search.offset ?? 0;
  const filter = { kind: search.kind, date_from: search.date_from, date_to: search.date_to };
  const events = useQuery({
    queryKey: ["auth-events", filter, offset],
    queryFn: () => fetchAuthEvents(filter, offset),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Event
          <select
            className={inputClass}
            value={search.kind ?? ""}
            onChange={(e) => onSearch(withParam("kind", e.target.value))}
          >
            <option value="">All events</option>
            {AUTH_KINDS.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <DateFilters search={search} onSearch={onSearch} />
      </div>
      <GeneralError error={events.error} />
      {events.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {events.data && events.data.total === 0 && (
        <p className="text-sm text-muted-foreground">No sign-in events match these filters.</p>
      )}
      {events.data && events.data.total > 0 && (
        <Table aria-label="Sign-in events">
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Event</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.data.items.map((e) => (
              <SignInRow key={e.id} entry={e} timeZone={timeZone} />
            ))}
          </TableBody>
        </Table>
      )}
      <Pager
        offset={offset}
        total={events.data?.total ?? 0}
        onChange={(o) => onSearch((s) => ({ ...s, offset: o || undefined }))}
      />
    </div>
  );
}

/** The Audit trail. All of its state (view, filters, page) is in the URL search params. */
export function AuditPage({ search, onSearch }: Props) {
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const timeZone = household.data?.time_zone ?? DEFAULT_TIME_ZONE;
  const signIns = search.tab === "sign-ins";

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Audit trail" description="Every change to the Household's records, and every sign-in." />
      <nav aria-label="Audit trail views" className="flex gap-2">
        {(
          [
            ["Changes", false],
            ["Sign-ins", true],
          ] as const
        ).map(([label, isSignIns]) => (
          <Link
            key={label}
            to="/audit"
            search={isSignIns ? { tab: "sign-ins" as const } : {}}
            aria-current={signIns === isSignIns ? "page" : undefined}
            className={cn(buttonVariants({ variant: signIns === isSignIns ? "default" : "outline" }))}
          >
            {label}
          </Link>
        ))}
      </nav>
      {signIns ? (
        <SignInsView search={search} onSearch={onSearch} timeZone={timeZone} />
      ) : (
        <ChangesView search={search} onSearch={onSearch} timeZone={timeZone} />
      )}
    </div>
  );
}

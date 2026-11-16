import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { FetchNowButton } from "@/components/fetch-now-button";
import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchInstruments, KINDS, schemeLabel } from "@/lib/instruments";
import { Pager, TransactionsTable } from "@/components/transactions-table";
import {
  commitImport,
  coverageText,
  createReferenceInstrument,
  discardImport,
  fetchImport,
  fetchImportDiff,
  fetchImportRows,
  fetchRemoved,
  formatDate,
  importBadge,
  mapImportType,
  mapReference,
  REMOVED_PER_PAGE,
  ROWS_PER_PAGE,
  rowKindLabel,
  streamLabel,
  type ImportDetail,
  type ImportDiff,
  type StagedRow,
  type UnmappedType,
  type UnresolvedReference,
} from "@/lib/imports";

function BackLink() {
  return (
    <Link
      to="/imports"
      className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft size={14} />
      Imports
    </Link>
  );
}

/** Match one unresolved reference: map it to an existing Instrument, or create one. */
function MatchDialog({
  imp,
  reference,
  onClose,
}: {
  imp: ImportDetail;
  reference: UnresolvedReference;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: fetchInstruments });
  const [mode, setMode] = useState<"map" | "create">("map");
  const [instrumentId, setInstrumentId] = useState("");
  const [name, setName] = useState(reference.name ?? reference.ref);
  const [kind, setKind] = useState(reference.kind_hint ?? "etf");
  const [currency, setCurrency] = useState("AUD");
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ["imports"] });
    void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    onClose();
  };
  const ref = { scheme: reference.scheme, ref: reference.ref };
  const map = useMutation({
    mutationFn: () => mapReference(imp.id, ref, instrumentId ? Number(instrumentId) : null),
    onSuccess: done,
  });
  const create = useMutation({
    mutationFn: () => createReferenceInstrument(imp.id, { ...ref, name, kind, currency }),
    onSuccess: done,
  });
  const active = mode === "map" ? map : create;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Match {reference.ref}</DialogTitle>
          <DialogDescription>
            {schemeLabel(reference.scheme)} {reference.ref}
            {reference.name && reference.name !== reference.ref ? ` (${reference.name})` : ""} is not matched
            to an Instrument yet. Matching adds it as an identifier, so later files resolve on their own.
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-2" role="group" aria-label="How to match">
          <Button
            type="button"
            variant="outline"
            aria-pressed={mode === "map"}
            className={mode === "map" ? "bg-accent" : ""}
            onClick={() => setMode("map")}
          >
            Existing Instrument
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-pressed={mode === "create"}
            className={mode === "create" ? "bg-accent" : ""}
            onClick={() => setMode("create")}
          >
            New Instrument
          </Button>
        </div>
        <form
          aria-label={`Match ${reference.ref}`}
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            active.mutate();
          }}
        >
          {mode === "map" ? (
            <label className="flex flex-col gap-1 text-sm">
              Instrument
              <select
                className={inputClass}
                value={instrumentId}
                onChange={(e) => setInstrumentId(e.target.value)}
              >
                <option value="">Choose an Instrument…</option>
                {instruments.data?.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
              </select>
              <FieldMessage error={map.error} field="instrument_id" />
              <FieldMessage error={map.error} field="value" />
            </label>
          ) : (
            <>
              <label className="flex flex-col gap-1 text-sm">
                Name
                <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
                <FieldMessage error={create.error} field="name" />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  Kind
                  <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
                    {KINDS.map((k) => (
                      <option key={k.key} value={k.key}>
                        {k.label}
                      </option>
                    ))}
                  </select>
                  <FieldMessage error={create.error} field="kind" />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  Currency
                  <input className={inputClass} value={currency} onChange={(e) => setCurrency(e.target.value)} />
                  <FieldMessage error={create.error} field="currency" />
                </label>
              </div>
              <FieldMessage error={create.error} field="value" />
            </>
          )}
          <GeneralError error={active.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={active.isPending || (mode === "map" && !instrumentId)}>
              {mode === "map" ? "Map reference" : "Create Instrument"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** References with no Instrument yet. While any remain, commit is blocked. */
function UnresolvedPanel({ imp }: { imp: ImportDetail }) {
  const [matching, setMatching] = useState<UnresolvedReference | null>(null);
  if (imp.unresolved.length === 0) return null;
  return (
    <Card aria-label="Unresolved references">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle>Unmatched references</CardTitle>
        <Badge variant="flagged">Commit blocked</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          These references in the file are not matched to an Instrument. Match each one before this file can be committed.
        </p>
        <Table aria-label="Unmatched references">
          <TableHeader>
            <TableRow>
              <TableHead>Reference</TableHead>
              <TableHead>Name in the file</TableHead>
              <TableHead className="text-right">Rows</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {imp.unresolved.map((u) => (
              <TableRow
                key={`${u.scheme}:${u.ref}`}
                className="cursor-pointer"
                onClick={() => setMatching(u)}
                aria-label={`Match ${u.ref}`}
              >
                <TableCell>
                  <Button
                    variant="link"
                    className="h-auto px-0 font-mono break-all"
                    onClick={(e) => {
                      e.stopPropagation();
                      setMatching(u);
                    }}
                  >
                    {u.ref}
                  </Button>
                  <p className="text-xs text-muted-foreground">{schemeLabel(u.scheme)}</p>
                </TableCell>
                <TableCell className="break-words">{u.name ?? "Not given"}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{u.row_count}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {matching && <MatchDialog imp={imp} reference={matching} onClose={() => setMatching(null)} />}
      </CardContent>
    </Card>
  );
}

/** One Type the Importer does not know: the owner says what it means, once, for this source. */
function UnmappedTypeRow({ imp, type }: { imp: ImportDetail; type: UnmappedType }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState("");
  const map = useMutation({
    mutationFn: () => mapImportType(imp.id, type.raw_type, kind),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
    },
  });
  return (
    <TableRow aria-label={`Map ${type.raw_type}`}>
      <TableCell className="break-words">{type.raw_type}</TableCell>
      <TableCell className="text-right font-mono tabular-nums">{type.row_count}</TableCell>
      <TableCell>
        <form
          aria-label={`Map ${type.raw_type}`}
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            map.mutate();
          }}
        >
          <select
            className={inputClass}
            aria-label={`Meaning of ${type.raw_type}`}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">Choose�</option>
            {type.choices.map((c) => (
              <option key={c} value={c}>
                {c === "ignore" ? "Ignore" : rowKindLabel(c)}
              </option>
            ))}
          </select>
          <Button type="submit" variant="outline" disabled={!kind || map.isPending}>
            Save
          </Button>
        </form>
        <FieldMessage error={map.error} field="kind" />
        <GeneralError error={map.error} />
      </TableCell>
    </TableRow>
  );
}

/** Types the Importer does not know. While any remain, commit is blocked. */
function UnmappedTypesPanel({ imp }: { imp: ImportDetail }) {
  if (imp.unmapped_types.length === 0) return null;
  return (
    <Card aria-label="New transaction types">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle>New transaction types</CardTitle>
        <Badge variant="flagged">Commit blocked</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          The file has transaction types Finance360 does not know. Say what each one means; it is remembered for{" "}
          {imp.account_name}, so later files apply it on their own. Rows set to Ignore are never committed.
        </p>
        <Table aria-label="New transaction types">
          <TableHeader>
            <TableRow>
              <TableHead>Type in the file</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Means</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {imp.unmapped_types.map((t) => (
              <UnmappedTypeRow key={t.raw_type} imp={imp} type={t} />
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function RowInstrument({ row }: { row: StagedRow }) {
  if (!row.instrument_ref) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="break-words">{row.resolved_name ?? row.instrument_name ?? row.instrument_ref}</span>
      <span className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        <span className="font-mono">{row.instrument_ref}</span>
        {row.instrument_id === null && <Badge variant="flagged">Unmatched</Badge>}
      </span>
    </div>
  );
}

const dash = <span className="text-muted-foreground">—</span>;

/** Added rows become Transactions on commit; unchanged rows already exist. */
function ChangeBadge({ change }: { change: string }) {
  return change === "added" ? <Badge variant="ok">Added</Badge> : <Badge variant="closed">Unchanged</Badge>;
}

function RowsSection({ imp, page, onPage }: { imp: ImportDetail; page: number; onPage: (page: number) => void }) {
  const rows = useQuery({
    queryKey: ["imports", "rows", imp.id, page],
    queryFn: () => fetchImportRows(imp.id, page),
  });
  const total = rows.data?.total ?? imp.row_count;
  const staged = imp.status === "staged";
  const pages = Math.max(1, Math.ceil(total / ROWS_PER_PAGE));
  return (
    <section aria-label="Staged rows" className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">Staged rows</h2>
      {rows.isPending && <p className="text-muted-foreground">Loading…</p>}
      {rows.error && <p className="text-destructive">Could not load the rows: {rows.error.message}</p>}
      {rows.data && (
        <>
          <Table aria-label="Staged rows">
            <TableHeader>
              <TableRow>
                <TableHead className="hidden text-right md:table-cell">#</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="hidden md:table-cell">Kind</TableHead>
                {staged && <TableHead className="hidden md:table-cell">Change</TableHead>}
                <TableHead>Instrument</TableHead>
                <TableHead className="hidden text-right md:table-cell">Units</TableHead>
                <TableHead className="hidden text-right md:table-cell">Unit price</TableHead>
                <TableHead className="hidden text-right md:table-cell">Fee</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.data.items.map((r) => (
                <TableRow key={r.row_no}>
                  <TableCell className="hidden text-right font-mono text-xs text-muted-foreground tabular-nums md:table-cell">
                    {r.row_no}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {r.occurred_on}
                    <p className="text-xs text-muted-foreground md:hidden">{rowKindLabel(r.kind)}</p>
                    {r.change && (
                      <p className="md:hidden">
                        <ChangeBadge change={r.change} />
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap md:table-cell">{rowKindLabel(r.kind)}</TableCell>
                  {staged && (
                    <TableCell className="hidden md:table-cell">
                      {r.change ? <ChangeBadge change={r.change} /> : dash}
                    </TableCell>
                  )}
                  <TableCell>
                    <RowInstrument row={r} />
                    {r.quantity && r.unit_price && (
                      <p className="font-mono text-xs text-muted-foreground tabular-nums md:hidden">
                        {r.quantity} @ {r.unit_price}
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-right font-mono tabular-nums md:table-cell">
                    {r.quantity ?? dash}
                  </TableCell>
                  <TableCell className="hidden text-right font-mono tabular-nums md:table-cell">
                    {r.unit_price ?? dash}
                  </TableCell>
                  <TableCell className="hidden text-right md:table-cell">
                    {r.fee ? <Money amount={r.fee.amount} currency={r.fee.currency} /> : dash}
                  </TableCell>
                  <TableCell className="text-right">
                    {r.amount ? <Money amount={r.amount.amount} currency={r.amount.currency} /> : dash}
                    {r.fee && (
                      <p className="text-xs text-muted-foreground md:hidden">
                        Fee <Money amount={r.fee.amount} currency={r.fee.currency} />
                      </p>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>
              {total} {total === 1 ? "row" : "rows"} · page {page} of {pages}
            </span>
            {pages > 1 && (
              <div className="flex gap-2">
                <Button variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
                  Previous page
                </Button>
                <Button variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
                  Next page
                </Button>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/** What committing would do: counts, the range, the Transactions it would supersede, and warnings. */
function ChangesPanel({ imp, diff }: { imp: ImportDetail; diff: ImportDiff }) {
  const [page, setPage] = useState(1);
  const removed = useQuery({
    queryKey: ["imports", "removed", imp.id, page],
    queryFn: () => fetchRemoved(imp.id, page),
    enabled: diff.removed > 0,
  });
  const stream = imp.stream === "cash" ? "cash" : "trade";
  return (
    <Card aria-label="Changes">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle>Changes if committed</CardTitle>
        <Badge variant="ok">{diff.added} added</Badge>
        <Badge variant={diff.removed > 0 ? "flagged" : "closed"}>{diff.removed} removed</Badge>
        <Badge variant="closed">{diff.unchanged} unchanged</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Compared with this Account&apos;s current {streamLabel(imp.stream).toLowerCase()} Transactions from the same
          Import source, {coverageText(diff.scope_from, diff.scope_to)}. Anything outside that range, in the other
          Stream, or from another Import source is left alone. A changed row is shown as one removed and one added.
        </p>
        {diff.warnings.length > 0 && (
          <div className="flex flex-col gap-1" role="status" aria-label="Warnings">
            <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
              <Badge variant="flagged">
                {diff.warnings.length} {diff.warnings.length === 1 ? "warning" : "warnings"}
              </Badge>
              <span>
                Trades without matching cash. These do not block commit; the other Stream may not be imported yet.
              </span>
            </p>
            <ul className="list-disc pl-5 text-sm text-muted-foreground">
              {diff.warnings.map((w) => (
                <li key={w.message} className="break-words">
                  {w.message}
                </li>
              ))}
            </ul>
          </div>
        )}
        {diff.removed > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Transactions this commit supersedes</h3>
            {removed.data && (
              <>
                <TransactionsTable items={removed.data.items} stream={stream} label="Removed Transactions" />
                <Pager
                  total={removed.data.total}
                  perPage={REMOVED_PER_PAGE}
                  page={page}
                  noun="Transaction"
                  onPage={setPage}
                />
              </>
            )}
            {removed.error && <p className="text-destructive">Could not load them: {removed.error.message}</p>}
            <p className="text-xs text-muted-foreground">They stay retrievable, marked superseded.</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Commit changes the Account's Transactions, so it always confirms and names the consequence. */
function CommitDialog({
  imp,
  diff,
  open,
  onOpenChange,
}: {
  imp: ImportDetail;
  diff: ImportDiff;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const commit = useMutation({
    mutationFn: () => commitImport(imp.id),
    onSuccess: () => {
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
      void queryClient.invalidateQueries({ queryKey: ["transactions"] });
      void queryClient.invalidateQueries({ queryKey: ["holdings"] });
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) commit.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Commit {imp.file_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            {diff.added} {diff.added === 1 ? "row becomes a Transaction" : "rows become Transactions"} on{" "}
            {imp.account_name}.{" "}
            {diff.removed > 0
              ? `${diff.removed} current ${diff.removed === 1 ? "Transaction is" : "Transactions are"} superseded; they stay retrievable. `
              : "No current Transaction is superseded. "}
            {diff.warnings.length > 0
              ? `${diff.warnings.length} ${diff.warnings.length === 1 ? "warning is" : "warnings are"} still open. `
              : ""}
            One audit entry records the commit.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FieldMessage error={commit.error} field="status" />
        <FieldMessage error={commit.error} field="references" />
        <FieldMessage error={commit.error} field="types" />
        <GeneralError error={commit.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={commit.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              commit.mutate();
            }}
          >
            Commit import
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** A committed Import: what it did, and where to see the Transactions. */
function CommittedPanel({ imp }: { imp: ImportDetail }) {
  if (!imp.committed) return null;
  const { added, replaced, unchanged, committed_at } = imp.committed;
  return (
    <Card aria-label="Commit result">
      <CardHeader>
        <CardTitle>Committed</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <p>
          On {formatDate(committed_at.slice(0, 10))}: {added} {added === 1 ? "Transaction" : "Transactions"} added,{" "}
          {replaced} superseded, {unchanged} already there.
        </p>
        <Link
          to="/accounts/$accountId"
          params={{ accountId: String(imp.account_id) }}
          search={{ stream: imp.stream === "cash" ? "cash" : "trade" }}
          className="w-fit underline"
        >
          View {imp.account_name} Transactions
        </Link>
        {imp.committed.offer_fetch && (
          <div className="flex flex-col items-start gap-1 border-t border-border pt-3">
            <p>
              {imp.committed.instruments_without_price === 1
                ? "1 Instrument in this Import has no Price yet."
                : `${imp.committed.instruments_without_price} Instruments in this Import have no Price yet.`}{" "}
              Fetch now to bring in their Prices (an Instrument needs a Yahoo symbol to be fetched).
            </p>
            <FetchNowButton variant="outline" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Discard is destructive: it lives in the overflow menu and always confirms. */
function DiscardDialog({
  imp,
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  imp: ImportDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const queryClient = useQueryClient();
  const discard = useMutation({
    mutationFn: () => discardImport(imp.id),
    onSuccess: () => {
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) discard.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard {imp.file_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            The file will not be imported. Its staged rows are kept for inspection, and the same file can be
            staged again later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={discard.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={discard.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              discard.mutate();
            }}
          >
            Discard import
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ImportDetailView({ imp, page, onPage }: { imp: ImportDetail; page: number; onPage: (page: number) => void }) {
  const [discarding, setDiscarding] = useState(false);
  const [committing, setCommitting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const badge = importBadge(imp);
  const diff = useQuery({
    queryKey: ["imports", "diff", imp.id],
    queryFn: () => fetchImportDiff(imp.id),
    enabled: imp.status === "staged",
  });
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink />
        <PageHeader
          title={imp.file_name}
          description={
            <span className="flex flex-wrap items-center gap-2">
              <Badge variant="info">{streamLabel(imp.stream)}</Badge>
              <Badge variant={badge.variant}>{badge.label}</Badge>
              <span>
                {imp.importer_name} ·{" "}
                <Link to="/accounts/$accountId" params={{ accountId: String(imp.account_id) }} className="underline">
                  {imp.account_name}
                </Link>{" "}
                · {coverageText(imp.coverage_from, imp.coverage_to)}
              </span>
            </span>
          }
          action={
            imp.status === "staged" ? (
              <div className="flex items-center gap-2">
                <Button disabled={imp.commit_blocked || !diff.data} onClick={() => setCommitting(true)}>
                  Commit import
                </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button ref={menuRef} variant="outline" aria-label="More actions" className="px-2">
                    <MoreHorizontal size={16} />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem destructive onSelect={() => setDiscarding(true)}>
                    Discard import
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              </div>
            ) : undefined
          }
        />
      </div>
      {imp.warnings.length > 0 && (
        <Card aria-label="Notes from the file">
          <CardHeader>
            <CardTitle>Notes from the file</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc pl-5 text-sm text-muted-foreground">
              {imp.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      {imp.status === "staged" && <UnmappedTypesPanel imp={imp} />}
      {imp.status === "staged" && <UnresolvedPanel imp={imp} />}
      {imp.status === "staged" && diff.data && <ChangesPanel imp={imp} diff={diff.data} />}
      <CommittedPanel imp={imp} />
      <RowsSection imp={imp} page={page} onPage={onPage} />
      {imp.status === "staged" && diff.data && (
        <CommitDialog imp={imp} diff={diff.data} open={committing} onOpenChange={setCommitting} />
      )}
      {imp.status === "staged" && (
        <DiscardDialog
          imp={imp}
          open={discarding}
          onOpenChange={setDiscarding}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            menuRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}

/** `/imports/$importId`: the Stream, Coverage, unmatched references and staged rows of one file. */
export function ImportDetailPage({
  importId,
  page,
  onPage,
}: {
  importId: string;
  page: number;
  onPage: (page: number) => void;
}) {
  const id = Number(importId);
  const valid = Number.isInteger(id) && id > 0;
  const imp = useQuery({
    queryKey: ["imports", "detail", id],
    queryFn: () => fetchImport(id),
    enabled: valid,
    retry: false,
  });
  if (imp.data) return <ImportDetailView imp={imp.data} page={page} onPage={onPage} />;
  if (!valid || imp.error) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader title="Import not found" description="There is no Import at this address." />
        <BackLink />
      </div>
    );
  }
  return <p className="text-muted-foreground">Loading…</p>;
}

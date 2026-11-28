import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Plus } from "lucide-react";
import { useRef, useState } from "react";

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
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { todayIn } from "@/lib/household";
import {
  addValuation,
  correctValuation,
  ENTERED_SOURCES,
  fetchValuations,
  removeValuation,
  type Valuation,
  type ValuationInput,
} from "@/lib/valuations";
import { announce, NOTICES_KEY } from "@/lib/notices";
import { QUESTIONS_KEY } from "@/lib/questions";

type Account = { id: number; name: string; currency: string; closed_on?: string | null };

/** A history longer than this shows the latest rows and a "Show all" control. */
const PAGE = 10;

function ValuationForm({
  account,
  timeZone,
  initial,
  onSubmit,
  onDone,
  onCancel,
}: {
  account: Account;
  timeZone: string;
  initial?: Valuation;
  onSubmit: (input: ValuationInput) => Promise<Valuation>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [asAt, setAsAt] = useState(initial?.as_at ?? todayIn(timeZone));
  const [amount, setAmount] = useState(initial?.amount ?? "");
  const [source, setSource] = useState<string>(initial?.source ?? "manual");
  const [note, setNote] = useState(initial?.provenance_note ?? "");
  const save = useMutation({
    mutationFn: () =>
      onSubmit({ as_at: asAt, amount, source, provenance_note: note, currency: account.currency }),
    onSuccess: (saved) => {
      announce(saved.notice);
      onDone();
    },
  });
  const label = initial ? `Correct Valuation of ${account.name}` : `Add Valuation to ${account.name}`;

  return (
    <form
      aria-label={label}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          As at
          <input
            className={inputClass}
            type="date"
            value={asAt}
            onChange={(e) => setAsAt(e.target.value)}
          />
          <FieldMessage error={save.error} field="as_at" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Amount ({account.currency})
          <input
            className={inputClass}
            inputMode="decimal"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
          <FieldMessage error={save.error} field="amount" />
          <FieldMessage error={save.error} field="currency" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        Source
        <select className={inputClass} value={source} onChange={(e) => setSource(e.target.value)}>
          {ENTERED_SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="source" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Provenance note
        <input
          className={inputClass}
          placeholder="Where did this figure come from?"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <FieldMessage error={save.error} field="provenance_note" />
      </label>
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save correction" : "Add Valuation"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function supersededLabel(v: Valuation): string {
  const when = v.superseded_at?.slice(0, 10) ?? "";
  return v.status === "removed" ? `Superseded: removed ${when}` : `Superseded: corrected ${when}`;
}

function ValuationRow({
  account,
  timeZone,
  liability,
  valuation,
  onChange,
}: {
  account: Account;
  timeZone: string;
  liability: boolean;
  valuation: Valuation;
  onChange: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const remove = useMutation({ mutationFn: () => removeValuation(valuation.id), onSuccess: onChange });
  const current = valuation.status === "current";
  const name = `Valuation of ${valuation.as_at}`;
  // The dialogs open from a menu item, so hand focus back to the row's menu button on close.
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };

  return (
    <TableRow data-status={valuation.status} className={current ? "" : "text-muted-foreground"}>
      <TableCell>
        <span className={`font-medium whitespace-nowrap ${current ? "" : "line-through"}`}>
          {valuation.as_at}
        </span>
        {valuation.provenance_note && (
          <p className="text-xs font-normal text-muted-foreground">{valuation.provenance_note}</p>
        )}
        {!current && (
          <Badge variant="superseded" className="mt-1 whitespace-normal">
            {supersededLabel(valuation)}
          </Badge>
        )}
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className={`text-right whitespace-nowrap ${current ? "" : "line-through"}`}>
        <Money amount={valuation.amount} currency={valuation.currency} liability={liability} />
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <Badge variant="info">{valuation.source}</Badge>
      </TableCell>
      <TableCell className="w-10 text-right">
        {current && (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button ref={menuRef} variant="ghost" aria-label={`Actions for ${name}`} className="px-2">
                  <MoreHorizontal size={16} />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem onSelect={() => setCorrecting(true)}>Correct Valuation</DropdownMenuItem>
                <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
                  Remove Valuation
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Dialog open={correcting} onOpenChange={setCorrecting}>
              <DialogContent onCloseAutoFocus={returnFocus}>
                <DialogHeader>
                  <DialogTitle>Correct the {name}</DialogTitle>
                  <DialogDescription>The original stays in the history as superseded.</DialogDescription>
                </DialogHeader>
                <ValuationForm
                  account={account}
                  timeZone={timeZone}
                  initial={valuation}
                  onSubmit={(input) => correctValuation(valuation.id, input)}
                  onCancel={() => setCorrecting(false)}
                  onDone={() => {
                    setCorrecting(false);
                    onChange();
                  }}
                />
              </DialogContent>
            </Dialog>
            <AlertDialog open={removing} onOpenChange={setRemoving}>
              <AlertDialogContent onCloseAutoFocus={returnFocus}>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Remove the {valuation.as_at} Valuation of {account.name}?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    It stops counting towards the Account&apos;s value. It stays in the history, marked as removed.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => remove.mutate()}>Remove Valuation</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        )}
      </TableCell>
    </TableRow>
  );
}

/** An Account's Valuation history (superseded rows included): add, correct and remove. */
export function ValuationsPanel({
  account,
  timeZone,
  liability = false,
}: {
  account: Account;
  timeZone: string;
  liability?: boolean;
}) {
  const queryClient = useQueryClient();
  const key = ["valuations", account.id];
  const { data, error, isPending } = useQuery({
    queryKey: key,
    queryFn: () => fetchValuations(account.id),
  });
  const [adding, setAdding] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
    void queryClient.invalidateQueries({ queryKey: ["position"] });
    // The write may have made a Notice and moved a Question's Readiness (ADR-0033).
    void queryClient.invalidateQueries({ queryKey: NOTICES_KEY });
    void queryClient.invalidateQueries({ queryKey: QUESTIONS_KEY });
  };
  const rows = showAll ? data : data?.slice(0, PAGE);

  return (
    <Card aria-label={`Valuations of ${account.name}`} role="region">
      <Dialog open={adding} onOpenChange={setAdding}>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Valuations</CardTitle>
          <DialogTrigger asChild>
            <Button>
              <Plus size={16} />
              Add Valuation
            </Button>
          </DialogTrigger>
        </CardHeader>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add a Valuation to {account.name}</DialogTitle>
            <DialogDescription>A dated figure, with where it came from.</DialogDescription>
          </DialogHeader>
          <ValuationForm
            account={account}
            timeZone={timeZone}
            onSubmit={(input) => addValuation(account.id, input)}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>
      <CardContent className="flex flex-col gap-3">
        {isPending && <p className="text-muted-foreground">Loading…</p>}
        {error && <p className="text-destructive">Could not load the Valuations: {error.message}</p>}
        {data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No Valuations yet. Until one is added, this Account has no value (not zero).
          </p>
        )}
        {rows && rows.length > 0 && (
          <Table aria-label="Valuation history">
            <TableHeader>
              <TableRow>
                <TableHead>As at</TableHead>
                <TableHead className="text-right">Amount ({account.currency})</TableHead>
                <TableHead className="hidden sm:table-cell">Source</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((v) => (
                <ValuationRow
                  key={v.id}
                  account={account}
                  timeZone={timeZone}
                  liability={liability}
                  valuation={v}
                  onChange={refresh}
                />
              ))}
            </TableBody>
          </Table>
        )}
        {data && data.length > PAGE && (
          <div>
            <Button variant="link" onClick={() => setShowAll(!showAll)}>
              {showAll ? `Show the latest ${PAGE}` : `Show all ${data.length} Valuations`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

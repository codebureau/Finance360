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
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  addFxRate,
  convertOn,
  correctFxRate,
  fetchFxRates,
  removeFxRate,
  type FxRate,
  type FxRateInput,
} from "@/lib/fx-rates";
import { fetchHousehold, todayIn } from "@/lib/household";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";

function FxRateForm({
  today,
  initial,
  onSubmit,
  onDone,
  onCancel,
}: {
  today: string;
  initial?: FxRate;
  onSubmit: (input: FxRateInput) => Promise<unknown>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [from, setFrom] = useState(initial?.from_ccy ?? "GBP");
  const [asAt, setAsAt] = useState(initial?.as_at ?? today);
  const [rate, setRate] = useState(initial?.rate ?? "");
  const [note, setNote] = useState(initial?.provenance_note ?? "");
  const save = useMutation({
    mutationFn: () =>
      onSubmit({ from_ccy: from, to_ccy: "AUD", as_at: asAt, rate, provenance_note: note }),
    onSuccess: onDone,
  });

  return (
    <form
      aria-label={initial ? "Correct FX rate" : "Add FX rate"}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          From currency
          <input className={inputClass} value={from} onChange={(e) => setFrom(e.target.value)} />
          <FieldMessage error={save.error} field="from_ccy" />
          <FieldMessage error={save.error} field="to_ccy" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          As at
          <input className={inputClass} type="date" value={asAt} onChange={(e) => setAsAt(e.target.value)} />
          <FieldMessage error={save.error} field="as_at" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        Rate (AUD per 1 {from.trim().toUpperCase() || "unit"})
        <input
          className={inputClass}
          inputMode="decimal"
          placeholder="2.0143"
          value={rate}
          onChange={(e) => setRate(e.target.value)}
        />
        <FieldMessage error={save.error} field="rate" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Provenance note
        <input
          className={inputClass}
          placeholder="Where did this rate come from?"
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
          {initial ? "Save correction" : "Add rate"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function supersededLabel(r: FxRate): string {
  const when = r.superseded_at?.slice(0, 10) ?? "";
  return r.status === "removed" ? `Superseded: removed ${when}` : `Superseded: corrected ${when}`;
}

function FxRateRow({ today, rate, onChange }: { today: string; rate: FxRate; onChange: () => void }) {
  const [correcting, setCorrecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const remove = useMutation({ mutationFn: () => removeFxRate(rate.id), onSuccess: onChange });
  const current = rate.status === "current";
  // Only the owner's own rates are edited; a fetched one is overridden by adding a manual rate.
  const editable = current && rate.source === "manual";
  const name = `${rate.from_ccy} rate of ${rate.as_at}`;
  // The dialogs open from a menu item, so hand focus back to the row's menu button on close.
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };

  return (
    <TableRow data-status={rate.status} className={current ? "" : "text-muted-foreground"}>
      <TableCell className={current ? "" : "line-through"}>
        <span className="font-medium whitespace-nowrap">{rate.as_at}</span>
        {rate.provenance_note && (
          <p className="text-xs font-normal text-muted-foreground no-underline">{rate.provenance_note}</p>
        )}
        {!current && (
          <Badge variant="superseded" className="mt-1 whitespace-normal">
            {supersededLabel(rate)}
          </Badge>
        )}
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className={current ? "whitespace-nowrap" : "whitespace-nowrap line-through"}>
        {rate.from_ccy} → {rate.to_ccy}
      </TableCell>
      <TableCell className={`text-right font-mono tabular-nums ${current ? "" : "line-through"}`}>
        {rate.rate}
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <Badge variant={rate.source === "manual" ? "ok" : "info"}>{rate.source}</Badge>
      </TableCell>
      <TableCell className="w-10 text-right">
        {editable && (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button ref={menuRef} variant="ghost" aria-label={`Actions for ${name}`} className="px-2">
                  <MoreHorizontal size={16} />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem onSelect={() => setCorrecting(true)}>Correct rate</DropdownMenuItem>
                <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
                  Remove rate
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Dialog open={correcting} onOpenChange={setCorrecting}>
              <DialogContent onCloseAutoFocus={returnFocus}>
                <DialogHeader>
                  <DialogTitle>Correct the {name}</DialogTitle>
                  <DialogDescription>The original stays in the history as superseded.</DialogDescription>
                </DialogHeader>
                <FxRateForm
                  today={today}
                  initial={rate}
                  onSubmit={(input) => correctFxRate(rate.id, input)}
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
                  <AlertDialogTitle>Remove the {name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Conversions stop using it. It stays in the history, marked as removed.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => remove.mutate()}>Remove FX rate</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        )}
      </TableCell>
    </TableRow>
  );
}

function ConversionPreview({ today }: { today: string }) {
  const [amount, setAmount] = useState("100.00");
  const [currency, setCurrency] = useState("GBP");
  const [on, setOn] = useState(today);
  const preview = useMutation({ mutationFn: () => convertOn(amount, currency, on) });
  const result = preview.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Conversion preview</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          aria-label="Conversion preview"
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            preview.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-sm">
              Amount
              <input
                className={inputClass}
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
              <FieldMessage error={preview.error} field="amount" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              Currency
              <input className={inputClass} value={currency} onChange={(e) => setCurrency(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              On
              <input className={inputClass} type="date" value={on} onChange={(e) => setOn(e.target.value)} />
            </label>
          </div>
          <div>
            <Button type="submit" variant="secondary" disabled={preview.isPending}>
              Convert to AUD
            </Button>
          </div>
          <GeneralError error={preview.error} />
          {result?.result === "converted" && (
            <p role="status">
              {result.currency} {result.amount} = AUD {result.converted} at {result.rate}, the rate of{" "}
              {result.rate_as_at}.
            </p>
          )}
          {result?.result === "no_rate" && (
            <p role="status" className="text-destructive">
              No {result.currency} to {result.to_ccy} rate on or before {result.on}. Add one; nothing is
              assumed.
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

const PAGE = 25;

/** FX rates (foreign to AUD) entered by hand or fetched: history table, add in a dialog, and a preview. */
export function FxRatesPage() {
  const queryClient = useQueryClient();
  const rates = useQuery({ queryKey: ["fx-rates"], queryFn: fetchFxRates });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const [adding, setAdding] = useState(false);
  // A fetched provider adds a rate per day, so the history is paged, not one endless table.
  const [shown, setShown] = useState(PAGE);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["fx-rates"] });
  const today = todayIn(household.data?.time_zone ?? DEFAULT_TIME_ZONE);

  return (
    <div className="flex flex-col gap-6">
      <Dialog open={adding} onOpenChange={setAdding}>
        <PageHeader
          title="FX rates"
          description="Foreign currency to AUD. A conversion uses the latest rate on or before its date."
          action={
            <DialogTrigger asChild>
              <Button>
                <Plus size={16} />
                Add FX rate
              </Button>
            </DialogTrigger>
          }
        />
        {rates.isPending && <p className="text-muted-foreground">Loading…</p>}
        {rates.error && <p className="text-destructive">Could not load the FX rates: {rates.error.message}</p>}
        {rates.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No FX rates yet. Until one is added, a foreign amount cannot be converted.
          </p>
        )}
        {rates.data && rates.data.length > 0 && (
          <Table aria-label="FX rate history">
            <TableHeader>
              <TableRow>
                <TableHead>As at</TableHead>
                <TableHead>Pair</TableHead>
                <TableHead className="text-right">Rate</TableHead>
                <TableHead className="hidden sm:table-cell">Source</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rates.data.slice(0, shown).map((r) => (
                <FxRateRow key={r.id} today={today} rate={r} onChange={refresh} />
              ))}
            </TableBody>
          </Table>
        )}
        {rates.data && rates.data.length > shown && (
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>
              Showing {shown} of {rates.data.length} rates, newest first.
            </span>
            <Button type="button" variant="outline" onClick={() => setShown(shown + PAGE)}>
              Show more
            </Button>
          </div>
        )}
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add FX rate</DialogTitle>
              <DialogDescription>Foreign currency to AUD, with where the rate came from.</DialogDescription>
            </DialogHeader>
            <FxRateForm
              today={today}
              onSubmit={addFxRate}
              onCancel={() => setAdding(false)}
              onDone={() => {
                setAdding(false);
                refresh();
              }}
            />
          </DialogContent>
      </Dialog>
      <ConversionPreview today={today} />
    </div>
  );
}

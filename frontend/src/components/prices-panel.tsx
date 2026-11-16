import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
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
import { Button, buttonVariants } from "@/components/ui/button";
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
import { fetchHousehold, todayIn } from "@/lib/household";
import type { Instrument } from "@/lib/instruments";
import {
  addPrice,
  correctPrice,
  fetchPrices,
  priceOn,
  removePrice,
  type Price,
  type PriceInput,
} from "@/lib/prices";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";
/** Rows shown before "Show all": long histories stay a short section. */
const SHOWN = 10;

function PriceForm({
  instrument,
  today,
  initial,
  onSubmit,
  onDone,
  onCancel,
}: {
  instrument: Instrument;
  today: string;
  initial?: Price;
  onSubmit: (input: PriceInput) => Promise<unknown>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [asAt, setAsAt] = useState(initial?.as_at ?? today);
  const [value, setValue] = useState(initial?.value ?? "");
  const [note, setNote] = useState(initial?.provenance_note ?? "");
  const save = useMutation({
    mutationFn: () => onSubmit({ as_at: asAt, value, provenance_note: note, currency: instrument.currency }),
    onSuccess: onDone,
  });

  return (
    <form
      aria-label={initial ? `Correct Price of ${instrument.name}` : `Add Price to ${instrument.name}`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          As at
          <input className={inputClass} type="date" value={asAt} onChange={(e) => setAsAt(e.target.value)} />
          <FieldMessage error={save.error} field="as_at" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Price per unit ({instrument.currency})
          <input
            className={inputClass}
            inputMode="decimal"
            placeholder="34.5678"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <FieldMessage error={save.error} field="value" />
          <FieldMessage error={save.error} field="currency" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        Note
        <input
          className={inputClass}
          placeholder="Where did this Price come from?"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <FieldMessage error={save.error} field="provenance_note" />
      </label>
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save correction" : "Add Price"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function supersededLabel(p: Price): string {
  const when = p.superseded_at?.slice(0, 10) ?? "";
  return p.status === "removed" ? `Superseded: removed ${when}` : `Superseded: corrected ${when}`;
}

function PriceRow({
  instrument,
  today,
  price,
  onChange,
}: {
  instrument: Instrument;
  today: string;
  price: Price;
  onChange: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const remove = useMutation({ mutationFn: () => removePrice(instrument.id, price.id), onSuccess: onChange });
  const current = price.status === "current";
  const editable = current && price.source === "manual";
  const name = `Price of ${price.as_at} for ${instrument.name}`;
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };
  const struck = current ? "" : "line-through";

  return (
    <TableRow data-status={price.status} className={current ? "align-top" : "align-top text-muted-foreground"}>
      <TableCell className={struck}>
        <span className="font-medium whitespace-nowrap">{price.as_at}</span>
        {price.provenance_note && (
          <p className="text-xs font-normal break-words text-muted-foreground no-underline">{price.provenance_note}</p>
        )}
        <Badge variant={price.source === "manual" ? "ok" : "info"} className="mt-1 block w-fit sm:hidden">
          {price.source}
        </Badge>
        {!current && (
          <Badge variant="superseded" className="mt-1 whitespace-normal">
            {supersededLabel(price)}
          </Badge>
        )}
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className={`text-right ${struck}`}>
        <Money amount={price.value} currency={price.currency} />
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <Badge variant={price.source === "manual" ? "ok" : "info"}>{price.source}</Badge>
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
                <DropdownMenuItem onSelect={() => setCorrecting(true)}>Correct Price</DropdownMenuItem>
                <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
                  Remove Price
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Dialog open={correcting} onOpenChange={setCorrecting}>
              <DialogContent onCloseAutoFocus={returnFocus}>
                <DialogHeader>
                  <DialogTitle>Correct the {name}</DialogTitle>
                  <DialogDescription>The original stays in the history as superseded.</DialogDescription>
                </DialogHeader>
                <PriceForm
                  instrument={instrument}
                  today={today}
                  initial={price}
                  onSubmit={(input) => correctPrice(instrument.id, price.id, input)}
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
                    Lookups stop using it. It stays in the history, marked as removed.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => remove.mutate()}>Remove Price</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        )}
      </TableCell>
    </TableRow>
  );
}

function PriceLookupForm({ instrument, today }: { instrument: Instrument; today: string }) {
  const [on, setOn] = useState(today);
  const lookup = useMutation({ mutationFn: () => priceOn(instrument.id, on) });
  const result = lookup.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Price on a date</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          aria-label={`Look up a Price of ${instrument.name}`}
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            lookup.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-sm">
              On
              <input className={inputClass} type="date" value={on} onChange={(e) => setOn(e.target.value)} />
            </label>
            <Button type="submit" variant="secondary" disabled={lookup.isPending}>
              Look up
            </Button>
          </div>
          <GeneralError error={lookup.error} />
          {result?.result === "price" && (
            <p role="status">
              <Money amount={result.value} currency={result.currency ?? instrument.currency} />, the Price of{" "}
              {result.as_at} ({result.source}).
            </p>
          )}
          {result?.result === "no_price" && (
            <p role="status" className="text-destructive">
              No Price on or before {result.on}. Add one; nothing is assumed.
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

/** An Instrument's Prices: history (gaps left as they are), add, correct, remove, and a lookup. */
export function PricesPanel({ instrument }: { instrument: Instrument }) {
  const queryClient = useQueryClient();
  const prices = useQuery({ queryKey: ["prices", instrument.id], queryFn: () => fetchPrices(instrument.id) });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const [adding, setAdding] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["prices", instrument.id] });
  const today = todayIn(household.data?.time_zone ?? DEFAULT_TIME_ZONE);
  const rows = prices.data ?? [];
  const visible = showAll ? rows : rows.slice(0, SHOWN);

  return (
    <section aria-label={`Prices of ${instrument.name}`} className="flex flex-col gap-3">
      <Dialog open={adding} onOpenChange={setAdding}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Prices</h2>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/audit"
              search={{ entity_type: "price" }}
              aria-label="Audit trail of Prices"
              className={buttonVariants({ variant: "outline" })}
            >
              Audit trail
            </Link>
            <DialogTrigger asChild>
              <Button>
                <Plus size={16} />
                Add Price
              </Button>
            </DialogTrigger>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          Per unit, in {instrument.currency}. A manual Price wins a same-date tie. Dates with no Price are left
          empty, never filled in.
        </p>
        {prices.isPending && <p className="text-muted-foreground">Loading…</p>}
        {prices.error && <p className="text-destructive">Could not load the Prices: {prices.error.message}</p>}
        {prices.data?.length === 0 && <p className="text-sm text-muted-foreground">No Prices yet.</p>}
        {rows.length > 0 && (
          <Table aria-label={`Price history of ${instrument.name}`}>
            <TableHeader>
              <TableRow>
                <TableHead>As at</TableHead>
                <TableHead className="text-right">Price ({instrument.currency})</TableHead>
                <TableHead className="hidden sm:table-cell">Source</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((p) => (
                <PriceRow key={p.id} instrument={instrument} today={today} price={p} onChange={refresh} />
              ))}
            </TableBody>
          </Table>
        )}
        {rows.length > SHOWN && (
          <div>
            <Button variant="outline" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${rows.length} Prices`}
            </Button>
          </div>
        )}
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add a manual Price</DialogTitle>
            <DialogDescription>
              For {instrument.name}, per unit. Say where it came from.
            </DialogDescription>
          </DialogHeader>
          <PriceForm
            instrument={instrument}
            today={today}
            onSubmit={(i) => addPrice(instrument.id, i)}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>
      <PriceLookupForm instrument={instrument} today={today} />
    </section>
  );
}

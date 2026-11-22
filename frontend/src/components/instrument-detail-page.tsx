import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { ExposureCurrencySummary, ExposureProfilePanel } from "@/components/exposure-profile-panel";
import { InstrumentForm } from "@/components/instruments-page";
import { PricesPanel } from "@/components/prices-panel";
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
  addIdentifier,
  fetchInstruments,
  kindLabel,
  removeIdentifier,
  SCHEMES,
  schemeLabel,
  updateInstrument,
  type Instrument,
  type InstrumentIdentifier,
} from "@/lib/instruments";

function IdentifierForm({
  instrument,
  onDone,
  onCancel,
}: {
  instrument: Instrument;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [scheme, setScheme] = useState<string>("name");
  const [value, setValue] = useState("");
  const add = useMutation({ mutationFn: () => addIdentifier(instrument.id, { scheme, value }), onSuccess: onDone });
  return (
    <form
      aria-label={`Add identifier to ${instrument.name}`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Scheme
        <select className={inputClass} value={scheme} onChange={(e) => setScheme(e.target.value)}>
          {SCHEMES.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
        <FieldMessage error={add.error} field="scheme" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Identifier
        <input className={inputClass} value={value} onChange={(e) => setValue(e.target.value)} />
        <FieldMessage error={add.error} field="value" />
      </label>
      <GeneralError error={add.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={add.isPending}>
          Add identifier
        </Button>
      </DialogFooter>
    </form>
  );
}

function IdentifierRow({
  instrument,
  identifier,
  onChange,
}: {
  instrument: Instrument;
  identifier: InstrumentIdentifier;
  onChange: () => void;
}) {
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const remove = useMutation({ mutationFn: () => removeIdentifier(instrument.id, identifier.id), onSuccess: onChange });
  const name = `${schemeLabel(identifier.scheme)} ${identifier.value}`;
  return (
    <TableRow>
      <TableCell>
        <Badge variant="info">{schemeLabel(identifier.scheme)}</Badge>
      </TableCell>
      <TableCell className="break-all">
        {identifier.value}
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className="w-10 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button ref={menuRef} variant="ghost" aria-label={`Actions for ${name}`} className="px-2">
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
              Remove identifier
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <AlertDialog open={removing} onOpenChange={setRemoving}>
          <AlertDialogContent
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              menuRef.current?.focus();
            }}
          >
            <AlertDialogHeader>
              <AlertDialogTitle>Remove the {name}?</AlertDialogTitle>
              <AlertDialogDescription>
                It no longer identifies {instrument.name}, so imports using it will not match.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>Remove identifier</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  );
}

function IdentifiersSection({ instrument, onChange }: { instrument: Instrument; onChange: () => void }) {
  const [adding, setAdding] = useState(false);
  return (
    <section aria-label={`Identifiers of ${instrument.name}`} className="flex flex-col gap-2">
      <Dialog open={adding} onOpenChange={setAdding}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Identifiers</h2>
          <DialogTrigger asChild>
            <Button variant="outline">
              <Plus size={16} />
              Add identifier
            </Button>
          </DialogTrigger>
        </div>
        {instrument.identifiers.length === 0 ? (
          <p className="text-sm text-muted-foreground">No identifiers yet.</p>
        ) : (
          <Table aria-label="Identifiers">
            <TableHeader>
              <TableRow>
                <TableHead>Scheme</TableHead>
                <TableHead>Identifier</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {instrument.identifiers.map((i) => (
                <IdentifierRow key={i.id} instrument={instrument} identifier={i} onChange={onChange} />
              ))}
            </TableBody>
          </Table>
        )}
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add identifier</DialogTitle>
            <DialogDescription>An identifier names exactly one Instrument.</DialogDescription>
          </DialogHeader>
          <IdentifierForm
            instrument={instrument}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              onChange();
            }}
          />
        </DialogContent>
      </Dialog>
    </section>
  );
}

function InstrumentDetail({ instrument, onChange }: { instrument: Instrument; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={instrument.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant="info">{kindLabel(instrument.kind)}</Badge>
            <Badge variant="closed">Holding currency {instrument.currency}</Badge>
            <ExposureCurrencySummary instrument={instrument} />
            {instrument.notes}
          </span>
        }
        action={
          <div className="flex flex-wrap gap-2">
            <Link
              to="/audit"
              search={{ entity_type: "instrument", entity_id: instrument.id }}
              aria-label={`Audit trail of ${instrument.name}`}
              className={buttonVariants({ variant: "outline" })}
            >
              Audit trail
            </Link>
            <Dialog open={editing} onOpenChange={setEditing}>
              <DialogTrigger asChild>
                <Button variant="outline" aria-label={`Edit ${instrument.name}`}>
                  Edit
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Edit {instrument.name}</DialogTitle>
                  <DialogDescription>Name, kind, currency and notes.</DialogDescription>
                </DialogHeader>
                <InstrumentForm
                  initial={instrument}
                  onSubmit={(input) => updateInstrument(instrument.id, input)}
                  onCancel={() => setEditing(false)}
                  onDone={() => {
                    setEditing(false);
                    onChange();
                  }}
                />
              </DialogContent>
            </Dialog>
          </div>
        }
      />
      <IdentifiersSection instrument={instrument} onChange={onChange} />
      <ExposureProfilePanel instrument={instrument} />
      <PricesPanel instrument={instrument} />
    </div>
  );
}

/** One Instrument at `/instruments/$instrumentId`: identifiers, Exposure profile and Prices as sections. */
export function InstrumentDetailPage({ instrumentId }: { instrumentId: string }) {
  const queryClient = useQueryClient();
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: fetchInstruments });
  const instrument = instruments.data?.find((i) => String(i.id) === instrumentId);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["instruments"] });

  return (
    <div className="flex flex-col gap-4">
      <Link
        to="/instruments"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={14} />
        Instruments
      </Link>
      {instruments.isPending && <p className="text-muted-foreground">Loading…</p>}
      {instruments.error && (
        <p className="text-destructive">Could not load the Instrument: {instruments.error.message}</p>
      )}
      {instruments.data && !instrument && (
        <div role="alert">
          <PageHeader title="Instrument not found" description="There is no Instrument at this address." />
        </div>
      )}
      {instrument && <InstrumentDetail instrument={instrument} onChange={refresh} />}
    </div>
  );
}

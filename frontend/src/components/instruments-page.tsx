import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  createInstrument,
  fetchInstruments,
  KINDS,
  kindLabel,
  schemeLabel,
  type Instrument,
  type InstrumentInput,
} from "@/lib/instruments";

/** Create or edit an Instrument. Rendered inside a Dialog. */
export function InstrumentForm({
  initial,
  onSubmit,
  onDone,
  onCancel,
}: {
  initial?: Instrument;
  onSubmit: (input: InstrumentInput) => Promise<unknown>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [kind, setKind] = useState<string>(initial?.kind ?? "etf");
  const [currency, setCurrency] = useState(initial?.currency ?? "AUD");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const save = useMutation({
    mutationFn: () => onSubmit({ name, kind, currency, notes: notes.trim() ? notes : null }),
    onSuccess: onDone,
  });

  return (
    <form
      aria-label={initial ? `Edit ${initial.name}` : "Add Instrument"}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        <FieldMessage error={save.error} field="name" />
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
          <FieldMessage error={save.error} field="kind" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Currency
          <input className={inputClass} value={currency} onChange={(e) => setCurrency(e.target.value)} />
          <FieldMessage error={save.error} field="currency" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        Notes
        <input className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} />
        <FieldMessage error={save.error} field="notes" />
      </label>
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save" : "Add Instrument"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function InstrumentRow({ instrument }: { instrument: Instrument }) {
  const navigate = useNavigate();
  const open = () => void navigate({ to: "/instruments/$instrumentId", params: { instrumentId: String(instrument.id) } });
  return (
    <TableRow className="cursor-pointer" onClick={open} aria-label={instrument.name}>
      <TableCell>
        <Link
          to="/instruments/$instrumentId"
          params={{ instrumentId: String(instrument.id) }}
          className="font-medium break-words hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          {instrument.name}
        </Link>
        {instrument.notes && <p className="text-xs text-muted-foreground">{instrument.notes}</p>}
      </TableCell>
      <TableCell>
        <Badge variant="info">{kindLabel(instrument.kind)}</Badge>
      </TableCell>
      <TableCell className="text-muted-foreground">{instrument.currency}</TableCell>
      <TableCell className="hidden text-muted-foreground md:table-cell">
        {instrument.identifiers.map((i) => `${schemeLabel(i.scheme)} ${i.value}`).join(" · ") || "None yet"}
      </TableCell>
    </TableRow>
  );
}

/** Instruments (things held in units and priced): a table whose rows open the detail page. */
export function InstrumentsPage() {
  const queryClient = useQueryClient();
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: fetchInstruments });
  const [adding, setAdding] = useState(false);
  const create = (input: InstrumentInput) => createInstrument({ ...input, identifiers: [] });

  return (
    <div className="flex flex-col gap-4">
      <Dialog open={adding} onOpenChange={setAdding}>
        <PageHeader
          title="Instruments"
          description="Things held in units and priced. Each identifier (ticker, ISIN, name alias…) names exactly one Instrument."
          action={
            <DialogTrigger asChild>
              <Button>
                <Plus size={16} />
                Add instrument
              </Button>
            </DialogTrigger>
          }
        />
        {instruments.isPending && <p className="text-muted-foreground">Loading…</p>}
        {instruments.error && (
          <p className="text-destructive">Could not load the Instruments: {instruments.error.message}</p>
        )}
        {instruments.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">No Instruments yet. Add one.</p>
        )}
        {instruments.data && instruments.data.length > 0 && (
          <Table aria-label="Instruments">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Currency</TableHead>
                <TableHead className="hidden md:table-cell">Identifiers</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {instruments.data.map((i) => (
                <InstrumentRow key={i.id} instrument={i} />
              ))}
            </TableBody>
          </Table>
        )}
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add instrument</DialogTitle>
            <DialogDescription>Add identifiers and Prices on its page afterwards.</DialogDescription>
          </DialogHeader>
          <InstrumentForm
            onSubmit={create}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              void queryClient.invalidateQueries({ queryKey: ["instruments"] });
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

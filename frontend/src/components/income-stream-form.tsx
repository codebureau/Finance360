import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { PeriodField } from "@/components/period-field";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import { fetchHousehold, type Member } from "@/lib/household";
import {
  createIncomeStream,
  updateIncomeStream,
  type IncomeStream,
  type IncomeStreamInput,
} from "@/lib/income-streams";

/** The types a stream can have. M3 registers `salary` only; the server rejects any other. */
const TYPES = [{ key: "salary", label: "Salary" }] as const;

function StreamForm({
  initial,
  members,
  onSaved,
}: {
  initial?: IncomeStream;
  members: Member[];
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [typeKey, setTypeKey] = useState<string>(initial?.type_key ?? TYPES[0].key);
  const [owner, setOwner] = useState<number>(initial?.owners[0]?.member_id ?? members[0]?.id ?? 0);
  const [gross, setGross] = useState(initial?.gross_annual ?? "");
  const [takeHome, setTakeHome] = useState(initial?.take_home_annual ?? "");
  const [startOn, setStartOn] = useState(initial?.start_on ?? "");
  const [endOn, setEndOn] = useState(initial?.end_on ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");

  const save = useMutation({
    mutationFn: () => {
      const input: IncomeStreamInput = {
        name,
        type_key: typeKey,
        ownership: "sole",
        credit_category: "cash",
        owners: [{ member_id: owner }],
        gross_annual: gross.trim(),
        take_home_annual: takeHome.trim() === "" ? null : takeHome.trim(),
        // A blank start is now; a blank end is the Member's retirement.
        start_on: startOn || null,
        end_on: endOn || null,
        end_event: endOn ? null : "retirement",
        notes: notes.trim() === "" ? null : notes.trim(),
      };
      return initial ? updateIncomeStream(initial.id, input) : createIncomeStream(input);
    },
    onSuccess: onSaved,
  });

  const dirtyProps = useDirtyFormProps();

  return (
    <form
      {...dirtyProps}
      aria-label={initial ? `Edit ${initial.name}` : "New income stream"}
      className="flex flex-col gap-4 overflow-y-auto pb-2"
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
      <label className="flex flex-col gap-1 text-sm">
        Type
        <select className={inputClass} value={typeKey} onChange={(e) => setTypeKey(e.target.value)}>
          {TYPES.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="type_key" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Member
        <select className={inputClass} value={owner} onChange={(e) => setOwner(Number(e.target.value))}>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="owners" />
        <FieldMessage error={save.error} field="ownership" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Gross pay a year ($)
        <input className={inputClass} inputMode="decimal" value={gross} onChange={(e) => setGross(e.target.value)} />
        <span className="text-xs text-muted-foreground">
          Today&apos;s dollars, before tax, excluding employer super.
        </span>
        <FieldMessage error={save.error} field="gross_annual" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Take-home pay a year ($)
        <input
          className={inputClass}
          inputMode="decimal"
          value={takeHome}
          onChange={(e) => setTakeHome(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          After tax. You enter it: tax is not calculated yet. This is what funds spending.
        </span>
        <FieldMessage error={save.error} field="take_home_annual" />
      </label>
      <PeriodField
        legend="When it runs"
        from={startOn}
        to={endOn}
        onChange={(from, to) => {
          setStartOn(from);
          setEndOn(to);
        }}
        allowBlank
        fromLabel="Starts"
        toLabel="Ends"
        fromHint="Blank: now."
        toHint="Blank: at the Member's retirement."
        warn={["end_before_start"]}
        error={save.error}
      />
      <FieldMessage error={save.error} field="end_event" />
      <label className="flex flex-col gap-1 text-sm">
        Notes
        <textarea className={`${inputClass} py-2`} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        <FieldMessage error={save.error} field="notes" />
      </label>
      <GeneralError error={save.error} />
      <div className="flex justify-end gap-2">
        <SheetClose asChild>
          <Button variant="ghost">Cancel</Button>
        </SheetClose>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save" : "Create income stream"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Create (no `stream`) or edit an income stream in a Sheet opened from `trigger`. The form mounts
 * only while open, so each opening starts from the stream's current values; a failed save keeps
 * the Sheet open with the message beside the field.
 */
export function IncomeStreamSheet({
  stream,
  trigger,
  onSaved,
}: {
  stream?: IncomeStream;
  trigger: ReactNode;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const members = household.data?.members ?? [];
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">{stream ? `Edit ${stream.name}` : "New income stream"}</SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          {stream
            ? "Changes apply to this income stream only. Saved projection runs keep what they used."
            : "A salary pays take-home pay into Cash until the Member retires, and employer super into their Superannuation."}
        </SheetDescription>
        {household.data ? (
          members.length === 0 ? (
            <p className="text-sm text-muted-foreground">Add a Member in Household first.</p>
          ) : (
            <StreamForm
              initial={stream}
              members={members}
              onSaved={() => {
                setOpen(false);
                onSaved();
              }}
            />
          )
        ) : (
          <p className="text-muted-foreground">Loading…</p>
        )}
      </SheetContent>
    </Sheet>
  );
}

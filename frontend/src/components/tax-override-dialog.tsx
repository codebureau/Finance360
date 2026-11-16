import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { inputClass } from "@/components/form-fields";
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
import { ApiError, type Member } from "@/lib/household";
import { fetchTaxRecords, fetchTaxSetup, type TaxLabel } from "@/lib/tax";
import { OVERRIDE_OPS, type TaxOverrideInput } from "@/lib/tax-estimates";

/** Every message of a rejected request: a what-if's errors are keyed by the override they are about. */
export function AllErrors({ error }: { error: unknown }) {
  if (!(error instanceof Error)) return null;
  const messages =
    error instanceof ApiError && Object.keys(error.fields).length > 0 ? Object.values(error.fields) : [error.message];
  return (
    <div role="alert" className="text-sm text-destructive">
      {messages.map((m) => (
        <p key={m}>{m}</p>
      ))}
    </div>
  );
}

type Props = {
  financialYear: number;
  members: Member[];
  /** Saves the whole list with this override added; rejects with the server's errors. */
  onAdd: (override: TaxOverrideInput) => Promise<void>;
  error: unknown;
};

/** The hypothetical record types the screen offers. The service takes any type; these two cover
 * an amount and a purchase, and the others are entered as records. */
const RECORD_TYPES = [
  { key: "line", label: "An amount (income or a cost)" },
  { key: "depreciating_asset", label: "A depreciating asset (a purchase)" },
] as const;

function Form({ financialYear, members, onAdd, error, onDone }: Props & { onDone: () => void }) {
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const records = useQuery({
    queryKey: ["tax-records", { fy: financialYear }],
    queryFn: () => fetchTaxRecords({ fy: financialYear }),
  });
  const [op, setOp] = useState<string>("set");
  const [memberId, setMemberId] = useState<number>(members[0]?.id ?? 0);
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [recordId, setRecordId] = useState<number>(0);
  const [type, setType] = useState<string>("line");
  const [description, setDescription] = useState("");
  const [fromOn, setFromOn] = useState(`${financialYear - 1}-07-01`);
  const [amount, setAmount] = useState("");
  const [workUse, setWorkUse] = useState("");
  const [life, setLife] = useState("");
  const [inFull, setInFull] = useState(false);
  const [pending, setPending] = useState(false);

  const labels: TaxLabel[] = (setup.data?.labels ?? []).filter((l) => l.direction !== "memo");
  const chosen = labels.find((l) => l.key === label);
  const forRecord = labels.filter((l) => l.kind === "money" && l.record_types.includes(type));

  function build(): TaxOverrideInput {
    if (op === "exclude_record") return { op, record_id: recordId || null };
    if (op === "exclude_label") return { op, member_id: memberId, label };
    if (op === "add_record") {
      return {
        op,
        record: {
          member_id: memberId,
          record_type: type,
          label,
          from_on: fromOn,
          description,
          amount,
          work_use_percent: workUse || null,
          ...(type === "depreciating_asset" ? { effective_life_years: life, claim_in_full: inFull } : {}),
        },
      };
    }
    return {
      op,
      member_id: memberId,
      label,
      amount: chosen?.kind === "money" ? value : null,
      days: chosen?.kind === "days" ? Number(value) : null,
      flag: chosen?.kind === "flag" ? value === "yes" : null,
    };
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setPending(true);
        onAdd(build())
          .then(onDone)
          .catch(() => undefined)
          .finally(() => setPending(false));
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        What to do
        <select className={inputClass} value={op} onChange={(e) => setOp(e.target.value)}>
          {OVERRIDE_OPS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {op !== "exclude_record" && (
        <label className="flex flex-col gap-1 text-sm">
          Member
          <select className={inputClass} value={memberId} onChange={(e) => setMemberId(Number(e.target.value))}>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {(op === "set" || op === "exclude_label") && (
        <label className="flex flex-col gap-1 text-sm">
          Return label
          <select className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)}>
            <option value="">Choose a label</option>
            {labels.map((l) => (
              <option key={l.key} value={l.key}>
                {l.name}
                {l.return_item ? ` (item ${l.return_item})` : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      {op === "set" && chosen && (
        <label className="flex flex-col gap-1 text-sm">
          {chosen.kind === "money" ? "The total, in dollars" : chosen.kind === "days" ? "The number of days" : "Is it so?"}
          {chosen.kind === "flag" ? (
            <select className={inputClass} value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">Choose</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          ) : (
            <input
              className={inputClass}
              inputMode={chosen.kind === "money" ? "decimal" : "numeric"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
          <span className="text-xs text-muted-foreground">
            This is the whole total, whatever the records add up to. It is not a change on top of them.
          </span>
        </label>
      )}

      {op === "exclude_record" && (
        <label className="flex flex-col gap-1 text-sm">
          Record to leave out
          <select className={inputClass} value={recordId} onChange={(e) => setRecordId(Number(e.target.value))}>
            <option value={0}>Choose a record</option>
            {(records.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.description} ({r.member_name?.split(" ")[0]})
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            The record stays in your register. Only this what-if leaves it out.
          </span>
        </label>
      )}

      {op === "add_record" && (
        <>
          <label className="flex flex-col gap-1 text-sm">
            Type
            <select
              className={inputClass}
              value={type}
              onChange={(e) => {
                setType(e.target.value);
                setLabel("");
              }}
            >
              {RECORD_TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Return label
            <select className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)}>
              <option value="">Choose a label</option>
              {forRecord.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Description
            <input className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {type === "depreciating_asset" ? "Date bought" : "Date"}
            <input className={inputClass} type="date" value={fromOn} onChange={(e) => setFromOn(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {type === "depreciating_asset" ? "Cost, in dollars" : "Amount, in dollars"}
            <input className={inputClass} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          {type === "depreciating_asset" && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                Effective life, in years
                <input className={inputClass} inputMode="decimal" value={life} onChange={(e) => setLife(e.target.value)} />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={inFull} onChange={(e) => setInFull(e.target.checked)} />
                Claim in full in the year bought (check the asset is eligible)
              </label>
            </>
          )}
          <label className="flex flex-col gap-1 text-sm">
            Used for work, percent (optional)
            <input className={inputClass} inputMode="decimal" value={workUse} onChange={(e) => setWorkUse(e.target.value)} />
          </label>
        </>
      )}

      <AllErrors error={error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          Add override
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Add one override to a Tax what-if. It saves the what-if's whole list, so the dialog stays open
 * with the server's message beside the field when the override is not accepted. */
export function OverrideDialog(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">Add override</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add override</DialogTitle>
          <DialogDescription>
            An override changes this what-if only. Your Tax records are never touched.
          </DialogDescription>
        </DialogHeader>
        {open && <Form {...props} onDone={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  );
}

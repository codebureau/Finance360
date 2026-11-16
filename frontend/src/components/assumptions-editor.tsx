import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
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
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import type { Member } from "@/lib/household";
import { CATEGORY_KEYS, CATEGORY_LABEL } from "@/lib/projection";
import {
  addScenario,
  fetchLayer,
  saveAssumptions,
  setDrawdownOrder,
  shownValue,
  slotsFor,
  type Change,
  type Layer,
  type Review,
  type Scenario,
  type Slot,
} from "@/lib/scenarios";

function Field({
  slot,
  value,
  placeholder,
  hint,
  error,
  onChange,
}: {
  slot: Slot;
  value: string;
  placeholder?: string;
  hint?: string;
  error: unknown;
  onChange: (value: string) => void;
}) {
  const id = `assumption-${slot.id}`;
  return (
    <div className="flex min-w-0 flex-col gap-1 text-sm">
      <label htmlFor={id}>{slot.label}</label>
      {slot.choices ? (
        <select id={id} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">{placeholder ? `Not stated (${placeholder})` : "Not stated"}</option>
          {slot.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          className={inputClass}
          inputMode={slot.unit === "years" ? "numeric" : "decimal"}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {(hint ?? slot.hint) && <span className="text-xs text-muted-foreground">{hint ?? slot.hint}</span>}
      <FieldMessage error={error} field={slot.id} />
    </div>
  );
}

const asMap = (layer: Layer) =>
  Object.fromEntries(layer.rows.map((r) => [r.scope_ref ? `${r.key}.${r.scope_ref}` : r.key, r]));

function overrideHint(names: string[]): string {
  return `${names.join(", ")} ${names.length === 1 ? "states its" : "state their"} own value, so will not move.`;
}

/**
 * A Scenario's Drawdown policy: the order Categories fund spending in. Moving a Category makes the
 * order the Scenario's own; Reset goes back to the default. Held in the form until Save.
 */
function DrawdownOrderField({
  order,
  isDefault,
  error,
  onMove,
  onReset,
}: {
  order: string[];
  isDefault: boolean;
  error: unknown;
  onMove: (index: number, by: -1 | 1) => void;
  onReset: () => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2" data-testid="drawdown-order-editor">
      <legend className="mb-1 text-sm font-medium">Drawdown order</legend>
      <p className="text-xs text-muted-foreground">
        Spending is drawn from the first Category until it runs out, then the next.{" "}
        <span>{isDefault ? "This is the default order." : "This Scenario states its own order."}</span>
      </p>
      <ol className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
        {order.map((key, i) => (
          <li key={key} className="flex items-center justify-between gap-2 px-3 py-1">
            <span>
              <span className="mr-2 text-muted-foreground tabular-nums">{i + 1}.</span>
              {CATEGORY_LABEL[key] ?? key}
            </span>
            <span className="flex gap-1">
              <Button
                variant="ghost"
                aria-label={`Move ${CATEGORY_LABEL[key] ?? key} up`}
                disabled={i === 0}
                onClick={() => onMove(i, -1)}
              >
                <ChevronDown size={16} aria-hidden className="rotate-180" />
              </Button>
              <Button
                variant="ghost"
                aria-label={`Move ${CATEGORY_LABEL[key] ?? key} down`}
                disabled={i === order.length - 1}
                onClick={() => onMove(i, 1)}
              >
                <ChevronDown size={16} aria-hidden />
              </Button>
            </span>
          </li>
        ))}
      </ol>
      <FieldMessage error={error} field="drawdown_order" />
      <div>
        <Button variant="outline" disabled={isDefault} onClick={onReset}>
          Reset to default order
        </Button>
      </div>
    </fieldset>
  );
}

function AssumptionsForm({
  scenario,
  members,
  layer,
  defaults,
  onSaved,
}: {
  scenario: Scenario | null;
  members: Member[];
  layer: Layer;
  /** The Household defaults, shown as what an empty Scenario field inherits. */
  defaults: Layer | null;
  onSaved: (review: Review[]) => void;
}) {
  const slots = slotsFor(members);
  const stored = asMap(layer);
  const inherited = defaults ? asMap(defaults) : {};
  const [text, setText] = useState<Record<string, string>>(() =>
    Object.fromEntries(slots.map((s) => [s.id, stored[s.id]?.value ?? ""])),
  );
  const [order, setOrder] = useState<string[]>(() =>
    scenario ? scenario.drawdown_order.map((c) => c.key) : [...CATEGORY_KEYS],
  );
  const [orderIsDefault, setOrderIsDefault] = useState(scenario?.drawdown_is_default ?? true);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: async () => {
      const changes: Change[] = slots
        .filter((s) => text[s.id].trim() !== (stored[s.id]?.value ?? ""))
        .map((s) => ({
          key: s.key,
          scope_type: s.scope_type,
          scope_ref: s.scope_ref,
          value: text[s.id].trim() === "" ? null : text[s.id].trim(),
        }));
      const saved = await saveAssumptions(scenario?.id ?? null, changes);
      const was = scenario?.drawdown_order.map((c) => c.key).join() ?? "";
      const changed = scenario && (orderIsDefault !== scenario.drawdown_is_default || (!orderIsDefault && order.join() !== was));
      if (scenario && changed) await setDrawdownOrder(scenario.id, orderIsDefault ? null : order);
      return saved;
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["projection"] });
      await queryClient.invalidateQueries({ queryKey: ["scenarios"] });
      await queryClient.invalidateQueries({ queryKey: ["assumptions"] });
      onSaved(saved.review);
    },
  });

  const group = (title: string, among: Slot[]) =>
    among.length > 0 && (
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-medium">{title}</legend>
        {among.map((s) => (
          <Field
            key={s.id}
            slot={s}
            value={text[s.id]}
            error={save.error}
            placeholder={scenario ? inherited[s.id]?.value : undefined}
            hint={
              !scenario && stored[s.id]?.overridden_by.length
                ? overrideHint(stored[s.id].overridden_by)
                : undefined
            }
            onChange={(value) => setText((t) => ({ ...t, [s.id]: value }))}
          />
        ))}
      </fieldset>
    );

  const dirtyProps = useDirtyFormProps();

  return (
    <form
      {...dirtyProps}
      aria-label={scenario ? `Edit ${scenario.name}` : "Edit Household defaults"}
      className="flex flex-col gap-5 overflow-y-auto pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {group("Returns", slots.filter((s) => s.key === "return_rate"))}
      {group(
        "Household",
        slots.filter((s) => s.scope_type === "household" && !["real_wage_growth", "tax_in_projection"].includes(s.key)),
      )}
      {group("Pay", slots.filter((s) => s.key === "real_wage_growth"))}
      {group("Retirement", slots.filter((s) => s.key === "retirement_age"))}
      {group("Tax", slots.filter((s) => s.key === "tax_in_projection" || s.key === "div293_payer"))}
      {scenario && (
        <DrawdownOrderField
          order={order}
          isDefault={orderIsDefault}
          error={save.error}
          onMove={(i, by) => {
            setOrder((o) => {
              const next = [...o];
              [next[i], next[i + by]] = [next[i + by], next[i]];
              return next;
            });
            setOrderIsDefault(false);
          }}
          onReset={() => {
            setOrder([...CATEGORY_KEYS]);
            setOrderIsDefault(true);
          }}
        />
      )}
      <GeneralError error={save.error} />
      <div className="flex justify-end gap-2">
        <SheetClose asChild>
          <Button variant="ghost">Cancel</Button>
        </SheetClose>
        <Button type="submit" disabled={save.isPending}>
          Save
        </Button>
      </div>
    </form>
  );
}

/**
 * Edit the Assumptions a Scenario states (`scenario`), or the Household defaults (null), in a
 * Sheet opened from `trigger`. The form mounts only while open, so each opening starts from what is
 * stored. An empty field means "not stated": a Scenario inherits it, a default is unset.
 */
export function AssumptionsSheet({
  scenario,
  members,
  trigger,
  onSaved,
}: {
  scenario: Scenario | null;
  members: Member[];
  trigger: ReactNode;
  onSaved: (review: Review[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const layer = useQuery({
    queryKey: ["assumptions", scenario?.id ?? null],
    queryFn: () => fetchLayer(scenario?.id ?? null),
    enabled: open,
    gcTime: 0,
  });
  const defaults = useQuery({
    queryKey: ["assumptions", null],
    queryFn: () => fetchLayer(null),
    enabled: open && scenario !== null,
    gcTime: 0,
  });
  const ready = layer.data && (scenario === null || defaults.data);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">
          {scenario ? `Edit ${scenario.name}` : "Edit Household defaults"}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          {scenario
            ? "Fill in only what differs from the Household defaults. Leave a field empty to inherit it."
            : "Used by every Scenario that does not state its own value. Amounts are in today's dollars."}
        </SheetDescription>
        <GeneralError error={layer.error ?? defaults.error} />
        {ready ? (
          <AssumptionsForm
            scenario={scenario}
            members={members}
            layer={layer.data}
            defaults={defaults.data ?? null}
            onSaved={(review) => {
              setOpen(false);
              onSaved(review);
            }}
          />
        ) : (
          <p className="text-muted-foreground">Loading…</p>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** The order Categories fund spending in, as one line: "Cash, then Investments, then ...". */
export function DrawdownOrderLine({ categories }: { categories: { key: string; name: string }[] }) {
  return (
    <p className="text-sm" data-testid="drawdown-order">
      <span className="font-medium">Drawdown order:</span> {categories.map((c) => c.name).join(", then ")}.
    </p>
  );
}

/** Add a Scenario, optionally copying another's Assumptions. */
export function AddScenarioDialog({
  copyFrom,
  trigger,
  onAdded,
}: {
  copyFrom: Scenario | null;
  trigger: ReactNode;
  onAdded: (added: Scenario) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [copy, setCopy] = useState(true);
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: () => addScenario(name, copy && copyFrom ? copyFrom.id : null),
    onSuccess: async (added) => {
      await queryClient.invalidateQueries({ queryKey: ["scenarios"] });
      setOpen(false);
      setName("");
      onAdded(added);
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Scenario</DialogTitle>
          <DialogDescription>A Scenario states only what differs from the Household defaults.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Name
            <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
            <FieldMessage error={add.error} field="name" />
          </label>
          {copyFrom && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} />
              Start from the Assumptions in {copyFrom.name}
            </label>
          )}
          <GeneralError error={add.error} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={add.isPending}>
              Add Scenario
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export { shownValue };

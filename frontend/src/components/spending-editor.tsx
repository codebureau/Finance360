import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Money } from "@/components/ui/money";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import { ANCHOR_OPTIONS, phaseStart } from "@/lib/projection";
import {
  fetchSpending,
  saveSpending,
  type Review,
  type Scenario,
  type SpendingLayer,
  type SpendingPhaseInput,
  type SpendingUsed,
} from "@/lib/scenarios";

/** One phase as the form holds it: text, until Save. */
type Row = { anchor: string; offset: string; amount: string };

const toRows = (layer: SpendingLayer): Row[] =>
  layer.phases.map((p) => ({ anchor: p.anchor, offset: String(p.offset_years), amount: p.annual_amount }));

/** A whole number from the text, or -1 so the server reports the field (it never sees a NaN). */
const wholeYears = (text: string) => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : -1);

/** A read-only list of phases (the Household default, shown to a Scenario that inherits it). */
function PhaseSummary({ layer }: { layer: SpendingLayer }) {
  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm" data-testid="inherited-phases">
      {layer.phases.map((p) => (
        <li
          key={`${p.anchor}-${p.offset_years}`}
          className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]"
        >
          <span>{phaseStart(p.anchor, p.offset_years)}</span>
          <span>
            <Money amount={p.annual_amount} /> <span className="text-muted-foreground">a year</span>
          </span>
        </li>
      ))}
      {layer.phases.length === 0 && (
        <li className="px-3 py-[var(--cell-py)] text-muted-foreground">The Household default has no phases yet.</li>
      )}
    </ul>
  );
}

function PhaseRow({
  n,
  row,
  error,
  onChange,
  onRemove,
}: {
  n: number;
  row: Row;
  error: unknown;
  onChange: (next: Row) => void;
  onRemove: () => void;
}) {
  const field = `spending_phases.${n - 1}`;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3" data-testid="phase-row">
      <p className="text-sm font-medium">Phase {n}</p>
      <div className="flex flex-col gap-1 text-sm">
        <label htmlFor={`phase-${n}-anchor`}>Starts after</label>
        <select
          id={`phase-${n}-anchor`}
          aria-label={`Phase ${n} starts after`}
          className={inputClass}
          value={row.anchor}
          onChange={(e) => onChange({ ...row, anchor: e.target.value })}
        >
          {ANCHOR_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <FieldMessage error={error} field={`${field}.anchor`} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex min-w-0 flex-col gap-1 text-sm">
          <label htmlFor={`phase-${n}-offset`}>Years after</label>
          <input
            id={`phase-${n}-offset`}
            aria-label={`Phase ${n} years after`}
            className={inputClass}
            inputMode="numeric"
            value={row.offset}
            onChange={(e) => onChange({ ...row, offset: e.target.value })}
          />
          <FieldMessage error={error} field={`${field}.offset_years`} />
        </div>
        <div className="flex min-w-0 flex-col gap-1 text-sm">
          <label htmlFor={`phase-${n}-amount`}>A year ($)</label>
          <input
            id={`phase-${n}-amount`}
            aria-label={`Phase ${n} amount a year`}
            className={inputClass}
            inputMode="decimal"
            value={row.amount}
            onChange={(e) => onChange({ ...row, amount: e.target.value })}
          />
          <FieldMessage error={error} field={`${field}.annual_amount`} />
        </div>
      </div>
      <div>
        <Button variant="ghost" aria-label={`Remove phase ${n}`} onClick={onRemove}>
          Remove
        </Button>
      </div>
    </li>
  );
}

function SpendingForm({
  scenario,
  layer,
  defaults,
  onSaved,
}: {
  scenario: Scenario | null;
  layer: SpendingLayer;
  /** The Household default list: what a Scenario inherits, and where its own list starts from. */
  defaults: SpendingLayer | null;
  onSaved: (review: Review[]) => void;
}) {
  const [rows, setRows] = useState<Row[]>(() => toRows(layer));
  // A Household list is always its own; a Scenario's is its own only if it states one.
  const [own, setOwn] = useState(scenario === null || layer.phases.length > 0);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => {
      const phases: SpendingPhaseInput[] = rows.map((r) => ({
        anchor: r.anchor,
        offset_years: wholeYears(r.offset),
        annual_amount: r.amount.trim(),
      }));
      return saveSpending(scenario?.id ?? null, own ? phases : null);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["projection"] });
      await queryClient.invalidateQueries({ queryKey: ["scenarios"] });
      await queryClient.invalidateQueries({ queryKey: ["spending"] });
      await queryClient.invalidateQueries({ queryKey: ["projection-run"] });
      onSaved(saved.review);
    },
  });
  const patch = (i: number, next: Row) => setRows((all) => all.map((r, at) => (at === i ? next : r)));

  const dirtyProps = useDirtyFormProps();

  return (
    <form
      {...dirtyProps}
      aria-label={scenario ? `Edit ${scenario.name} spending` : "Edit Household spending"}
      className="flex flex-col gap-4 overflow-y-auto pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {scenario && !own && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground" data-testid="spending-inherited">
            {scenario.name} uses the Household default phases. Give it its own list to change them for this Scenario
            only.
          </p>
          {defaults && <PhaseSummary layer={defaults} />}
          <div>
            <Button
              variant="outline"
              onClick={() => {
                setRows(defaults ? toRows(defaults) : []);
                setOwn(true);
              }}
            >
              Give {scenario.name} its own phases
            </Button>
          </div>
        </div>
      )}
      {own && (
        <div className="flex flex-col gap-3">
          {scenario && (
            <p className="text-xs text-muted-foreground">
              This list replaces the Household default as a whole: a phase you leave out here is not inherited.
            </p>
          )}
          <ol className="flex flex-col gap-3">
            {rows.map((row, i) => (
              <PhaseRow
                key={i}
                n={i + 1}
                row={row}
                error={save.error}
                onChange={(next) => patch(i, next)}
                onRemove={() => setRows((all) => all.filter((_, at) => at !== i))}
              />
            ))}
          </ol>
          {rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {scenario ? "No phases: Save to use the Household default." : "No phases: spending is not set."}
            </p>
          )}
          <div>
            <Button
              variant="outline"
              onClick={() =>
                setRows((all) => [
                  ...all,
                  { anchor: "last_retirement", offset: all.length === 0 ? "0" : "", amount: "" },
                ])
              }
            >
              <Plus size={16} aria-hidden /> Add phase
            </Button>
          </div>
          {scenario && (
            <div>
              <Button
                variant="ghost"
                onClick={() => {
                  setOwn(false);
                  setRows([]);
                }}
              >
                Use the Household default phases
              </Button>
            </div>
          )}
        </div>
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
 * Edit a Scenario's spending phases (`scenario`), or the Household default list (null), in a Sheet
 * opened from `trigger`. A list is read and written whole: a Scenario's replaces the default.
 */
export function SpendingSheet({
  scenario,
  trigger,
  onSaved,
}: {
  scenario: Scenario | null;
  trigger: ReactNode;
  onSaved: (review: Review[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const layer = useQuery({
    queryKey: ["spending", scenario?.id ?? null],
    queryFn: () => fetchSpending(scenario?.id ?? null),
    enabled: open,
    gcTime: 0,
  });
  const defaults = useQuery({
    queryKey: ["spending", null],
    queryFn: () => fetchSpending(null),
    enabled: open && scenario !== null,
    gcTime: 0,
  });
  const ready = layer.data && (scenario === null || defaults.data);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">
          {scenario ? `Edit ${scenario.name} spending` : "Edit Household spending"}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          Annual spending in today&apos;s dollars, in phases. Each phase runs until the next starts; the last runs to
          the horizon. Anchored to the last retirement, a phase moves when a Scenario retires earlier or later.
        </SheetDescription>
        <GeneralError error={layer.error ?? defaults.error} />
        {ready ? (
          <SpendingForm
            scenario={scenario}
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

/**
 * The spending phases a projection used, resolved to ages (the youngest Member's) and which layer
 * supplied them. `spending` is null for a run saved before spending phases were recorded.
 */
export function SpendingPhasesUsed({
  spending,
  referenceAge,
  years,
  scenarioName,
}: {
  spending: SpendingUsed | null | undefined;
  /** The youngest Member's age at the start, to turn a year index into an age. */
  referenceAge: number;
  years: number;
  scenarioName: string;
}) {
  if (!spending) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="spending-phases">
        This run was saved before spending phases were recorded, so it has no phase list. Its annual spending is
        in the Assumptions above.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-testid="spending-phases">
      <h3 className="flex flex-wrap items-center gap-2 text-sm font-medium">
        Spending phases
        {spending.layer === "scenario" && <Badge variant="info">{scenarioName}</Badge>}
        {spending.layer === "household" && <Badge variant="closed">Household default</Badge>}
      </h3>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card text-sm">
        {spending.phases.map((p) => (
          <li
            key={`${p.anchor}-${p.offset_years}`}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-[var(--cell-py)]"
            data-testid="spending-phase"
          >
            <span>
              {phaseStart(p.anchor, p.offset_years)}
              <span className="block text-xs text-muted-foreground">
                {p.until_year === null
                  ? `From age ${referenceAge + p.from_year} to the horizon`
                  : `From age ${referenceAge + p.from_year} to ${referenceAge + p.until_year}`}
              </span>
            </span>
            <span className="flex flex-wrap items-center gap-2">
              {p.empty && <Badge variant="closed">Replaced</Badge>}
              {!p.empty && p.from_year >= years && <Badge variant="closed">After the horizon</Badge>}
              <span>
                <Money amount={p.annual_amount} /> <span className="text-muted-foreground">a year</span>
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Ages are the youngest Member&apos;s. Nothing is spent before the first phase. Where two phases start the same
        year, the one from the last retirement wins.
      </p>
    </div>
  );
}

import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { Button } from "@/components/ui/button";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { ApiError } from "@/lib/household";
import {
  addSuperStatement,
  CHECK_LABELS,
  checkSuperStatement,
  correctSuperStatement,
  OUTFLOW_FIELDS,
  RECEIVED_FIELDS,
  ROLL_FIELDS,
  type SuperCheck,
  type SuperStatement,
  type SuperStatementInput,
} from "@/lib/super-statements";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";

type Account = { id: number; name: string; currency: string };
type OptionDraft = { name: string; units: string; unit_price: string; value: string };
type AmountKey =
  | (typeof ROLL_FIELDS)[number]["key"]
  | (typeof OUTFLOW_FIELDS)[number]["key"]
  | (typeof RECEIVED_FIELDS)[number]["key"];

const ALL_AMOUNTS: AmountKey[] = [...ROLL_FIELDS, ...OUTFLOW_FIELDS, ...RECEIVED_FIELDS].map(
  (f) => f.key,
);
const OPTIONAL_KEYS: AmountKey[] = [...OUTFLOW_FIELDS, ...RECEIVED_FIELDS].map((f) => f.key);
const CHECK_DEBOUNCE_MS = 300;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** The newest ended financial year that has no Super statement yet. */
function defaultYear(taken: readonly number[]): number {
  for (let y = currentFinancialYear() - 1; y > currentFinancialYear() - 10; y--) {
    if (!taken.includes(y)) return y;
  }
  return currentFinancialYear() - 1;
}

function draftAmounts(statement?: SuperStatement): Record<AmountKey, string> {
  return Object.fromEntries(
    ALL_AMOUNTS.map((k) => [k, (statement?.[k] as string | null | undefined) ?? ""]),
  ) as Record<AmountKey, string>;
}

/** The request body for a draft. A blank optional figure is "not stated" (null), never zero. */
function toInput(
  year: number,
  amounts: Record<AmountKey, string>,
  options: OptionDraft[],
  ticked: string[],
): SuperStatementInput {
  const optional = (k: AmountKey) => (amounts[k].trim() === "" ? null : amounts[k]);
  return {
    period_start: `${year - 1}-07-01`,
    period_end: `${year}-06-30`,
    opening: amounts.opening,
    contributions_in: amounts.contributions_in,
    investment_returns: amounts.investment_returns,
    withdrawals: amounts.withdrawals,
    closing: amounts.closing,
    ...Object.fromEntries(OPTIONAL_KEYS.map((k) => [k, optional(k)])),
    options: options.map((o) => ({
      name: o.name,
      value: o.value,
      units: o.units.trim() === "" ? null : o.units,
      unit_price: o.unit_price.trim() === "" ? null : o.unit_price,
    })),
    ticked_checks: ticked,
  };
}

function MoneyField({
  label,
  value,
  onChange,
  error,
  field,
  currency,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  error: unknown;
  field: string;
  currency: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span>
        {label} <span className="text-muted-foreground">({currency})</span>
      </span>
      <input
        className={`${inputClass} text-right font-mono`}
        inputMode="decimal"
        placeholder="0.00"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <FieldMessage error={error} field={field} />
    </label>
  );
}

/** A failed check, beside the figures it is about, with the tick that lets the owner save. */
function CheckNotice({
  check,
  ticked,
  onTick,
}: {
  check: SuperCheck | undefined;
  ticked: boolean;
  onTick: (on: boolean) => void;
}) {
  if (!check || check.status !== "warn") return null;
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm"
    >
      <p>{check.message}</p>
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={ticked}
          onChange={(e) => onTick(e.target.checked)}
        />
        <span>I have checked this against the statement ({CHECK_LABELS[check.name]}).</span>
      </label>
    </div>
  );
}

function SuperStatementForm({
  account,
  initial,
  takenYears,
  onSaved,
}: {
  account: Account;
  initial?: SuperStatement;
  takenYears: readonly number[];
  onSaved: () => void;
}) {
  const dirtyProps = useDirtyFormProps();
  const [year, setYear] = useState(initial?.financial_year ?? defaultYear(takenYears));
  const [amounts, setAmounts] = useState(() => draftAmounts(initial));
  const [options, setOptions] = useState<OptionDraft[]>(() =>
    (initial?.options ?? []).map((o) => ({
      name: o.name,
      units: o.units ?? "",
      unit_price: o.unit_price ?? "",
      value: o.value,
    })),
  );
  // A tick answers the message it was given for; `true` is a tick saved with the statement.
  const [ticks, setTicks] = useState<Record<string, string | true>>(() =>
    Object.fromEntries((initial?.ticked_checks ?? []).map((n) => [n, true as const])),
  );
  const [showOutflows, setShowOutflows] = useState(() =>
    OUTFLOW_FIELDS.some((f) => initial?.[f.key] != null),
  );
  const setAmount = (k: AmountKey, v: string) => setAmounts((a) => ({ ...a, [k]: v }));
  const setOption = (i: number, patch: Partial<OptionDraft>) =>
    setOptions((list) => list.map((o, at) => (at === i ? { ...o, ...patch } : o)));

  const draft = toInput(year, amounts, options, []);
  // The live check follows the figures a moment after typing stops, and only once the roll is whole.
  const settled = useDebounced(JSON.stringify(draft), CHECK_DEBOUNCE_MS);
  const settledDraft = JSON.parse(settled) as SuperStatementInput;
  const rollFilled = ROLL_FIELDS.every((f) => settledDraft[f.key].trim() !== "");
  const checks = useQuery({
    queryKey: ["super-check", account.id, settled],
    queryFn: () => checkSuperStatement(account.id, settledDraft),
    enabled: rollFilled,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const checkList = rollFilled && !checks.isError ? (checks.data ?? []) : [];

  const save = useMutation({
    mutationFn: (input: SuperStatementInput) =>
      initial ? correctSuperStatement(initial.id, input) : addSuperStatement(account.id, input),
    onSuccess: onSaved,
  });

  // A refusal that names a check (the server is the authority) shows that check even if the live
  // check had not caught up, so it can be ticked.
  const refused =
    save.error instanceof ApiError
      ? Object.entries(save.error.fields)
          .filter(([f]) => f.startsWith("checks."))
          .map(([f, message]): SuperCheck => ({
            name: f.slice("checks.".length) as SuperCheck["name"],
            status: "warn",
            message,
          }))
      : [];
  const byName = (name: SuperCheck["name"]) =>
    checkList.find((c) => c.name === name) ?? refused.find((c) => c.name === name);
  const warned = (["roll", "outflows", "options"] as const).filter(
    (n) => byName(n)?.status === "warn",
  );
  const isTicked = (name: SuperCheck["name"]) =>
    ticks[name] === true || (ticks[name] !== undefined && ticks[name] === byName(name)?.message);
  const blocksSave = warned.some((n) => !isTicked(n));
  const notice = (name: SuperCheck["name"]) => (
    <CheckNotice
      check={byName(name)}
      ticked={isTicked(name)}
      onTick={(on) =>
        setTicks((t) => {
          const next = { ...t };
          if (on) next[name] = byName(name)?.message ?? true;
          else delete next[name];
          return next;
        })
      }
    />
  );
  const years = financialYears([initial?.financial_year]).filter(
    (y) => y < currentFinancialYear() && (y === year || !takenYears.includes(y)),
  );
  const label = initial
    ? `Correct Super statement ${financialYearLabel(initial.financial_year)}`
    : `Add Super statement to ${account.name}`;
  const money = (key: AmountKey, text: string) => (
    <MoneyField
      key={key}
      label={text}
      value={amounts[key]}
      onChange={(v) => setAmount(key, v)}
      error={save.error}
      field={key}
      currency={account.currency}
    />
  );

  return (
    <form
      aria-label={label}
      className="flex flex-col gap-4"
      {...dirtyProps}
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(toInput(year, amounts, options, warned.filter(isTicked)));
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Financial year
        <FinancialYearSelect
          value={year}
          onChange={(y) => y !== undefined && setYear(y)}
          years={initial ? [initial.financial_year] : years}
        />
        {initial && (
          <span className="text-xs text-muted-foreground">
            A correction keeps the year. To change it, remove this statement and add another.
          </span>
        )}
        <FieldMessage error={save.error} field="period_start" />
      </label>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-medium">Balance roll</legend>
        {ROLL_FIELDS.map((f) => money(f.key, f.label))}
        {notice("roll")}
      </fieldset>

      <div className="flex flex-col gap-3">
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          aria-expanded={showOutflows}
          onClick={() => setShowOutflows(!showOutflows)}
        >
          {showOutflows ? "Hide outflow breakdown" : "Add outflow breakdown (optional)"}
        </Button>
        {showOutflows && (
          <fieldset className="flex flex-col gap-3">
            <legend className="mb-1 text-sm font-medium">Outflow breakdown</legend>
            <p className="text-xs text-muted-foreground">
              Leave a figure blank when the statement does not state it. Blank is not zero. The
              parts (the rebate subtracts) should add up to withdrawals. Fees taken inside the
              investment are not part of that sum.
            </p>
            {OUTFLOW_FIELDS.map((f) => money(f.key, f.label))}
            {notice("outflows")}
          </fieldset>
        )}
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-medium">Contributions received (optional)</legend>
        {RECEIVED_FIELDS.map((f) => money(f.key, f.label))}
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-medium">Investment options at 30 June (optional)</legend>
        {options.map((o, i) => (
          <div key={i} className="flex flex-col gap-2 rounded-md border border-border p-3">
            <label className="flex flex-col gap-1 text-sm">
              Option {i + 1} name
              <input
                className={inputClass}
                value={o.name}
                onChange={(e) => setOption(i, { name: e.target.value })}
              />
              <FieldMessage error={save.error} field={`options.${i}.name`} />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-sm">
                Units
                <input
                  className={`${inputClass} text-right font-mono`}
                  inputMode="decimal"
                  value={o.units}
                  aria-label={`Option ${i + 1} units`}
                  onChange={(e) => setOption(i, { units: e.target.value })}
                />
                <FieldMessage error={save.error} field={`options.${i}.units`} />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                Unit price
                <input
                  className={`${inputClass} text-right font-mono`}
                  inputMode="decimal"
                  value={o.unit_price}
                  aria-label={`Option ${i + 1} unit price`}
                  onChange={(e) => setOption(i, { unit_price: e.target.value })}
                />
                <FieldMessage error={save.error} field={`options.${i}.unit_price`} />
              </label>
            </div>
            <label className="flex flex-col gap-1 text-sm">
              Value ({account.currency})
              <input
                className={`${inputClass} text-right font-mono`}
                inputMode="decimal"
                placeholder="0.00"
                value={o.value}
                aria-label={`Option ${i + 1} value`}
                onChange={(e) => setOption(i, { value: e.target.value })}
              />
              <FieldMessage error={save.error} field={`options.${i}.value`} />
            </label>
            <Button
              type="button"
              variant="ghost"
              className="w-fit"
              aria-label={`Remove option ${i + 1}`}
              onClick={() => setOptions((list) => list.filter((_, at) => at !== i))}
            >
              <Trash2 size={16} />
              Remove option
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          onClick={() =>
            setOptions((list) => [...list, { name: "", units: "", unit_price: "", value: "" }])
          }
        >
          <Plus size={16} />
          Add option
        </Button>
        <FieldMessage error={save.error} field="options" />
        {notice("options")}
      </fieldset>

      <GeneralError error={save.error} />
      {blocksSave && (
        <p className="text-right text-xs text-muted-foreground">
          Tick that you have checked the figures to save.
        </p>
      )}
      <div className="flex justify-end gap-2">
        <SheetClose asChild>
          <Button variant="ghost">Cancel</Button>
        </SheetClose>
        <Button type="submit" disabled={save.isPending || blocksSave}>
          {initial ? "Save correction" : "Add Super statement"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Add (no `statement`) or correct a Super statement in a Sheet opened from `trigger` (ADR-0025).
 * The form mounts only while open, so each opening starts from the statement's saved figures.
 */
export function SuperStatementSheet({
  account,
  statement,
  trigger,
  open: controlledOpen,
  onOpenChange,
  takenYears,
  onSaved,
}: {
  account: Account;
  statement?: SuperStatement;
  /** The control that opens it. Omit it when `open` is driven by something else (a menu item). */
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  takenYears: readonly number[];
  onSaved: () => void;
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const open = controlledOpen ?? ownOpen;
  const setOpen = onOpenChange ?? setOwnOpen;
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      {trigger && <SheetTrigger asChild>{trigger}</SheetTrigger>}
      <SheetContent expandable className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">
          {statement
            ? `Correct the ${financialYearLabel(statement.financial_year)} Super statement`
            : `New Super statement for ${account.name}`}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          Type the figures from the fund&apos;s annual statement. Saving also records the 30 June
          Valuation.
        </SheetDescription>
        <SuperStatementForm
          account={account}
          initial={statement}
          takenYears={takenYears}
          onSaved={() => {
            setOpen(false);
            onSaved();
          }}
        />
      </SheetContent>
    </Sheet>
  );
}

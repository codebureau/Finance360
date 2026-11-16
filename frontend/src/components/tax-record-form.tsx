import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import {
  FieldMessage,
  GeneralError,
  inputClass,
} from "@/components/form-fields";
import { useCarKilometres } from "@/components/car-kilometres-entry";
import { useHomeOffice } from "@/components/home-office-entry";
import { defaultPeriod, PeriodField } from "@/components/period-field";
import { PrefillLayout } from "@/components/prefill-layout";
import { useStatementReader } from "@/components/statement-reader";
import { Button } from "@/components/ui/button";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
  useExpandSheet,
} from "@/components/ui/sheet";
import { fetchHousehold, type Member } from "@/lib/household";
import { addAndLink } from "@/lib/library";
import {
  createTaxRecord,
  fetchTaxRecords,
  fetchTaxSetup,
  financialYearOfDate,
  REQUIRED_STATEMENT_FIGURES,
  STATEMENT_GROUPS,
  TAX_RECORD_KIND,
  updateTaxRecord,
  type TaxLabel,
  type TaxRecord,
  type TaxRecordInput,
  type TaxSetup,
} from "@/lib/tax";

const TYPES = [
  { key: "line", label: "An amount" },
  { key: "fact", label: "A fact (yes or no, or days)" },
  { key: "income_statement", label: "An income statement" },
  { key: "depreciating_asset", label: "A depreciating asset" },
  { key: "home_office_hours", label: "Home office hours (monthly)" },
  { key: "car_kilometres", label: "Car kilometres (trips)" },
] as const;

/** The financial year (the year it ends in) a date falls in, or `null` for text that is not a date. */
function yearOf(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-\d{2}$/.exec(iso);
  if (!m) return null;
  return Number(m[2]) >= 7 ? Number(m[1]) + 1 : Number(m[1]);
}

/** The Return labels a plain line shares with an income statement (it feeds both). */
const STATEMENT_LABELS = ["salary", "tax_withheld"];

/**
 * The double count to warn about: a plain `salary` or `tax_withheld` line and an income statement
 * for the same Member in the same financial year both add the gross pay (or the tax) to the Label
 * total. Read from the register, so it needs no money arithmetic. It warns; it never blocks.
 */
function useDoubleCount(opts: {
  memberId: number;
  type: string;
  label: string;
  fromOn: string;
  toOn: string;
  selfId?: number;
}): TaxRecord[] {
  const years = [
    ...new Set(
      [yearOf(opts.fromOn), yearOf(opts.toOn)].filter(
        (y): y is number => y !== null,
      ),
    ),
  ];
  const relevant =
    opts.type === "income_statement" ||
    (opts.type === "line" && STATEMENT_LABELS.includes(opts.label));
  const results = useQueries({
    queries: years.map((fy) => ({
      queryKey: ["tax-records", { member: opts.memberId, fy }],
      queryFn: () => fetchTaxRecords({ member: opts.memberId, fy }),
      enabled: relevant && opts.memberId > 0,
    })),
  });
  if (!relevant) return [];
  const found = new Map<number, TaxRecord>();
  for (const r of results.flatMap((q) => q.data ?? [])) {
    if (r.id === opts.selfId) continue;
    const clash =
      opts.type === "income_statement"
        ? r.record_type === "line" && STATEMENT_LABELS.includes(r.label_key)
        : r.record_type === "income_statement";
    if (clash) found.set(r.id, r);
  }
  return [...found.values()];
}

function RecordForm({
  initial,
  members,
  setup,
  defaultMember,
  newType,
  defaultYear,
  onSaved,
}: {
  initial?: TaxRecord;
  members: Member[];
  setup: TaxSetup;
  defaultMember?: number;
  newType?: string;
  defaultYear?: number;
  onSaved: () => void;
}) {
  const [memberId, setMemberId] = useState<number>(
    initial?.member_id ?? defaultMember ?? members[0]?.id ?? 0,
  );
  const [type, setType] = useState<string>(
    initial?.record_type ?? newType ?? "line",
  );
  const [label, setLabel] = useState(
    initial?.record_type === "income_statement"
      ? ""
      : (initial?.label_key ?? ""),
  );
  const [description, setDescription] = useState(initial?.description ?? "");
  // A new record starts on the financial year being looked at (else the current one): 1 July to
  // 30 June. A depreciating asset has a start date and a last day used, not a period.
  const [fromOn, setFromOn] = useState(
    initial?.from_on ??
      (newType === "depreciating_asset" ? "" : defaultPeriod(defaultYear).from),
  );
  const [toOn, setToOn] = useState(
    initial?.to_on ??
      (newType === "depreciating_asset" ? "" : defaultPeriod(defaultYear).to),
  );
  const [figures, setFigures] = useState<Record<string, string>>(
    Object.fromEntries(
      STATEMENT_GROUPS.flatMap((g) => g.fields).map(({ key }) => [
        key,
        initial?.statement?.[
          key as keyof NonNullable<TaxRecord["statement"]>
        ] ?? "",
      ]),
    ),
  );
  const [amount, setAmount] = useState(initial?.amount ?? "");
  const [workUse, setWorkUse] = useState(initial?.work_use_percent ?? "");
  const [flag, setFlag] = useState(
    initial?.kind === "flag" ? initial.value === true : true,
  );
  const [days, setDays] = useState(
    initial?.kind === "days" ? String(initial.value ?? "") : "",
  );
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [life, setLife] = useState(initial?.asset?.effective_life_years ?? "");
  const [claimInFull, setClaimInFull] = useState(
    initial?.asset?.claim_in_full ?? false,
  );
  const home = useHomeOffice(initial?.home_office ?? undefined, fromOn, toOn);
  const carTrips = useCarKilometres(initial?.car ?? undefined);

  const available = setup.labels.filter((l) => l.record_types.includes(type));
  const chosen: TaxLabel | undefined = available.find((l) => l.key === label);

  const doubled = useDoubleCount({
    memberId,
    type,
    label,
    fromOn,
    toOn,
    selfId: initial?.id,
  });
  const statement = type === "income_statement";
  const depreciating = type === "depreciating_asset";
  const homeOffice = type === "home_office_hours";
  const carKm = type === "car_kilometres";
  // The month and trip tables need room: the Sheet opens at full width, and can be collapsed.
  useExpandSheet(homeOffice || carKm);
  const dirtyProps = useDirtyFormProps();
  const queryClient = useQueryClient();
  const reader = useStatementReader({
    financialYear: yearOf(toOn || fromOn),
    fill: (read) => setFigures((was) => ({ ...was, ...read })),
  });
  // The record saved, when its PDF is still to be filed in the Library and linked (or that failed).
  const [savedId, setSavedId] = useState<number | null>(null);
  const attach = useMutation({
    mutationFn: (id: number) => addAndLink(reader.file as File, TAX_RECORD_KIND, id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["library-links"] });
      void queryClient.invalidateQueries({ queryKey: ["library-documents"] });
      onSaved();
    },
  });

  const save = useMutation({
    mutationFn: () => {
      const input: TaxRecordInput = {
        member_id: memberId,
        record_type: type,
        label: statement ? "" : label,
        description,
        from_on: fromOn,
        to_on: toOn || null,
        notes: notes.trim() === "" ? null : notes.trim(),
      };
      if (statement) {
        // Blank figures are left out: the server asks for the four required and defaults the rest to 0.
        input.statement = Object.fromEntries(
          Object.entries(figures)
            .map(([key, text]) => [key, text.trim()])
            .filter(([, text]) => text !== ""),
        );
      } else if (type === "line") {
        input.amount = amount.trim();
        input.work_use_percent = workUse.trim() === "" ? null : workUse.trim();
      } else if (depreciating) {
        input.amount = amount.trim();
        input.work_use_percent = workUse.trim() === "" ? null : workUse.trim();
        input.effective_life_years = life.trim();
        input.claim_in_full = claimInFull;
      } else if (homeOffice) {
        home.apply(input);
      } else if (carKm) {
        carTrips.apply(input);
      } else if (chosen?.kind === "days") {
        input.value = /^\d+$/.test(days.trim()) ? Number(days.trim()) : null;
      } else {
        input.value = flag;
      }
      return initial
        ? updateTaxRecord(initial.id, input)
        : createTaxRecord(input);
    },
    onSuccess: (record) => {
      if (statement && reader.file) {
        setSavedId(record.id);
        attach.mutate(record.id);
      } else {
        onSaved();
      }
    },
  });

  const form = (
    <form
      {...dirtyProps}
      aria-label={
        initial
          ? `Edit ${initial.description}`
          : statement
            ? "New income statement"
            : "New Tax record"
      }
      className="flex flex-col gap-4 overflow-y-auto pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Member
        <select
          className={inputClass}
          value={memberId}
          onChange={(e) => setMemberId(Number(e.target.value))}
        >
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        {!statement && (
          <span className="text-xs text-muted-foreground">
            A joint cost is entered once for each Member, as their share.
          </span>
        )}
        <FieldMessage error={save.error} field="member_id" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        What it records
        <select
          className={inputClass}
          value={type}
          onChange={(e) => {
            setType(e.target.value);
            setLabel("");
            if (!initial) {
              // Only a depreciating asset starts on blank dates; the rest start on a year.
              const { from, to } = defaultPeriod(defaultYear);
              const asset = e.target.value === "depreciating_asset";
              setFromOn(asset ? "" : from);
              setToOn(asset ? "" : to);
            }
          }}
        >
          {TYPES.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="record_type" />
      </label>
      {!statement && (
        <label className="flex flex-col gap-1 text-sm">
          Return label
          <select
            className={inputClass}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          >
            <option value="">Choose a Return label…</option>
            {setup.sections.map((s) => {
              const inSection = available.filter((l) => l.section === s.key);
              return inSection.length === 0 ? null : (
                <optgroup key={s.key} label={s.name}>
                  {inSection.map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.return_item
                        ? `${l.name} (item ${l.return_item})`
                        : l.name}
                    </option>
                  ))}
                </optgroup>
              );
            })}
          </select>
          {chosen?.description && (
            <span className="text-xs text-muted-foreground">
              {chosen.description}
            </span>
          )}
          <FieldMessage error={save.error} field="label" />
        </label>
      )}
      {doubled.length > 0 && (
        <div
          role="note"
          data-testid="double-count"
          className="rounded-md border border-warning bg-warning/10 p-3 text-sm"
        >
          {statement
            ? `This Member already has a plain salary or tax withheld record in this year (${doubled.map((r) => r.description).join("; ")}). An income statement adds the same pay and tax again, so an estimate would count them twice. You can still save; delete one of them to avoid the double count.`
            : `This Member already has an income statement in this year (${doubled.map((r) => r.description).join("; ")}). It adds the same pay and tax, so an estimate would count them twice. You can still save; delete one of them to avoid the double count.`}
        </div>
      )}
      <label className="flex flex-col gap-1 text-sm">
        Description
        <input
          className={inputClass}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <FieldMessage error={save.error} field="description" />
      </label>
      {depreciating ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Start date
            <input
              className={inputClass}
              type="date"
              value={fromOn}
              onChange={(e) => setFromOn(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              When it was first used or installed ready for use.
            </span>
            <FieldMessage error={save.error} field="from_on" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Last day used
            <input
              className={inputClass}
              type="date"
              value={toOn}
              onChange={(e) => setToOn(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              Leave blank while you still use it.
            </span>
            <FieldMessage error={save.error} field="to_on" />
          </label>
        </div>
      ) : (
        <PeriodField
          from={fromOn}
          to={toOn}
          onChange={(from, to) => {
            setFromOn(from);
            setToOn(to);
          }}
          defaultYear={defaultYear}
          fromLabel={
            statement ? "Statement from" : homeOffice || carKm ? "From" : "Date"
          }
          toLabel={
            statement
              ? "Statement to"
              : homeOffice || carKm
                ? "To"
                : "To (if it spans days)"
          }
          error={save.error}
        />
      )}
      {statement ? (
        <div className="flex flex-col gap-4">
          {reader.panel}
          <p className="text-xs text-muted-foreground">
            Copy the statement from top to bottom, in dollars as printed. Three
            figures are required (enter 0 if there was none): Total gross
            amount, PAYGW amount and Employer superannuation contribution
            liability. The rest are optional and count as 0 when blank. Net pay
            received is not on the statement: leave it blank and the Income
            reconciliation works take-home out.
          </p>
          {STATEMENT_GROUPS.map((group) => (
            <fieldset key={group.heading} className="flex flex-col gap-3">
              <legend className="mb-1 text-sm font-medium">
                {group.heading}
              </legend>
              {group.fields.map(({ key, name, text }) => (
                <label key={key} className="flex flex-col gap-1 text-sm">
                  {text ? name : `${name} ($)`}
                  <input
                    className={
                      text ? inputClass : `${inputClass} text-right font-mono`
                    }
                    inputMode={text ? "text" : "decimal"}
                    maxLength={text ? 80 : undefined}
                    aria-required={REQUIRED_STATEMENT_FIGURES.includes(key)}
                    placeholder={
                      key === "net_pay"
                        ? "Not stated"
                        : text || REQUIRED_STATEMENT_FIGURES.includes(key)
                          ? undefined
                          : "0"
                    }
                    value={figures[key]}
                    onChange={(e) => {
                      setFigures({ ...figures, [key]: e.target.value });
                      reader.edited(key);
                    }}
                  />
                  {reader.mark(key)}
                  <FieldMessage error={save.error} field={key} />
                </label>
              ))}
            </fieldset>
          ))}
          <FieldMessage error={save.error} field="body" />
        </div>
      ) : depreciating ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            Cost ($)
            <input
              className={`${inputClass} text-right font-mono`}
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
            <FieldMessage error={save.error} field="amount" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Effective life (years)
            <input
              className={`${inputClass} text-right font-mono`}
              inputMode="decimal"
              placeholder="4"
              value={life}
              onChange={(e) => setLife(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              Your own entry, such as 4 or 2.5. Take it from the ATO's effective
              life tables or your own estimate.
            </span>
            <FieldMessage error={save.error} field="effective_life_years" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Work-use share (%)
            <input
              className={`${inputClass} text-right font-mono`}
              inputMode="decimal"
              placeholder="100"
              value={workUse}
              onChange={(e) => setWorkUse(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              Leave blank when it is used only for this. Only this share of each
              year's decline in value counts.
            </span>
            <FieldMessage error={save.error} field="work_use_percent" />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={claimInFull}
              onChange={(e) => setClaimInFull(e.target.checked)}
            />
            <span>
              Claim in full in the first year
              <span className="block text-xs text-muted-foreground">
                Deducts the work-use share of the cost in that year and nothing
                after. Finance360 does not check whether the asset is eligible:
                that depends on what it cost and how it is used. Check before
                you choose this.
              </span>
            </span>
          </label>
          <FieldMessage error={save.error} field="claim_in_full" />
        </>
      ) : homeOffice ? (
        home.fields(save.error, {
          rate: initial?.home_office?.rate_per_hour ?? null,
          year: initial ? financialYearOfDate(initial.from_on) : null,
        })
      ) : carKm ? (
        carTrips.fields(
          save.error,
          { fromOn, toOn },
          {
            limit: initial?.car?.limit_km ?? null,
            year: initial ? financialYearOfDate(initial.from_on) : null,
          },
        )
      ) : type === "line" ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            Amount ($)
            <input
              className={`${inputClass} text-right font-mono`}
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
            <FieldMessage error={save.error} field="amount" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Work-use share (%)
            <input
              className={`${inputClass} text-right font-mono`}
              inputMode="decimal"
              placeholder="100"
              value={workUse}
              onChange={(e) => setWorkUse(e.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              Leave blank when the whole amount counts. Only this share of the
              amount counts toward the total.
            </span>
            <FieldMessage error={save.error} field="work_use_percent" />
          </label>
        </>
      ) : chosen?.kind === "days" ? (
        <label className="flex flex-col gap-1 text-sm">
          Days
          <input
            className={`${inputClass} text-right font-mono`}
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <FieldMessage error={save.error} field="value" />
        </label>
      ) : (
        <fieldset className="flex flex-col gap-2 text-sm">
          <legend className="mb-1 font-medium">Applies?</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="fact-flag"
              checked={flag}
              onChange={() => setFlag(true)}
            />
            Yes
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="fact-flag"
              checked={!flag}
              onChange={() => setFlag(false)}
            />
            No
          </label>
          <FieldMessage error={save.error} field="value" />
        </fieldset>
      )}
      <label className="flex flex-col gap-1 text-sm">
        Notes
        <textarea
          className={`${inputClass} py-2`}
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <FieldMessage error={save.error} field="notes" />
      </label>
      <GeneralError error={save.error} />
      <FieldMessage error={save.error} field="body" />
      {savedId !== null && attach.isError ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm"
        >
          <p>
            The Tax record was saved, but the PDF could not be kept as a
            document: {attach.error.message} You can try again, or close and
            add it from the record's Documents.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onSaved}>
              Close
            </Button>
            <Button
              type="button"
              disabled={attach.isPending}
              onClick={() => attach.mutate(savedId)}
            >
              Try again
            </Button>
          </div>
        </div>
      ) : (
        <>
          {reader.blocksSave && (
            <p className="text-right text-xs text-muted-foreground">
              Tick that you have checked the figures to save.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <SheetClose asChild>
              <Button variant="ghost">Cancel</Button>
            </SheetClose>
            <Button
              type="submit"
              disabled={save.isPending || attach.isPending || reader.blocksSave}
            >
              {initial ? "Save" : "Create Tax record"}
            </Button>
          </div>
        </>
      )}
    </form>
  );
  // With a PDF read into the statement, it sits beside the form (tabs on a phone): ADR-0026 section 5.
  return (
    <PrefillLayout
      form={form}
      document={statement ? reader.documentPane : null}
      jump={reader.jump}
    />
  );
}

/**
 * Create (no `record`) or edit a Tax record in a Sheet opened from `trigger`. The form mounts only
 * while open, so each opening starts from the record's current values; a failed save keeps the
 * Sheet open with the message beside the field.
 */
export function TaxRecordSheet({
  record,
  trigger,
  defaultMember,
  newType,
  defaultYear,
  onSaved,
}: {
  record?: TaxRecord;
  trigger: ReactNode;
  defaultMember?: number;
  /** For a new record: the Record type to start on (the Return label list is then hidden for a statement). */
  newType?: string;
  /** For a new record: the financial year (the year it ends in) its period starts on; else the current one. */
  defaultYear?: number;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const household = useQuery({
    queryKey: ["household"],
    queryFn: fetchHousehold,
  });
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const members = household.data?.members ?? [];
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent expandable className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">
          {record
            ? `Edit ${record.description}`
            : newType === "income_statement"
              ? "New income statement"
              : "New Tax record"}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          A dated amount, fact, income statement, depreciating asset, home
          office worksheet or list of car trips for one Member. The financial
          year comes from the dates.
        </SheetDescription>
        {household.data && setup.data ? (
          members.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Add a Member in Household first.
            </p>
          ) : (
            <RecordForm
              initial={record}
              members={members}
              setup={setup.data}
              defaultMember={defaultMember}
              newType={newType}
              defaultYear={defaultYear}
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus, X } from "lucide-react";
import { useState } from "react";

import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { LinkedDocuments } from "@/components/linked-documents";
import { TIGHT } from "@/components/tax-estimate-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchHousehold } from "@/lib/household";
import { currentFinancialYear, financialYearLabel, RESULT_LINES } from "@/lib/tax";
import {
  amendLodgedResult,
  enterLodgedResult,
  fetchWorksheet,
  LODGED_KIND,
  STATUS_TEXT,
  type LodgedResult,
  type ReturnWorksheet,
} from "@/lib/tax-lodged";

const KEY = ["tax-worksheet"];
const labelOf = (key: string) => RESULT_LINES.find((l) => l.key === key)?.label ?? key;

// --- entering a notice ---------------------------------------------------------------------

type OtherRow = { name: string; amount: string };

function NoticeForm({
  memberId,
  financialYear,
  current,
  onDone,
}: {
  memberId: number;
  financialYear: number;
  current: LodgedResult | null;
  onDone: () => void;
}) {
  const dirty = useDirtyFormProps();
  const queryClient = useQueryClient();
  const [assessedOn, setAssessedOn] = useState("");
  const [items, setItems] = useState<Record<string, string>>(() =>
    Object.fromEntries((current?.items ?? []).map((i) => [i.key, i.amount])),
  );
  const [other, setOther] = useState<OtherRow[]>(() => (current?.other ?? []).map((o) => ({ ...o })));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        member_id: memberId,
        financial_year: financialYear,
        assessed_on: assessedOn || null,
        items,
        other,
      };
      return current ? amendLodgedResult(current.id, body) : enterLodgedResult(body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: KEY });
      onDone();
    },
  });
  const setOtherAt = (at: number, patch: Partial<OtherRow>) =>
    setOther((rows) => rows.map((row, i) => (i === at ? { ...row, ...patch } : row)));

  return (
    <form
      {...dirty}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Date on the notice
        <input
          type="date"
          className={inputClass}
          value={assessedOn}
          onChange={(e) => setAssessedOn(e.target.value)}
        />
        <FieldMessage error={save.error} field="assessed_on" />
      </label>
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Amounts on the notice</legend>
        {RESULT_LINES.map((line) => (
          <label key={line.key} className="grid grid-cols-[minmax(0,1fr)_8rem] items-center gap-2 text-sm">
            <span>
              {line.label}
              {line.key === "net_outcome" && (
                <span className="block text-xs text-muted-foreground">
                  A refund is positive, an amount payable negative.
                </span>
              )}
            </span>
            <input
              className={`${inputClass} text-right`}
              inputMode="decimal"
              value={items[line.key] ?? ""}
              onChange={(e) => setItems((now) => ({ ...now, [line.key]: e.target.value }))}
              aria-label={line.label}
            />
            <span className="col-span-2">
              <FieldMessage error={save.error} field={`items.${line.key}`} />
            </span>
          </label>
        ))}
        <FieldMessage error={save.error} field="items" />
      </fieldset>
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">Other lines</legend>
        <p className="text-xs text-muted-foreground">
          Anything else the notice lists, named as it names it. The engine has no figure for these, so the
          worksheet reports each one as unmatched.
        </p>
        {other.map((row, i) => (
          <div key={i} className="grid grid-cols-[minmax(0,1fr)_6.5rem_auto] items-start gap-2">
            <div className="flex flex-col gap-1">
              <input
                className={`${inputClass} w-full min-w-0`}
                value={row.name}
                maxLength={100}
                placeholder="Name"
                aria-label={`Other line ${i + 1} name`}
                onChange={(e) => setOtherAt(i, { name: e.target.value })}
              />
              <FieldMessage error={save.error} field={`other.${i}.name`} />
            </div>
            <div className="flex flex-col gap-1">
              <input
                className={`${inputClass} w-full min-w-0 text-right`}
                inputMode="decimal"
                value={row.amount}
                placeholder="0.00"
                aria-label={`Other line ${i + 1} amount`}
                onChange={(e) => setOtherAt(i, { amount: e.target.value })}
              />
              <FieldMessage error={save.error} field={`other.${i}.amount`} />
            </div>
            <Button
              type="button"
              variant="ghost"
              aria-label={`Remove other line ${i + 1}`}
              onClick={() => setOther((rows) => rows.filter((_, at) => at !== i))}
            >
              <X size={16} aria-hidden />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          onClick={() => setOther((rows) => [...rows, { name: "", amount: "" }])}
        >
          <Plus size={16} aria-hidden />
          Add a line
        </Button>
        <FieldMessage error={save.error} field="other" />
      </fieldset>
      <FieldMessage error={save.error} field="financial_year" />
      <FieldMessage error={save.error} field="member_id" />
      <GeneralError error={save.error} />
      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="ghost">
            Cancel
          </Button>
        </DialogClose>
        <Button type="submit" disabled={save.isPending}>
          {current ? "Save amended notice" : "Save notice"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function NoticeDialog({
  memberId,
  financialYear,
  current,
}: {
  memberId: number;
  financialYear: number;
  current: LodgedResult | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>{current ? "Enter an amended notice" : "Enter Lodged result"}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{current ? "Enter an amended notice" : "Enter Lodged result"}</DialogTitle>
          <DialogDescription>
            {current
              ? "The amounts below are the current notice's. Change what the amended notice changes. The earlier notice is kept and stays in the history."
              : "The amounts from the notice of assessment for FY " +
                financialYearLabel(financialYear) +
                ". Leave blank anything the notice leaves out."}
          </DialogDescription>
        </DialogHeader>
        <NoticeForm
          memberId={memberId}
          financialYear={financialYear}
          current={current}
          onDone={() => setOpen(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

// --- the worksheet -------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const text = STATUS_TEXT[status];
  if (!text) return null;
  return (
    <Badge variant={status === "differs" ? "flagged" : "ok"} >
      {text}
    </Badge>
  );
}

function Comparison({ sheet }: { sheet: ReturnWorksheet }) {
  const noticed = sheet.current !== null;
  return (
    <Table aria-label="Estimate beside Lodged result">
      <TableHeader>
        <TableRow>
          <TableHead>
            <span className="sr-only">Item</span>
          </TableHead>
          <TableHead className="px-1.5 text-right sm:px-[var(--cell-px)]">Estimate</TableHead>
          <TableHead className="px-1.5 text-right sm:px-[var(--cell-px)]">Lodged</TableHead>
          <TableHead className="px-1.5 text-right sm:px-[var(--cell-px)]">Difference</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sheet.lines.map((line) => (
          <TableRow key={line.key} className={line.key === "net_outcome" ? "font-semibold" : undefined}>
            <TableCell className={`${TIGHT} w-20 sm:w-auto`}>{labelOf(line.key)}</TableCell>
            <TableCell className={`${TIGHT} text-right`}>
              <Money amount={line.engine} />
            </TableCell>
            <TableCell className={`${TIGHT} text-right`}>
              {line.lodged === null ? <span className="text-muted-foreground">Not entered</span> : <Money amount={line.lodged} />}
            </TableCell>
            <TableCell className={`${TIGHT} text-right`}>
              {line.difference !== null && (
                <div className="flex flex-col items-end gap-1">
                  <Money amount={line.difference} />
                  <StatusBadge status={line.status} />
                </div>
              )}
              {line.difference === null && noticed && line.status === "not_lodged" && (
                <span className="text-muted-foreground">-</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function History({ history }: { history: LodgedResult[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Notices entered</CardTitle>
        <p className="text-sm text-muted-foreground">
          An amended notice replaces the one before it, which is kept here exactly as entered.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {history.map((notice) => (
          <details key={notice.id} className="rounded-lg border border-border p-3" data-testid="notice">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
              <span>Assessed {notice.assessed_on}</span>
              {notice.current ? <Badge variant="ok">Current</Badge> : <Badge variant="superseded">Superseded</Badge>}
            </summary>
            <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
              {notice.items.map((i) => (
                <div key={i.key} className="col-span-2 grid grid-cols-subgrid">
                  <dt>{labelOf(i.key)}</dt>
                  <dd className="text-right">
                    <Money amount={i.amount} />
                  </dd>
                </div>
              ))}
              {notice.other.map((o) => (
                <div key={o.name} className="col-span-2 grid grid-cols-subgrid">
                  <dt>{o.name}</dt>
                  <dd className="text-right">
                    <Money amount={o.amount} />
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        ))}
      </CardContent>
    </Card>
  );
}

function Worksheet({ sheet }: { sheet: ReturnWorksheet }) {
  const lodgedCount = sheet.lines.filter((l) => l.lodged !== null).length;
  return (
    <>
      <Card data-testid="worksheet">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>
              {sheet.member_name}, FY {sheet.financial_year_label}
            </CardTitle>
            {sheet.incomplete && <Badge variant="flagged">Estimate incomplete</Badge>}
          </div>
          {sheet.current && (
            <p className="text-sm text-muted-foreground" data-testid="summary">
              Notice assessed {sheet.current.assessed_on}: {sheet.matched_count} of {lodgedCount} items matched,{" "}
              {sheet.differs_count} {sheet.differs_count === 1 ? "differs" : "differ"}, {sheet.unmatched.length}{" "}
              {sheet.unmatched.length === 1 ? "line" : "lines"} the engine cannot match.
            </p>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {sheet.problems.length > 0 && (
            <div role="status" data-testid="no-estimate" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
              <p className="font-medium">The estimate could not be calculated, so there is nothing to compare yet.</p>
              {sheet.problems.map((p) => (
                <p key={p}>{p}</p>
              ))}
              <p>
                <Link
                  to="/rule-figures"
                  search={{ fy: sheet.financial_year }}
                  className="text-primary underline underline-offset-4"
                >
                  Open Rule figures for FY {sheet.financial_year_label}
                </Link>
              </p>
            </div>
          )}
          {!sheet.current && sheet.problems.length === 0 && (
            <p className="text-sm text-muted-foreground" data-testid="no-notice">
              No Lodged result is entered for this Member and year. The estimate is shown on its own; enter the
              notice of assessment to compare it.
            </p>
          )}
          <Comparison sheet={sheet} />
          <p className="text-sm text-muted-foreground">
            Difference is the estimate less the notice. Net outcome is matched within 5 cents, because the notice
            rounds its final result; every other item is compared to the cent.
            {sheet.rule_pack_version && ` Rule pack ${sheet.rule_pack_version}, engine v${sheet.engine_version}.`}
          </p>
        </CardContent>
      </Card>
      {sheet.unmatched.length > 0 && (
        <Card data-testid="unmatched">
          <CardHeader>
            <CardTitle>Lines the engine cannot match</CardTitle>
            <p className="text-sm text-muted-foreground">
              The notice lists these and the engine has no figure for them, so they are reported, not compared.
            </p>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-1 text-sm">
              {sheet.unmatched.map((u) => (
                <li key={u.name} className="flex justify-between gap-4">
                  <span>{u.name}</span>
                  <Money amount={u.amount} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      {sheet.history.length > 0 && <History history={sheet.history} />}
      {sheet.current && <LinkedDocuments kind={LODGED_KIND} id={sheet.current.id} />}
    </>
  );
}

/** `/tax/lodged?member=1&fy=2026`: the engine's estimate beside what the notice of assessment says. */
export function TaxLodgedPage({
  member,
  fy,
  onSearch,
}: {
  member: number | undefined;
  fy: number | undefined;
  onSearch: (patch: { member?: number; fy?: number }) => void;
}) {
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const [dependants, setDependants] = useState("0");
  const members = household.data?.members ?? [];
  const memberId = member ?? members[0]?.id;
  const year = fy ?? currentFinancialYear() - 1;
  const children = Number.parseInt(dependants, 10);
  const sheet = useQuery({
    queryKey: [...KEY, memberId, year, children],
    queryFn: () => fetchWorksheet(memberId as number, year, Number.isNaN(children) ? 0 : children),
    enabled: memberId !== undefined,
    retry: false,
  });

  if (household.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (household.error) return <p className="text-destructive">Could not load the Household: {household.error.message}</p>;
  if (members.length === 0) {
    return (
      <div>
        <PageHeader title="Return worksheet" />
        <p className="text-sm text-muted-foreground">Add a Member to the Household first.</p>
      </div>
    );
  }

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <PageHeader
        title="Return worksheet"
        description="The tax estimate beside the Lodged result from a notice of assessment: each difference, and every line the engine cannot match."
        action={
          memberId !== undefined && (
            <NoticeDialog memberId={memberId} financialYear={year} current={sheet.data?.current ?? null} />
          )
        }
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-sm">
          Member
          <select
            className={inputClass}
            value={memberId}
            onChange={(e) => onSearch({ member: Number(e.target.value), fy: year })}
          >
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Financial year
          <FinancialYearSelect
            value={year}
            onChange={(next) => onSearch({ member: memberId, fy: next })}
            years={financialYears([year])}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Dependent children
          <input
            className={inputClass}
            inputMode="numeric"
            value={dependants}
            onChange={(e) => setDependants(e.target.value)}
          />
          <span className="text-xs text-muted-foreground">For the estimate, as on the Tax estimate screen.</span>
        </label>
      </div>
      {sheet.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      <GeneralError error={sheet.error} />
      {sheet.data && <Worksheet sheet={sheet.data} />}
    </div>
  );
}

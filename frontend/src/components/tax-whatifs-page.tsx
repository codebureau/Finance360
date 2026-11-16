import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Calculator, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { SaveEstimateDialog } from "@/components/save-estimate-dialog";
import { TaxEstimateBody } from "@/components/tax-estimate-body";
import { AllErrors, OverrideDialog } from "@/components/tax-override-dialog";
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
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchHousehold, type Member } from "@/lib/household";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";
import {
  createWhatIf,
  deleteWhatIf,
  fetchWhatIf,
  fetchWhatIfs,
  overrideInput,
  previewEstimate,
  updateWhatIf,
  whatIfInput,
  type TaxOverride,
  type TaxOverrideInput,
  type TaxWhatIf,
} from "@/lib/tax-estimates";

const MAX_MEMBERS = 2;
const WHATIFS_KEY = ["tax-whatifs"];

function BackLink({ children = "Tax what-ifs" }: { children?: string }) {
  return (
    <Link to="/tax/whatifs" className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}>
      <ArrowLeft size={16} aria-hidden />
      {children}
    </Link>
  );
}

/** One override in words. */
function overrideText(o: TaxOverride, names: Record<number, string>): string {
  const who = o.member_id ? (names[o.member_id] ?? `Member ${o.member_id}`).split(" ")[0] : "";
  if (o.op === "set") return `${who}: ${o.label_name} is stated as a total`;
  if (o.op === "exclude_label") return `${who}: ${o.label_name} is left out`;
  if (o.op === "exclude_record") return `Record ${o.record_id} is left out`;
  const r = o.record;
  return `Hypothetical record: ${r?.description ?? ""} (${(names[r?.member_id ?? 0] ?? "").split(" ")[0]})`;
}

// --- the list ----------------------------------------------------------------------------

function NewWhatIfDialog({ year, onCreated }: { year: number; onCreated: (w: TaxWhatIf) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [fy, setFy] = useState<number | undefined>(year);
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      createWhatIf({ name, financial_year: fy ?? year, notes: notes.trim() || null, overrides: [] }),
    onSuccess: async (made) => {
      await queryClient.invalidateQueries({ queryKey: WHATIFS_KEY });
      setOpen(false);
      setName("");
      setNotes("");
      onCreated(made);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          create.reset();
          setFy(year);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button>New Tax what-if</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Tax what-if</DialogTitle>
          <DialogDescription>
            A way to try a change on one year without touching your Tax records. You add the overrides on the next
            screen.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Name
            <input className={inputClass} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            <FieldMessage error={create.error} field="name" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Financial year
            <FinancialYearSelect value={fy} onChange={setFy} years={financialYears([year])} />
            <FieldMessage error={create.error} field="financial_year" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Notes (optional)
            <input className={inputClass} value={notes} maxLength={500} onChange={(e) => setNotes(e.target.value)} />
            <FieldMessage error={create.error} field="notes" />
          </label>
          <GeneralError error={create.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              Create Tax what-if
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** `/tax/whatifs`: every Tax what-if of a year, each opening its editor. */
export function TaxWhatIfsPage({ fy, onYear }: { fy: number | undefined; onYear: (year: number | undefined) => void }) {
  const whatifs = useQuery({ queryKey: [...WHATIFS_KEY, fy ?? "all"], queryFn: () => fetchWhatIfs(fy) });
  const navigate = useNavigate();
  const year = fy ?? currentFinancialYear();
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <PageHeader
        title="Tax what-ifs"
        description="Try a change on one financial year without touching your Tax records: state a total, leave something out, or add a hypothetical record. Each is calculated and compared on its own."
        action={
          <NewWhatIfDialog
            year={year}
            onCreated={(made) =>
              void navigate({ to: "/tax/whatifs/$whatifId", params: { whatifId: String(made.id) } })
            }
          />
        }
      />
      <label className="flex w-full max-w-xs flex-col gap-1 text-sm">
        Financial year
        <FinancialYearSelect value={fy} onChange={onYear} years={financialYears([fy])} allLabel="All years" />
      </label>
      <GeneralError error={whatifs.error} />
      {whatifs.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {whatifs.data && whatifs.data.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="no-whatifs">
          No Tax what-ifs yet{fy ? ` for FY ${financialYearLabel(fy)}` : ""}. Choose New Tax what-if to try one.
        </p>
      )}
      {whatifs.data && whatifs.data.length > 0 && (
        <Table aria-label="Tax what-ifs">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Year</TableHead>
              <TableHead className="text-right">Overrides</TableHead>
              <TableHead className="text-right">Saved estimates</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {whatifs.data.map((w) => (
              <TableRow key={w.id}>
                <TableCell>
                  <Link
                    to="/tax/whatifs/$whatifId"
                    params={{ whatifId: String(w.id) }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {w.name}
                  </Link>
                  {w.origin === "ai" && (
                    <Badge variant="info" className="ml-2">
                      From a question
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap">FY {w.financial_year_label}</TableCell>
                <TableCell className="text-right">{w.overrides.length}</TableCell>
                <TableCell className="text-right">{w.estimate_count}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

// --- the editor --------------------------------------------------------------------------

function EditDetailsDialog({ whatif }: { whatif: TaxWhatIf }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(whatif.name);
  const [notes, setNotes] = useState(whatif.notes ?? "");
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      updateWhatIf(whatif.id, {
        ...whatIfInput(whatif, whatif.overrides.map(overrideInput)),
        name,
        notes: notes.trim() || null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: WHATIFS_KEY });
      setOpen(false);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          save.reset();
          setName(whatif.name);
          setNotes(whatif.notes ?? "");
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">Edit details</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit details</DialogTitle>
          <DialogDescription>
            Estimates already saved from this Tax what-if keep the name and overrides they were saved with.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Name
            <input className={inputClass} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            <FieldMessage error={save.error} field="name" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Notes (optional)
            <input className={inputClass} value={notes} maxLength={500} onChange={(e) => setNotes(e.target.value)} />
            <FieldMessage error={save.error} field="notes" />
          </label>
          <GeneralError error={save.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              Save details
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  whatif,
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  whatif: TaxWhatIf;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => deleteWhatIf(whatif.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: WHATIFS_KEY });
      onOpenChange(false);
      void navigate({ to: "/tax/whatifs" });
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) remove.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {whatif.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            The what-if and its overrides are removed. Your Tax records are not touched. A Tax what-if with saved
            estimates cannot be deleted. The deletion is recorded in the Audit trail.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AllErrors error={remove.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so a refusal shows here
              remove.mutate();
            }}
          >
            Delete Tax what-if
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function Editor({ whatif, members }: { whatif: TaxWhatIf; members: Member[] }) {
  const queryClient = useQueryClient();
  const [dependants, setDependants] = useState("0");
  const [deleting, setDeleting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const names = Object.fromEntries(members.map((m) => [m.id, m.name]));
  const request = {
    financialYear: whatif.financial_year,
    dependants,
    memberIds: members.slice(0, MAX_MEMBERS).map((m) => m.id),
    whatifId: whatif.id,
  };

  const calculate = useMutation({ mutationFn: () => previewEstimate(request) });
  const replace = useMutation({
    mutationFn: (overrides: TaxOverrideInput[]) => updateWhatIf(whatif.id, whatIfInput(whatif, overrides)),
    onSuccess: async () => {
      calculate.reset();
      await queryClient.invalidateQueries({ queryKey: WHATIFS_KEY });
    },
  });

  const current = whatif.overrides.map(overrideInput);

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <BackLink />
      <PageHeader
        title={whatif.name}
        description={`FY ${whatif.financial_year_label}${whatif.notes ? `. ${whatif.notes}` : ""}`}
        action={
          <div className="flex items-center gap-2">
            <EditDetailsDialog key={whatif.id} whatif={whatif} />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button ref={menuRef} variant="outline" aria-label="More actions" className="px-2">
                  <MoreHorizontal size={16} aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
                  Delete Tax what-if
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        }
      />

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2">
          <CardTitle>Overrides</CardTitle>
          <OverrideDialog
            financialYear={whatif.financial_year}
            members={members.slice(0, MAX_MEMBERS)}
            error={replace.error}
            onAdd={async (added) => {
              replace.reset();
              await replace.mutateAsync([...current, added]);
            }}
          />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Each override is absolute: it replaces what your records give, it is not a change on top. This what-if
            never builds on another.
          </p>
          {whatif.overrides.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="no-overrides">
              No overrides yet, so this calculates the same as your records.
            </p>
          ) : (
            <ul className="divide-y divide-border text-sm" aria-label="Overrides">
              {whatif.overrides.map((o, i) => (
                <li key={i} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span>
                    {overrideText(o, names)}
                    {o.op === "set" && o.value && (
                      <>
                        : <Money amount={o.value.amount} />
                        {o.value.days !== null && `${o.value.days} days`}
                        {o.value.flag !== null && (o.value.flag ? "yes" : "no")}
                      </>
                    )}
                  </span>
                  <Button
                    variant="ghost"
                    aria-label={`Remove override ${i + 1}`}
                    disabled={replace.isPending}
                    onClick={() => replace.mutate(current.filter((_, at) => at !== i))}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Calculate</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Dependent children
            <input
              className={inputClass}
              inputMode="numeric"
              value={dependants}
              onChange={(e) => setDependants(e.target.value)}
            />
          </label>
          <Button disabled={calculate.isPending} onClick={() => calculate.mutate()}>
            <Calculator size={16} aria-hidden />
            {calculate.isPending ? "Calculating…" : "Calculate"}
          </Button>
          {calculate.data && (
            <SaveEstimateDialog
              request={request}
              variant="outline"
              onSaved={(saved) =>
                void navigate({ to: "/tax/estimates/$estimateId", params: { estimateId: String(saved.id) } })
              }
            />
          )}
        </CardContent>
      </Card>
      <AllErrors error={calculate.error} />
      {calculate.data && <TaxEstimateBody view={calculate.data} />}
      <DeleteDialog
        key={whatif.id}
        whatif={whatif}
        open={deleting}
        onOpenChange={setDeleting}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          menuRef.current?.focus();
        }}
      />
    </div>
  );
}

/** `/tax/whatifs/$whatifId`: edit the overrides, calculate, and save the estimate. */
export function TaxWhatIfPage({ whatifId }: { whatifId: string }) {
  const id = Number(whatifId);
  const whatif = useQuery({
    queryKey: [...WHATIFS_KEY, "detail", id],
    queryFn: () => fetchWhatIf(id),
    enabled: Number.isInteger(id),
    retry: false,
  });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  if (whatif.isPending || household.isPending) return <p className="text-muted-foreground">Loading…</p>;
  if (whatif.error || !whatif.data || !household.data) {
    return (
      <div role="alert" className="flex flex-col gap-3">
        <BackLink />
        <p className="text-destructive">
          Could not load this Tax what-if: {whatif.error?.message ?? household.error?.message ?? "not found"}
        </p>
      </div>
    );
  }
  return <Editor whatif={whatif.data} members={household.data.members} />;
}

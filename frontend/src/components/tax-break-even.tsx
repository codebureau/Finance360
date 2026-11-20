import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Search, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { AllErrors } from "@/components/tax-override-dialog";
import { Button } from "@/components/ui/button";
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
import { Money } from "@/components/ui/money";
import type { Member } from "@/lib/household";
import { fetchTaxSetup } from "@/lib/tax";
import {
  createWhatIf,
  overrideInput,
  searchBreakEven,
  type EstimateRequest,
  type TaxBreakEven as BreakEvenResult,
  type TaxOverrideInput,
  type TaxWhatIf,
} from "@/lib/tax-estimates";

const REASONS: Record<string, string> = {
  cliff:
    "The outcome jumps at this amount, as it does at a Medicare levy surcharge tier or a HELP repayment threshold, so a small change here can matter a lot.",
  reversal: "Across the range the outcome both rises and falls as the amount grows.",
  many_crossings:
    "The target is met and missed more than once across the range, so this is the lowest crossing, not the only one.",
};

/** The what-if's overrides, with this Member and label's own override replaced by the found amount. */
function overridesWith(whatif: TaxWhatIf, memberId: number, label: string, amount: string): TaxOverrideInput[] {
  const others = whatif.overrides
    .filter(
      (o) =>
        !(
          (o.op === "set" || o.op === "exclude_label") &&
          o.member_id === memberId &&
          o.label_key === label
        ),
    )
    .map(overrideInput);
  return [...others, { op: "set", member_id: memberId, label, amount }];
}

function SaveAsWhatIf({
  whatif,
  memberId,
  label,
  labelName,
  amount,
}: {
  whatif: TaxWhatIf;
  memberId: number;
  label: string;
  labelName: string;
  amount: string;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      createWhatIf({
        name,
        financial_year: whatif.financial_year,
        notes: `Break-even of ${labelName}, from ${whatif.name}.`,
        overrides: overridesWith(whatif, memberId, label, amount),
      }),
    onSuccess: async (made) => {
      await queryClient.invalidateQueries({ queryKey: ["tax-whatifs"] });
      setOpen(false);
      void navigate({ to: "/tax/whatifs/$whatifId", params: { whatifId: String(made.id) } });
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) save.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">Save as what-if</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as what-if</DialogTitle>
          <DialogDescription>
            Creates an ordinary Tax what-if with this what-if&apos;s other overrides and {labelName} stated as the
            amount found. Nothing is kept until you save.
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
          <GeneralError error={save.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              Create Tax what-if
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Outcome({
  result,
  whatif,
  memberId,
  label,
  labelName,
  who,
}: {
  result: BreakEvenResult;
  whatif: TaxWhatIf;
  memberId: number;
  label: string;
  labelName: string;
  who: string;
}) {
  if (result.status !== "found" || result.amount === null) {
    return (
      <p className="text-sm" data-testid="break-even-none">
        {result.status === "always"
          ? `The base case is met at every amount of ${who}'s ${labelName} from `
          : `No amount of ${who}'s ${labelName} from `}
        <Money amount={result.lower} /> to <Money amount={result.upper} />
        {result.status === "always" ? ", so there is no crossing to find." : " meets the base case: no crossing in range."}
      </p>
    );
  }
  const rows = [
    { name: "One cent below", probe: result.below },
    { name: "The amount", probe: result.at },
    { name: "One cent above", probe: result.above },
  ];
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm" data-testid="break-even-found">
        {result.direction === "largest" ? "Up to " : "From "}
        <Money amount={result.amount} className="font-semibold" /> of {who}&apos;s {labelName}, the household&apos;s
        net outcome is at least the base case (<Money amount={result.target} />).
      </p>
      <div className="overflow-x-auto">
        <table className="w-full max-w-lg text-sm" aria-label="Verification">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-1 pr-3 font-normal">Check</th>
              <th className="py-1 pr-3 text-right font-normal">{labelName}</th>
              <th className="py-1 pr-3 text-right font-normal">Net outcome</th>
              <th className="py-1 font-normal">Base case met</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(
              (r) =>
                r.probe && (
                  <tr key={r.name} className="border-t border-border">
                    <td className="py-1 pr-3">{r.name}</td>
                    <td className="py-1 pr-3 text-right">
                      <Money amount={r.probe.amount} />
                    </td>
                    <td className="py-1 pr-3 text-right">
                      <Money amount={r.probe.net_outcome} />
                    </td>
                    <td className="py-1">{r.probe.meets_target ? "Yes" : "No"}</td>
                  </tr>
                ),
            )}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-muted-foreground" data-testid="break-even-verified">
        {result.verified
          ? "Checked again by a full calculation: the base case is met at this amount and not at the next cent."
          : "This amount could not be verified, so do not rely on it."}
      </p>
      {result.non_monotone && (
        <div
          role="alert"
          data-testid="break-even-warning"
          className="flex gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
        >
          <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" />
          <div className="flex flex-col gap-1">
            <p className="font-medium">The outcome is not steady across this range</p>
            {result.reasons.map((r) => (
              <p key={r}>{REASONS[r] ?? r}</p>
            ))}
            <p>This is one crossing, not necessarily the only one: check the amounts either side before you rely on it.</p>
          </div>
        </div>
      )}
      <div>
        <SaveAsWhatIf whatif={whatif} memberId={memberId} label={label} labelName={labelName} amount={result.amount} />
      </div>
    </div>
  );
}

/**
 * Break-even for one amount: pick a Member and a Return label, and find the amount at which the
 * household net outcome is at least the base case, with this what-if's other overrides in force.
 * Nothing is stored; the amount can be saved as an ordinary Tax what-if.
 */
export function TaxBreakEven({
  whatif,
  members,
  request,
}: {
  whatif: TaxWhatIf;
  members: Member[];
  request: EstimateRequest;
}) {
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const [memberId, setMemberId] = useState<number>(members[0]?.id ?? 0);
  const [label, setLabel] = useState("");
  const labels = (setup.data?.labels ?? []).filter((l) => l.kind === "money" && l.direction !== "memo");
  const chosen = labels.find((l) => l.key === label);
  const who = (members.find((m) => m.id === memberId)?.name ?? "").split(" ")[0];
  const search = useMutation({ mutationFn: () => searchBreakEven(request, memberId, label) });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Break-even</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          At what amount of one total is the household&apos;s net outcome at least what your Tax records give? This Tax
          what-if&apos;s other overrides stay in force. Nothing is saved unless you save the amount as a what-if.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-w-0 max-w-full flex-col gap-1 text-sm">
            Member
            <select
              className={`${inputClass} max-w-full`}
              value={memberId}
              onChange={(e) => {
                setMemberId(Number(e.target.value));
                search.reset();
              }}
            >
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-w-0 max-w-full flex-col gap-1 text-sm">
            Return label
            <select
              className={`${inputClass} max-w-full`}
              value={label}
              onChange={(e) => {
                setLabel(e.target.value);
                search.reset();
              }}
            >
              <option value="">Choose a label</option>
              {labels.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" disabled={!label || search.isPending} onClick={() => search.mutate()}>
            <Search size={16} aria-hidden />
            {search.isPending ? "Searching…" : "Find break-even"}
          </Button>
        </div>
        <AllErrors error={search.error} />
        {search.data && chosen && (
          <Outcome
            result={search.data}
            whatif={whatif}
            memberId={memberId}
            label={label}
            labelName={chosen.name}
            who={who}
          />
        )}
      </CardContent>
    </Card>
  );
}

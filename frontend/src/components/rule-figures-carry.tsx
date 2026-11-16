import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { GeneralError } from "@/components/form-fields";
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
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  carryRuleFigures,
  confirmCarriedFigure,
  confirmCarriedPage,
  fetchCarryPreview,
  figureText,
  groupFigures,
  isCarried,
  type RuleFigure,
  type RuleFigureYear,
} from "@/lib/rule-figures";
import { financialYearLabel } from "@/lib/tax";

/** A known change the maintainer has written down for a figure; guidance only. */
export function ScheduledChange({ text }: { text: string }) {
  return (
    <p
      data-testid="scheduled-change"
      className="mt-1 rounded-md border border-warning/40 bg-warning/10 p-2 text-xs"
    >
      <span className="font-medium">Known change:</span> {text}
    </p>
  );
}

/**
 * "Start from <FY>" (ADR-0022 section 4): a preview of last year's figures for every missing
 * figure, and nothing is written until it is accepted. Each accepted figure is a provisional
 * capture, so the estimate warns until it is confirmed.
 */
export function CarryOffer({
  year,
  offer,
  onDone,
}: {
  year: number;
  offer: NonNullable<RuleFigureYear["carry_offer"]>;
  onDone: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const preview = useQuery({
    queryKey: ["rule-figures-carry", year],
    queryFn: () => fetchCarryPreview(year),
    enabled: open,
    staleTime: 0,
  });
  const carry = useMutation({
    mutationFn: () => carryRuleFigures(year),
    onSuccess: (written) => {
      setOpen(false);
      onDone(
        `Started FY ${financialYearLabel(year)} from earlier years: ${written.proposals.length} figure${written.proposals.length === 1 ? "" : "s"} carried, each provisional until you confirm it.`,
      );
    },
  });
  const data = preview.data;
  return (
    <section
      aria-label="Start from last year"
      className="flex flex-col gap-2 rounded-md border bg-muted/40 p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <p>
        {offer.count} figure{offer.count === 1 ? " is" : "s are"} missing for FY{" "}
        {financialYearLabel(year)} and can start from FY{" "}
        {offer.source_financial_year_label}. They stay provisional until you
        check each against the official page.
      </p>
      <Button onClick={() => setOpen(true)}>
        Start from {offer.source_financial_year_label}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              Start FY {financialYearLabel(year)} from earlier years
            </DialogTitle>
            <DialogDescription>
              Each missing figure is proposed with its most recent earlier
              figure. Nothing is saved until you accept, and each figure then
              stays <strong>provisional</strong> (shown on the Tax estimate)
              until you confirm it against the official page.
            </DialogDescription>
          </DialogHeader>
          {preview.isPending && <p className="text-muted-foreground">Loading…</p>}
          {preview.error && (
            <p className="text-destructive">
              Could not load the proposal: {preview.error.message}
            </p>
          )}
          {data && (
            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
              <ul
                aria-label="Proposed figures"
                className="flex flex-col divide-y rounded-md border text-sm"
              >
                {data.proposals.map((p) => (
                  <li
                    key={p.key}
                    data-testid={`carry-${p.key}`}
                    className="flex flex-col gap-1 p-2"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="font-medium">{p.label}</span>
                      <span className="font-mono">
                        {figureText(p, p.value)}{" "}
                        <span className="font-sans text-xs text-muted-foreground">
                          from FY {p.source_financial_year_label}
                        </span>
                      </span>
                    </div>
                    {p.scheduled_change && (
                      <ScheduledChange text={p.scheduled_change} />
                    )}
                  </li>
                ))}
              </ul>
              {data.no_earlier.length > 0 && (
                <div data-testid="no-earlier" className="text-sm">
                  <p className="font-medium">No earlier figure; capture it</p>
                  <p className="text-muted-foreground">
                    {data.no_earlier.map((n) => n.label).join("; ")}.
                  </p>
                </div>
              )}
            </div>
          )}
          <GeneralError error={carry.error} />
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              disabled={carry.isPending || !data || data.proposals.length === 0}
              onClick={() => carry.mutate()}
            >
              Carry {data?.proposals.length ?? offer.count} figures forward
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** Confirm one carried figure as unchanged, with its known change shown beside the control. */
export function ConfirmUnchanged({
  figure,
  onDone,
}: {
  figure: RuleFigure;
  onDone: (message: string) => void;
}) {
  const capture = figure.capture;
  const confirm = useMutation({
    mutationFn: () => confirmCarriedFigure(capture?.id ?? 0),
    onSuccess: () =>
      onDone(
        `Confirmed ${figure.label} for FY ${figure.financial_year_label} as unchanged.`,
      ),
  });
  if (!capture || !isCarried(figure)) return null;
  return (
    <div className="mt-2 flex flex-col items-start gap-1">
      {figure.scheduled_change && (
        <ScheduledChange text={figure.scheduled_change} />
      )}
      <Button
        variant="outline"
        aria-label={`Confirm ${figure.label} unchanged`}
        disabled={confirm.isPending}
        onClick={() => confirm.mutate()}
      >
        Confirm unchanged
      </Button>
      <GeneralError error={confirm.error} />
    </div>
  );
}

/** Carried figures grouped by the official page that holds them, each confirmable in one action. */
export function CarriedPages({
  figures,
  year,
  onDone,
}: {
  figures: RuleFigure[];
  year: number;
  onDone: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const carried = figures.filter(isCarried);
  const pages = new Map<string, RuleFigure[]>();
  for (const figure of carried) {
    pages.set(figure.page_url, [...(pages.get(figure.page_url) ?? []), figure]);
  }
  const confirm = useMutation({
    mutationFn: (url: string) => confirmCarriedPage(year, url),
    onSuccess: (done) => {
      setConfirming(null);
      const own = done.needs_own_confirm.length;
      onDone(
        `Confirmed ${done.confirmed.length} figure${done.confirmed.length === 1 ? "" : "s"} as unchanged.${own ? ` ${own} with a known change must be confirmed on its own row.` : ""}`,
      );
    },
  });
  if (carried.length === 0) return null;
  return (
    <section
      aria-label="Carried figures to check"
      className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
    >
      <div>
        <p className="font-medium">
          {carried.length} carried figure{carried.length === 1 ? "" : "s"}{" "}
          not yet checked against the page
        </p>
        <p className="text-muted-foreground">
          Open each official page. If its figures are unchanged, confirm them
          together; if one changed, correct it from its row. The Tax estimate
          shows these as provisional until then.
        </p>
      </div>
      <ul className="flex flex-col gap-3">
        {[...pages.entries()].map(([url, group]) => {
          const own = group.filter((f) => f.scheduled_change);
          const bulk = group.filter((f) => !f.scheduled_change);
          const titles = [...new Set(groupFigures(group).map((g) => g.title))];
          return (
            <li
              key={url}
              data-testid="carried-page"
              className="flex flex-col gap-2 rounded-md border bg-background p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <p>
                  <span className="font-medium">{titles.join(", ")}</span>:{" "}
                  {group.length} carried
                </p>
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline underline-offset-4"
                >
                  Open the official page
                </a>
                {own.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {own.length} with a known change (
                    {own.map((f) => f.label).join("; ")}) must be confirmed on
                    their own row.
                  </p>
                )}
              </div>
              {bulk.length > 0 && (
                <Button
                  variant="outline"
                  onClick={() => setConfirming(url)}
                  aria-label={`Confirm ${bulk.length} unchanged from ${titles.join(", ")}`}
                >
                  Confirm {bulk.length} unchanged
                </Button>
              )}
              <AlertDialog
                open={confirming === url}
                onOpenChange={(open) => !open && setConfirming(null)}
              >
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      Confirm {bulk.length} figure
                      {bulk.length === 1 ? "" : "s"} as unchanged?
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      You are saying you opened the official page and these
                      figures are the same as the earlier year&apos;s. Each is
                      saved as a typed figure from today.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <ul className="flex max-h-60 flex-col gap-1 overflow-y-auto text-sm">
                    {bulk.map((f) => (
                      <li key={f.key} className="flex justify-between gap-3">
                        <span>{f.label}</span>
                        <Badge variant="closed" className="font-mono">
                          {f.value != null ? figureText(f, f.value) : ""}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                  <GeneralError error={confirm.error} />
                  <AlertDialogFooter>
                    <AlertDialogCancel>Not yet</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={(e) => {
                        e.preventDefault(); // stay open on a failure; success closes it
                        confirm.mutate(url);
                      }}
                    >
                      Confirm unchanged
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

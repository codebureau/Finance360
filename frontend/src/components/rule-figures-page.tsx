import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { FinancialYearSelect } from "@/components/financial-year-select";
import { GeneralError } from "@/components/form-fields";
import {
  CarriedPages,
  CarryOffer,
  ConfirmUnchanged,
} from "@/components/rule-figures-carry";
import { RuleFigureReader } from "@/components/rule-figure-reader";
import { RuleFigureSheet } from "@/components/rule-figure-sheet";
import { shortDate } from "@/components/tax-record-trace";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader } from "@/components/ui/page-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  fetchRuleFigures,
  figureText,
  groupFigures,
  withdrawRuleFigure,
  type NoLongerFits,
  type RuleFigure,
} from "@/lib/rule-figures";
import { currentFinancialYear, financialYearLabel } from "@/lib/tax";

/** What a figure falls back to once its capture is withdrawn, in words. */
function fallbackText(figure: RuleFigure): string {
  return figure.shipped_value != null
    ? `Use the release's ${figureText(figure, figure.shipped_value)} again?`
    : `Withdraw the captured figure? FY ${figure.financial_year_label} then has no ${figure.label.toLowerCase()} until you capture one.`;
}

export function WithdrawDialog({
  captureId,
  title,
  description,
  open,
  onOpenChange,
  onDone,
  returnFocus,
}: {
  captureId: number;
  title: string;
  description: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: (message: string) => void;
  returnFocus?: (e: Event) => void;
}) {
  const withdraw = useMutation({
    mutationFn: () => withdrawRuleFigure(captureId),
    onSuccess: () => onDone("Withdrawn"),
  });
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent onCloseAutoFocus={returnFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={withdraw.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Keep captured figure</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault(); // stay open on a failure; the success path closes it
              withdraw.mutate();
            }}
          >
            Withdraw figure
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function StateBadge({
  figure,
  version,
}: {
  figure: RuleFigure;
  version: string | null;
}) {
  if (figure.state === "captured" && figure.capture?.method === "carried") {
    return (
      <Badge variant="flagged" className="whitespace-normal">
        Carried from an earlier year, not checked against the page
      </Badge>
    );
  }
  if (figure.state === "captured" && figure.capture) {
    return (
      <Badge variant="info" className="whitespace-normal">
        Captured {shortDate(figure.capture.captured_at.slice(0, 10))} by the{" "}
        {figure.capture.captured_by}
        {figure.capture.method === "ai" && ". Read by AI"}
        {figure.capture.method === "matched" &&
          ". Matched by words on the page"}
      </Badge>
    );
  }
  if (figure.state === "shipped") {
    const release = version?.split("+")[0].split("/").pop();
    return (
      <Badge variant="closed">Shipped{release ? ` (${release})` : ""}</Badge>
    );
  }
  return <Badge variant="flagged">Missing</Badge>;
}

function FigureRow({
  figure,
  version,
  onDone,
}: {
  figure: RuleFigure;
  version: string | null;
  onDone: (message: string) => void;
}) {
  const [sheet, setSheet] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const capture = figure.capture;
  // The dialogs open from a menu item, so hand focus back to the row's menu button on close.
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };

  return (
    <TableRow data-state={figure.state}>
      <TableCell className="min-w-36 whitespace-normal">
        <p className="font-medium">{figure.label}</p>
        <p className="text-xs text-muted-foreground">
          <a
            href={figure.page_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline-offset-4 hover:underline"
          >
            Official page
          </a>
          {!figure.url_confirmed && " (address not confirmed)"}
        </p>
        {figure.provisional_note && (
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <Badge variant="flagged">Provisional</Badge>
            {figure.provisional_note}
          </p>
        )}
        {capture?.quote && (
          <p className="mt-1 text-xs text-muted-foreground">
            “{capture.quote}”
          </p>
        )}
      </TableCell>
      <TableCell className="whitespace-normal">
        <p className="font-mono text-sm break-all">
          {figure.value != null ? (
            figureText(figure, figure.value)
          ) : (
            <span className="font-sans text-muted-foreground">Missing</span>
          )}
        </p>
        <p className="mt-1">
          <StateBadge figure={figure} version={version} />
        </p>
        <ConfirmUnchanged figure={figure} onDone={onDone} />
        {figure.state === "captured" && figure.shipped_value != null && (
          <p className="mt-1 text-xs text-muted-foreground">
            The release holds {figureText(figure, figure.shipped_value)}.
          </p>
        )}
        <p className="mt-1 text-xs text-muted-foreground sm:hidden">
          {figure.previous
            ? `FY ${figure.previous.financial_year_label}: ${figureText(figure, figure.previous.value)}`
            : "No earlier year"}
        </p>
      </TableCell>
      <TableCell className="hidden font-mono text-sm break-all whitespace-normal sm:table-cell">
        {figure.previous ? (
          <>
            {figureText(figure, figure.previous.value)}
            <span className="block font-sans text-xs text-muted-foreground">
              FY {figure.previous.financial_year_label}
            </span>
          </>
        ) : (
          <span className="font-sans text-muted-foreground">None</span>
        )}
      </TableCell>
      <TableCell className="w-10 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              ref={menuRef}
              variant="ghost"
              aria-label={`Actions for ${figure.label}`}
              className="px-2"
            >
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onSelect={() => setSheet(true)}>
              {capture ? "Correct figure" : "Capture figure"}
            </DropdownMenuItem>
            {capture && (
              <DropdownMenuItem
                destructive
                onSelect={() => setWithdrawing(true)}
              >
                Withdraw figure
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        {sheet && (
          <RuleFigureSheet
            figure={figure}
            open={sheet}
            onOpenChange={setSheet}
            onDone={(message) => {
              setSheet(false);
              onDone(
                `${message} ${figure.label} for FY ${figure.financial_year_label}.`,
              );
            }}
          />
        )}
        {capture && (
          <WithdrawDialog
            captureId={capture.id}
            title={`Withdraw the captured ${figure.label.toLowerCase()}?`}
            description={fallbackText(figure)}
            open={withdrawing}
            onOpenChange={setWithdrawing}
            returnFocus={returnFocus}
            onDone={(message) => {
              setWithdrawing(false);
              onDone(
                `${message} ${figure.label} for FY ${figure.financial_year_label}.`,
              );
            }}
          />
        )}
      </TableCell>
    </TableRow>
  );
}

/** A capture a release made invalid is ignored (the shipped figure applies) until it is re-captured. */
function NoLongerFitting({
  items,
  figures,
  fy,
  onDone,
}: {
  items: NoLongerFits[];
  figures: RuleFigure[];
  fy: number;
  onDone: (message: string) => void;
}) {
  const [correcting, setCorrecting] = useState<RuleFigure | null>(null);
  const [withdrawing, setWithdrawing] = useState<NoLongerFits | null>(null);
  if (items.length === 0) return null;
  const nameOf = (item: NoLongerFits) =>
    figures.find((f) => f.key === item.key)?.label ?? item.key;
  return (
    <div
      role="alert"
      data-testid="no-longer-fits"
      className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
    >
      <p className="font-medium">
        {items.length === 1
          ? "A captured figure no longer fits"
          : `${items.length} captured figures no longer fit`}
      </p>
      <p className="text-muted-foreground">
        A release changed what these figures may be, so they are ignored and the
        released figure applies. Correct each one, or withdraw it.
      </p>
      <ul className="flex flex-col gap-3">
        {items.map((item) => {
          const figure =
            item.financial_year === fy
              ? figures.find((f) => f.key === item.key)
              : undefined;
          return (
            <li key={item.capture_id} className="flex flex-col gap-1">
              <p>
                <span className="font-medium">{nameOf(item)}</span> for FY{" "}
                {item.financial_year_label}: {item.reason}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {figure ? (
                  <Button
                    variant="outline"
                    onClick={() => setCorrecting(figure)}
                  >
                    Correct {nameOf(item)}
                  </Button>
                ) : (
                  <Link
                    to="/rule-figures"
                    search={{ fy: item.financial_year }}
                    className="text-primary underline underline-offset-4"
                  >
                    Show FY {item.financial_year_label} to correct it
                  </Link>
                )}
                <Button variant="outline" onClick={() => setWithdrawing(item)}>
                  Withdraw {nameOf(item)}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {correcting && (
        <RuleFigureSheet
          figure={correcting}
          open
          onOpenChange={(open) => !open && setCorrecting(null)}
          onDone={(message) => {
            setCorrecting(null);
            onDone(
              `${message} ${correcting.label} for FY ${correcting.financial_year_label}.`,
            );
          }}
        />
      )}
      {withdrawing && (
        <WithdrawDialog
          captureId={withdrawing.capture_id}
          title={`Withdraw the captured ${nameOf(withdrawing).toLowerCase()}?`}
          description={`It is already ignored. Withdrawing ends it for good; the released figure for FY ${withdrawing.financial_year_label} keeps applying.`}
          open
          onOpenChange={(open) => !open && setWithdrawing(null)}
          onDone={(message) => {
            setWithdrawing(null);
            onDone(
              `${message} ${nameOf(withdrawing)} for FY ${withdrawing.financial_year_label}.`,
            );
          }}
        />
      )}
    </div>
  );
}

/**
 * Rule figures at `/rule-figures`: every figure a financial year needs, who supplies it (the release,
 * a capture or nobody yet), and typed capture, correction and withdrawal (ADR-0022). The year and the
 * Missing-only filter are URL search params.
 */
export function RuleFiguresPage({
  fy,
  missingOnly,
  onSearch,
}: {
  fy: number | undefined;
  missingOnly: boolean;
  onSearch: (patch: { fy?: number; missing?: true }) => void;
}) {
  const year = fy ?? currentFinancialYear();
  const queryClient = useQueryClient();
  const figures = useQuery({
    queryKey: ["rule-figures", year],
    queryFn: () => fetchRuleFigures(year),
  });
  const [done, setDone] = useState<string | null>(null);
  const data = figures.data;
  const refreshed = (message: string) => {
    setDone(message);
    // A figure changes what records count and what an estimate uses, so refresh everything on screen.
    void queryClient.invalidateQueries();
  };

  const first = data?.first_financial_year ?? year;
  const last = data?.last_financial_year ?? year;
  const years = Array.from(
    { length: Math.max(last - first + 1, 1) },
    (_, i) => last - i,
  );
  if (!years.includes(year)) years.push(year);
  const shown = (data?.figures ?? []).filter(
    (f) => !missingOnly || f.state === "missing",
  );

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <PageHeader
        title="Rule figures"
        action={
          data && (
            <RuleFigureReader
              key={year}
              figures={data.figures}
              year={year}
              onCaptured={refreshed}
            />
          )
        }
        description={
          <>
            The official rates and thresholds each financial year uses. Read a
            figure from the official page, type it here (or paste the page and
            let AI find it), and records in that year count it.{" "}
            <Link
              to="/guide/rule-figures"
              className="text-primary underline underline-offset-4"
            >
              How to capture a figure
            </Link>
          </>
        }
      />

      <form
        role="search"
        aria-label="Choose a financial year"
        className="flex flex-wrap items-end gap-4"
        onSubmit={(e) => e.preventDefault()}
      >
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Financial year
          <FinancialYearSelect
            value={year}
            onChange={(next) => {
              setDone(null);
              onSearch({ fy: next, missing: missingOnly || undefined });
            }}
            years={years}
          />
        </label>
        <label className="flex min-h-[var(--control-h)] items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={missingOnly}
            onChange={(e) =>
              onSearch({ fy: year, missing: e.target.checked || undefined })
            }
          />
          Missing only
        </label>
      </form>

      {done && (
        <p
          role="status"
          data-testid="rule-figures-done"
          className="rounded-md border border-success/30 bg-success/10 p-3 text-sm"
        >
          {done}
        </p>
      )}
      {data?.carry_offer && (
        <CarryOffer
          key={year}
          year={year}
          offer={data.carry_offer}
          onDone={refreshed}
        />
      )}
      {data && (
        <CarriedPages figures={data.figures} year={year} onDone={refreshed} />
      )}
      {data && (
        <NoLongerFitting
          items={data.no_longer_fit}
          figures={data.figures}
          fy={year}
          onDone={refreshed}
        />
      )}
      {figures.isPending && <p className="text-muted-foreground">Loading…</p>}
      {figures.error && (
        <p className="text-destructive">
          Could not load the Rule figures: {figures.error.message}
        </p>
      )}
      {data && shown.length === 0 && (
        <p className="text-muted-foreground" data-testid="no-missing">
          {missingOnly
            ? `Nothing is missing for FY ${financialYearLabel(year)}.`
            : "There are no figures to capture."}
        </p>
      )}
      {groupFigures(shown).map((group) => (
        <section
          key={group.title}
          aria-label={group.title}
          className="flex min-w-0 flex-col gap-2"
        >
          <h2 className="text-base font-semibold">{group.title}</h2>
          <Table aria-label={`${group.title} figures`}>
            <TableHeader>
              <TableRow>
                <TableHead>Figure</TableHead>
                <TableHead>FY {financialYearLabel(year)}</TableHead>
                <TableHead className="hidden sm:table-cell">
                  Last year
                </TableHead>
                <TableHead className="relative w-10">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {group.figures.map((figure) => (
                <FigureRow
                  key={figure.key}
                  figure={figure}
                  version={data?.rule_pack_version ?? null}
                  onDone={refreshed}
                />
              ))}
            </TableBody>
          </Table>
        </section>
      ))}
      {data?.rule_pack_version && (
        <p className="text-xs text-muted-foreground">
          Rule pack {data.rule_pack_version}
        </p>
      )}
    </div>
  );
}

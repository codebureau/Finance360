import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useMemo } from "react";

import { FinancialYearSelect, financialYears } from "@/components/financial-year-select";
import { TaxRecordSheet } from "@/components/tax-record-form";
import { useRefreshTax } from "@/components/tax-records-page";
import { ShareChart } from "@/components/share-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toneOf, TOKEN_BG } from "@/lib/entity-colour";
import { packageHeadline, packageShareRows, PACKAGE_FORMS } from "@/lib/income-share";
import { cn } from "@/lib/utils";
import {
  currentFinancialYear,
  fetchIncomeReconciliation,
  fetchTaxSetup,
  financialYearLabel,
  shortPercent,
  type IncomeReconciliation,
} from "@/lib/tax";

function Swatch({ sliceKey }: { sliceKey: string }) {
  const tone = toneOf("package-part", sliceKey === "other" ? "other_deductions" : sliceKey);
  return (
    <span
      aria-hidden
      className={cn("inline-block size-3 shrink-0 rounded-sm", typeof tone === "object" && TOKEN_BG[tone.token])}
    />
  );
}

function Reconciliation({ item }: { item: IncomeReconciliation }) {
  const rows = useMemo(() => packageShareRows(item), [item]);
  const gap = item.slices.find((s) => s.key === "unreconciled");
  const negative = item.unreconciled.startsWith("-");
  const nothing = item.package === "0.00" && item.slices.every((s) => s.amount === "0.00");
  if (nothing) {
    return (
      <Card aria-label={`Income reconciliation for ${item.name}`} data-testid="reconciliation">
        <CardHeader>
          <CardTitle>{item.name}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No figures on the income statements.</p>
        </CardContent>
      </Card>
    );
  }
  const figures = (
    <div className="flex flex-col gap-4">
      <dl className="grid min-w-0 grid-cols-[auto_auto] justify-start gap-x-6 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Gross pay</dt>
        <dd className="text-right">
          <Money amount={item.gross_pay} />
        </dd>
        <dt className="text-muted-foreground">Employer super</dt>
        <dd className="text-right">
          <Money amount={item.employer_super} />
        </dd>
        <dt className="font-medium">Total package (100%)</dt>
        <dd className="text-right font-medium">
          <Money amount={item.package} />
        </dd>
      </dl>
      <Table aria-label={`Where ${item.name}'s package goes`}>
        <TableHeader>
          <TableRow>
            <TableHead>Where it went</TableHead>
            <TableHead className="text-right">Amount</TableHead>
            <TableHead className="text-right">Share</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {item.slices
            .filter((s) => s.key !== "unreconciled")
            .map((s) => (
              <TableRow key={s.key}>
                <TableCell>
                  <span className="flex items-center gap-2">
                    <Swatch sliceKey={s.key} />
                    {s.name}
                  </span>
                  {s.key === "take_home" && item.take_home_worked_out !== "0.00" && (
                    <span className="block text-xs text-muted-foreground" data-testid="take-home-worked-out">
                      {item.take_home_worked_out === s.amount ? (
                        "Worked out, not stated"
                      ) : (
                        <>
                          Includes <Money amount={item.take_home_worked_out} /> worked out, not stated
                        </>
                      )}
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Money amount={s.amount} />
                </TableCell>
                <TableCell className="text-right" title={s.share_percent ? `${s.share_percent}%` : undefined}>
                  {shortPercent(s.share_percent)}
                </TableCell>
              </TableRow>
            ))}
          <TableRow data-testid="unreconciled">
            <TableCell>
              <span className="flex items-center gap-2">
                {gap ? <Swatch sliceKey="unreconciled" /> : <span className="size-3 shrink-0" aria-hidden />}
                Unreconciled
              </span>
            </TableCell>
            <TableCell className="text-right">
              <Money amount={item.unreconciled} />
            </TableCell>
            <TableCell className="text-right" title={gap?.share_percent ? `${gap.share_percent}%` : undefined}>
              {gap ? shortPercent(gap.share_percent) : <span className="text-muted-foreground">Not drawn</span>}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
  );
  return (
    <div role="group" aria-label={`Income reconciliation for ${item.name}`} data-testid="reconciliation">
      <ShareChart
        viewKey="tax.reconciliation"
        title={item.name}
        headline={packageHeadline(rows)}
        rows={rows}
        offered={PACKAGE_FORMS}
        chartLabel={`Where ${item.name}'s total package goes.`}
        figures={figures}
        after={
          <div className="flex flex-col gap-2">
            <p className="text-sm">
              <span className="text-muted-foreground">Reportable fringe benefits </span>
              <Money amount={item.reportable_fringe_benefits} />
              <span className="block text-xs text-muted-foreground">
                A grossed-up, notional figure, so it is shown beside the chart, not in it.
              </span>
            </p>
            {negative && (
              <p className="text-sm" data-testid="negative-note">
                The money accounted for is more than the package, so Unreconciled is negative (
                <Money amount={item.unreconciled} />). It is not drawn as a part; the shares divide the parts alone.
              </p>
            )}
            {item.super_insurance_premiums !== "0.00" && (
              <p className="text-sm text-muted-foreground" data-testid="insurance-note">
                <Money amount={item.super_insurance_premiums} /> of insurance premiums was paid from the super
                balance. It was not paid from pay, so it is not part of the package.
              </p>
            )}
          </div>
        }
      />
    </div>
  );
}

/** `/tax/reconciliation?fy=2026`: where each Member's total package goes, and the Household's. */
export function IncomeReconciliationPage({ fy, onYear }: { fy: number | undefined; onYear: (year: number) => void }) {
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const queryClient = useQueryClient();
  const refresh = useRefreshTax();
  // The URL's year, else the latest year the Rule pack holds figures for (as the Tax estimate
  // does), else the current one.
  const packYears = setup.data?.years.map((y) => y.financial_year) ?? [];
  const year = fy ?? packYears.at(-1) ?? currentFinancialYear();
  const result = useQuery({
    queryKey: ["income-reconciliation", year],
    queryFn: () => fetchIncomeReconciliation(year),
  });
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <PageHeader
        title="Income reconciliation"
        description="Where the total package (gross pay plus employer super) goes, from each Member's income statements. A fact at gross level, not an estimate."
        action={
          <TaxRecordSheet
            newType="income_statement"
            defaultYear={year}
            onSaved={() => {
              refresh();
              void queryClient.invalidateQueries({ queryKey: ["income-reconciliation"] });
            }}
            trigger={
              <Button>
                <Plus size={16} aria-hidden />
                Add income statement
              </Button>
            }
          />
        }
      />
      <Card>
        <CardContent className="pt-6">
          <label className="flex max-w-xs flex-col gap-1 text-sm">
            Financial year
            <FinancialYearSelect
              value={year}
              onChange={(next) => next !== undefined && onYear(next)}
              years={financialYears([year, ...packYears])}
              describedBy="reconciliation-year-hint"
            />
            <span id="reconciliation-year-hint" className="text-xs text-muted-foreground">
              1 July {year - 1} to 30 June {year}.
            </span>
          </label>
        </CardContent>
      </Card>
      {result.isPending && <p className="text-muted-foreground">Loading…</p>}
      {result.error && <p className="text-destructive">Could not load the reconciliation: {result.error.message}</p>}
      {result.data && result.data.members.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="reconciliation-empty">
          No income statement falls in FY {financialYearLabel(result.data.financial_year)}. Use Add income statement
          above to enter one. An income statement is a kind of Tax record; see{" "}
          <Link to="/tax/records" search={{ fy: result.data.financial_year }} className="text-primary underline-offset-4 hover:underline">
            Tax records
          </Link>
          .
        </p>
      )}
      {result.data?.members.map((m) => <Reconciliation key={m.member_id} item={m} />)}
      {result.data?.household && result.data.members.length > 1 && <Reconciliation item={result.data.household} />}
    </div>
  );
}

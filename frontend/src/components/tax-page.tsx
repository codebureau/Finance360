import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Calculator } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { FinancialYearSelect } from "@/components/financial-year-select";
import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { SaveEstimateDialog } from "@/components/save-estimate-dialog";
import { Division293 } from "@/components/tax-division-293";
import { EstimateNotices, firstName } from "@/components/tax-estimate-view";
import { TaxWaterfallCard } from "@/components/tax-waterfall-card";
import { LabelTotals } from "@/components/tax-label-totals";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError, fetchHousehold, type Member } from "@/lib/household";
import {
  calculateTaxEstimate,
  currentFinancialYear,
  fetchTaxSetup,
  financialYearLabel,
  TAX_SIMPLIFICATIONS,
  type TaxEstimate,
  type TaxRequest,
  type TaxSetup,
} from "@/lib/tax";

const MAX_MEMBERS = 2;

/** The fix for a year with no figures, shown wherever the screen says there are none. */
function CaptureHint({ year }: { year: number }) {
  return (
    <>
      Capture them on{" "}
      <Link to="/rule-figures" search={{ fy: year }} className="text-primary underline underline-offset-4">
        Rule figures (FY {financialYearLabel(year)})
      </Link>
      .
    </>
  );
}

function YearNotice({ setup, year }: { setup: TaxSetup; year: number }) {
  const held = setup.years.find((y) => y.financial_year === year);
  if (setup.years.length === 0) {
    return (
      <p role="status" data-testid="no-figures" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
        No tax figures have been captured in the Rule pack yet, so no estimate can be calculated. For FY{" "}
        {financialYearLabel(year)} <CaptureHint year={year} />
      </p>
    );
  }
  if (!held) {
    return (
      <p role="status" data-testid="year-not-held" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
        The Rule pack holds no tax figures for FY {financialYearLabel(year)}, and an estimate never uses another
        year&apos;s. <CaptureHint year={year} />
      </p>
    );
  }
  if (!held.complete) {
    return (
      <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
        FY {held.label} is missing {held.missing.length} {held.missing.length === 1 ? "figure" : "figures"}:{" "}
        {held.missing.join(", ")}. <CaptureHint year={year} />
      </p>
    );
  }
  return null;
}

function Results({
  estimate,
  members,
  request,
}: {
  estimate: TaxEstimate;
  members: Member[];
  request: TaxRequest;
}) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [estimate]);
  const navigate = useNavigate();
  return (
    <div data-testid="tax-result" className="flex flex-col gap-4">
      <Card aria-labelledby="tax-result-title">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle id="tax-result-title" ref={ref} tabIndex={-1} className="outline-none">
              Estimate for FY {estimate.financial_year_label}
            </CardTitle>
            {estimate.incomplete && <Badge variant="flagged">Incomplete</Badge>}
            {estimate.provisional.length > 0 && <Badge variant="flagged">Provisional</Badge>}
            <div className="ml-auto">
              <SaveEstimateDialog
                request={{ ...request, whatifId: null }}
                onSaved={(saved) =>
                  void navigate({ to: "/tax/estimates/$estimateId", params: { estimateId: String(saved.id) } })
                }
              />
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            Rule pack {estimate.rule_pack_version}, engine v{estimate.engine_version}. Not saved until you press Save
            estimate.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 empty:hidden">
          <EstimateNotices
            incomplete={estimate.incomplete}
            provisional={estimate.provisional}
            unmodelled={estimate.unmodelled}
          />
        </CardContent>
      </Card>
      <TaxWaterfallCard members={estimate.members} household={estimate.household} incomplete={estimate.incomplete} />
      <Card>
        <CardContent className="flex flex-col gap-4 pt-6">
          {estimate.members.some((m) => m.notes.length > 0) && (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {estimate.members.flatMap((m) =>
                m.notes.map((note) => (
                  <li key={`${m.member_id}-${note}`}>
                    {members.length > 1 ? `${firstName(m.name)}: ` : ""}
                    {note}
                  </li>
                )),
              )}
            </ul>
          )}
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {TAX_SIMPLIFICATIONS.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function TaxEstimator({
  setup,
  members,
  initialYear,
  onYear,
}: {
  setup: TaxSetup;
  members: Member[];
  initialYear: number;
  onYear: (year: number) => void;
}) {
  const [year, setYear] = useState<number | undefined>(initialYear);
  const [dependants, setDependants] = useState("0");
  const mutation = useMutation({ mutationFn: (request: TaxRequest) => calculateTaxEstimate(request) });

  const error = mutation.error;

  return (
    <form
      id="tax-form"
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (year === undefined) return;
        onYear(year);
        mutation.mutate({
          financialYear: year,
          dependants,
          memberIds: members.slice(0, MAX_MEMBERS).map((m) => m.id),
        });
      }}
    >
      <PageHeader
        title="Tax estimate"
        description="The tax, offsets, Medicare levy, surcharge, HELP and net outcome for a financial year, from each Member's Tax records."
        action={
          <Button type="submit" form="tax-form" disabled={mutation.isPending || year === undefined}>
            <Calculator size={16} />
            {mutation.isPending ? "Calculating…" : "Calculate"}
          </Button>
        }
      />
      <Card>
        <CardContent className="flex flex-col gap-3 pt-6">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              Financial year
              <FinancialYearSelect
                value={year}
                onChange={setYear}
                years={[...new Set([initialYear, ...setup.years.map((y) => y.financial_year)])].sort((a, b) => b - a)}
                describedBy="tax-year-hint"
              />
              <span id="tax-year-hint" className="text-xs text-muted-foreground">
                {year === undefined
                  ? "The years the Rule pack holds tax figures for."
                  : `1 July ${year - 1} to 30 June ${year}.`}
              </span>
              <FieldMessage error={error} field="financial_year" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              Dependent children
              <input
                className={inputClass}
                inputMode="numeric"
                value={dependants}
                onChange={(e) => setDependants(e.target.value)}
              />
              <FieldMessage error={error} field="dependants" />
            </label>
          </div>
          {year !== undefined && <YearNotice setup={setup} year={year} />}
          {setup.years.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Figures held for:{" "}
              {setup.years.map((y) => `FY ${y.label}${y.complete ? "" : " (incomplete)"}`).join(", ")} (
              {setup.rule_pack_version}).
            </p>
          )}
          {members.length > MAX_MEMBERS && (
            <p className="text-xs text-muted-foreground">
              An estimate covers two Members; the first two are shown.
            </p>
          )}
        </CardContent>
      </Card>

      {error && <ErrorNotice error={error} />}
      {mutation.data && mutation.variables && (
        <Results estimate={mutation.data} members={members} request={mutation.variables} />
      )}
      {mutation.data && <Division293 data={mutation.data.division_293} year={mutation.data.financial_year} />}

      {year !== undefined && year >= 1990 && year <= 2200 && (
        <LabelTotals
          financialYear={year}
          description={
            <>
              These are the totals the estimate uses, added up from your Tax records. To change one, add or edit a
              record in{" "}
              <Link to="/tax/records" search={{ fy: year }} className="text-primary underline-offset-4 hover:underline">
                Tax records
              </Link>
              .
            </>
          }
        />
      )}
    </form>
  );
}

/** A failure with no box of its own (the year has no figures, say) is shown here, in words. */
/** The financial year an error message points at ("Capture it on Rule figures (FY 2026-27)."), or undefined. */
function figuresYearIn(messages: string[]): number | undefined {
  const found = messages.map((m) => /Rule figures \(FY (\d{4})-\d{2}\)/.exec(m)).find((m) => m);
  return found ? Number(found[1]) + 1 : undefined;
}

function ErrorNotice({ error }: { error: unknown }) {
  if (!(error instanceof Error)) return null;
  if (error instanceof ApiError && Object.keys(error.fields).length > 0) {
    const figuresYear = figuresYearIn(Object.values(error.fields));
    return (
      <div role="alert" data-testid="tax-error" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
        <p className="font-medium">The estimate could not be calculated.</p>
        {Object.entries(error.fields).map(([field, message]) => (
          <p key={field}>{message}</p>
        ))}
        {figuresYear !== undefined && (
          <p>
            <Link to="/rule-figures" search={{ fy: figuresYear }} className="text-primary underline underline-offset-4">
              Open Rule figures for FY {financialYearLabel(figuresYear)}
            </Link>
          </p>
        )}
      </div>
    );
  }
  return (
    <div role="alert" data-testid="tax-error" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
      <GeneralError error={error} />
    </div>
  );
}

export function TaxPage({ fy, onYear }: { fy: number | undefined; onYear: (year: number) => void }) {
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });

  if (setup.isPending || household.isPending) return <p className="text-muted-foreground">Loading…</p>;
  const failure = setup.error ?? household.error;
  if (failure || !setup.data || !household.data) {
    return <p className="text-destructive">Could not load the tax estimate: {failure?.message}</p>;
  }
  if (household.data.members.length === 0) {
    return (
      <div>
        <PageHeader title="Tax estimate" />
        <p className="text-sm text-muted-foreground">Add a Member to the Household first.</p>
      </div>
    );
  }
  const latest = setup.data.years.at(-1)?.financial_year;
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <TaxEstimator
        setup={setup.data}
        members={household.data.members.slice(0, MAX_MEMBERS)}
        initialYear={fy ?? latest ?? currentFinancialYear()}
        onYear={onYear}
      />
    </div>
  );
}

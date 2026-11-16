import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { GeneralError } from "@/components/form-fields";
import { LinkedDocuments, useLinkedDocuments } from "@/components/linked-documents";
import { TaxRecordTrace } from "@/components/tax-record-trace";
import { TaxRecordSheet } from "@/components/tax-record-form";
import { Counted, useRefreshTax } from "@/components/tax-records-page";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
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
  deleteTaxRecord,
  fetchTaxRecord,
  financialYearLabel,
  recordDates,
  REQUIRED_STATEMENT_FIGURES,
  STATEMENT_FIGURES,
  STATEMENT_GROUPS,
  TAX_RECORD_KIND,
  WEEKDAYS,
  type TaxAsset,
  type TaxCar,
  type TaxHomeOffice,
  type TaxPreviousYear,
  type TaxRecord,
} from "@/lib/tax";
import { cn } from "@/lib/utils";

function BackLink() {
  return (
    <Link
      to="/tax/records"
      search={{}}
      className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}
    >
      <ArrowLeft size={16} aria-hidden />
      Tax records
    </Link>
  );
}

function Detail({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** Delete Tax record, always confirmed: it names the record and says what changes. */
function DeleteDialog({
  record,
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  record: TaxRecord;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const navigate = useNavigate();
  const refresh = useRefreshTax();
  const files = useLinkedDocuments(TAX_RECORD_KIND, record.id).data?.length ?? 0;
  const remove = useMutation({
    mutationFn: () => deleteTaxRecord(record.id),
    onSuccess: () => {
      onOpenChange(false);
      refresh();
      void navigate({ to: "/tax/records", search: {} });
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
          <AlertDialogTitle>Delete {record.description}?</AlertDialogTitle>
          <AlertDialogDescription>
            The {record.label_name} total no longer includes it, and so no
            estimate does.
            {files > 0 &&
              ` ${files === 1 ? "Its document is unlinked and stays" : `Its ${files} documents are unlinked and stay`} in the Library.`}{" "}
            The deletion is recorded in the Audit trail
            {files > 0 && ", with the documents that were linked"}.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={remove.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              remove.mutate();
            }}
          >
            Delete Tax record
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * A depreciating asset's worksheet: year by year, opening value, days held, the deduction that
 * counts toward the Return label, and the closing value. Computed by the server now, never stored.
 */
function AssetSchedule({
  record,
  asset,
}: {
  record: TaxRecord;
  asset: TaxAsset;
}) {
  const startYear = (() => {
    const [y, m] = record.from_on.split("-").map(Number);
    return m >= 7 ? y + 1 : y;
  })();
  return (
    <Card aria-label="Depreciation schedule">
      <CardHeader>
        <CardTitle>Year by year</CardTitle>
        <p className="text-sm text-muted-foreground">
          Each year's deduction counts toward {record.label_name}.{" "}
          {asset.claim_in_full
            ? "It is claimed in full, so only the first year has a deduction. Check the asset is eligible."
            : asset.multiplier_percent != null
              ? `Diminishing value at ${asset.multiplier_percent}% of the base rate (1 divided by the effective life), on the opening value, for the days held in the year. Years after these follow the same rule.`
              : ""}
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {asset.missing.length > 0 && (
          <div
            role="alert"
            className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
          >
            <p className="font-medium">This asset cannot be worked out yet</p>
            <p>
              The Rule pack does not hold{" "}
              <code>{asset.missing.join(", ")}</code> for FY{" "}
              {financialYearLabel(startYear)}, and no figure is guessed, so this
              asset adds nothing to any total and an estimate for that Member
              will not calculate. Capture the figure on{" "}
              <Link
                to="/rule-figures"
                search={{ fy: startYear }}
                className="text-primary underline underline-offset-4"
              >
                Rule figures (FY {financialYearLabel(startYear)})
              </Link>
              .
            </p>
          </div>
        )}
        {asset.schedule.length > 0 && (
          <Table aria-label="Depreciation schedule">
            <TableHeader>
              <TableRow>
                <TableHead>Financial year</TableHead>
                <TableHead className="hidden text-right sm:table-cell">
                  Opening value
                </TableHead>
                <TableHead className="text-right">Deduction</TableHead>
                <TableHead className="text-right">Closing value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {asset.schedule.map((row) => (
                <TableRow key={row.financial_year}>
                  <TableCell>
                    <Link
                      to="/tax/records"
                      search={{
                        fy: row.financial_year,
                        label: record.label_key,
                      }}
                      className="font-medium text-primary underline-offset-4 hover:underline"
                    >
                      {row.financial_year_label}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {asset.claim_in_full
                        ? "claimed in full"
                        : `${row.days} ${row.days === 1 ? "day" : "days"}`}
                    </p>
                    <p className="text-xs text-muted-foreground sm:hidden">
                      Opening <Money amount={row.opening} />
                    </p>
                  </TableCell>
                  <TableCell className="hidden text-right sm:table-cell">
                    <Money amount={row.opening} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Money amount={row.deduction} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Money amount={row.closing} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

/** "Regular: Mon, Tue, Thu, Fri at home, 7.60 hours a day" or "Casual: actual hours". */
function arrangementText(sheet: TaxHomeOffice): string {
  if (sheet.arrangement === "casual") return "Casual: actual hours";
  const pattern = sheet.home_weekdays
    ? `: ${WEEKDAYS.filter((d) => sheet.home_weekdays?.includes(d.code))
        .map((d) => d.label)
        .join(", ")} at home`
    : sheet.office_days_per_month != null
      ? `: ${sheet.office_days_per_month.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")} office days a month`
      : "";
  return `Regular${pattern}${sheet.hours_per_day ? `, ${sheet.hours_per_day} hours a day` : ""}`;
}

/**
 * A home office worksheet, read-only: the months with their home days and hours, then the total
 * hours, the Rule pack rate and the deduction. Computed by the server now, never stored.
 */
function HomeOfficeWorksheet({
  record,
  sheet,
}: {
  record: TaxRecord;
  sheet: TaxHomeOffice;
}) {
  const startYear = (() => {
    const [y, m] = record.from_on.split("-").map(Number);
    return m >= 7 ? y + 1 : y;
  })();
  return (
    <Card aria-label="Home office worksheet">
      <CardHeader>
        <CardTitle>Monthly worksheet</CardTitle>
        <p className="text-sm text-muted-foreground">
          {sheet.arrangement === "casual"
            ? "Each month holds the hours actually worked from home. "
            : `Home days are the working days less leave and office days. Hours are home days times ${sheet.hours_per_day} hours a day. `}
          The total hours at the rate per hour count toward {record.label_name}.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {sheet.missing.length > 0 && (
          <div
            role="alert"
            className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
          >
            <p className="font-medium">This record cannot be worked out yet</p>
            <p>
              The Rule pack does not hold{" "}
              <code>{sheet.missing.join(", ")}</code> for FY{" "}
              {financialYearLabel(startYear)}, and no figure is guessed, so this
              record adds nothing to any total and an estimate for that Member
              will not calculate. Capture the figure on{" "}
              <Link
                to="/rule-figures"
                search={{ fy: startYear }}
                className="text-primary underline underline-offset-4"
              >
                Rule figures (FY {financialYearLabel(startYear)})
              </Link>
              .
            </p>
          </div>
        )}
        <div className="overflow-x-auto">
          <Table aria-label="Home office months">
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                {sheet.arrangement !== "casual" && (
                  <>
                    <TableHead className="text-right">Working days</TableHead>
                    <TableHead className="text-right">Leave days</TableHead>
                    <TableHead className="text-right">Office days</TableHead>
                    <TableHead className="text-right">Home days</TableHead>
                  </>
                )}
                <TableHead className="text-right">Hours</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sheet.months.map((m) => (
                <TableRow key={m.month}>
                  <TableCell className="whitespace-nowrap">
                    {m.month_label}
                  </TableCell>
                  {sheet.arrangement !== "casual" && (
                    <>
                      <TableCell className="text-right font-mono">
                        {m.working_days}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {m.leave_days}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {m.office_days}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {m.home_days}
                      </TableCell>
                    </>
                  )}
                  <TableCell className="text-right font-mono">
                    {m.hours}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <dl className="grid gap-4 sm:grid-cols-3">
          <Detail label="Total hours">
            <span className="font-mono">{sheet.total_hours}</span>
          </Detail>
          <Detail label="Rate per hour">
            {sheet.rate_per_hour != null ? (
              <Money amount={sheet.rate_per_hour} />
            ) : (
              <Badge variant="flagged">Figure needed</Badge>
            )}
          </Detail>
          <Detail label="Deduction">
            {sheet.deduction != null ? (
              <Money amount={sheet.deduction} />
            ) : (
              <Badge variant="flagged">Figure needed</Badge>
            )}
          </Detail>
        </dl>
      </CardContent>
    </Card>
  );
}

/**
 * A car kilometres schedule, read-only: the trips with their totals, then the total kilometres,
 * the kilometres counted (after the Rule pack limit), the rate per kilometre and the deduction.
 * Computed by the server now, never stored.
 */
function CarSchedule({ record, car }: { record: TaxRecord; car: TaxCar }) {
  const startYear = (() => {
    const [y, m] = record.from_on.split("-").map(Number);
    return m >= 7 ? y + 1 : y;
  })();
  return (
    <Card aria-label="Car kilometres">
      <CardHeader>
        <CardTitle>Trips</CardTitle>
        <p className="text-sm text-muted-foreground">
          Each row is the kilometres of one trip and how many times it was made.
          The kilometres counted, up to the limit per car, at the rate per
          kilometre count toward {record.label_name}.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {car.missing.length > 0 && (
          <div
            role="alert"
            className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
          >
            <p className="font-medium">This record cannot be worked out yet</p>
            <p>
              The Rule pack does not hold <code>{car.missing.join(", ")}</code>{" "}
              for FY {financialYearLabel(startYear)}, and no figure is guessed,
              so this record adds nothing to any total and an estimate for that
              Member will not calculate. Capture the figures on{" "}
              <Link
                to="/rule-figures"
                search={{ fy: startYear }}
                className="text-primary underline underline-offset-4"
              >
                Rule figures (FY {financialYearLabel(startYear)})
              </Link>
              .
            </p>
          </div>
        )}
        <div className="overflow-x-auto">
          <Table aria-label="Car trips">
            <TableHeader>
              <TableRow>
                <TableHead>Description</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">km</TableHead>
                <TableHead className="text-right">Times</TableHead>
                <TableHead className="text-right">Total km</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {car.trips.map((t, i) => (
                <TableRow key={i}>
                  <TableCell className="min-w-48">{t.description}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {t.on ?? ""}
                  </TableCell>
                  <TableCell className="text-right font-mono">{t.km}</TableCell>
                  <TableCell className="text-right font-mono">
                    {t.times}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {t.total_km}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Detail label="Total km">
            <span className="font-mono">{car.total_km}</span>
          </Detail>
          <Detail label="Km counted">
            {car.counted_km != null ? (
              <>
                <span className="font-mono">{car.counted_km}</span>
                {car.capped && (
                  <>
                    {" "}
                    <Badge variant="info">Limited to {car.limit_km} km</Badge>
                  </>
                )}
              </>
            ) : (
              <Badge variant="flagged">Figure needed</Badge>
            )}
          </Detail>
          <Detail label="Rate per km">
            {car.rate_per_km != null ? (
              <Money amount={car.rate_per_km} />
            ) : (
              <Badge variant="flagged">Figure needed</Badge>
            )}
          </Detail>
          <Detail label="Deduction">
            {car.deduction != null ? (
              <Money amount={car.deduction} />
            ) : (
              <Badge variant="flagged">Figure needed</Badge>
            )}
          </Detail>
        </dl>
      </CardContent>
    </Card>
  );
}

type Statement = NonNullable<TaxRecord["statement"]>;

/** The statement's figures under the ATO's headings; optional figures that are blank or 0 are left out. */
function StatementFigures({ statement }: { statement: Statement }) {
  return (
    <Card aria-label="Statement figures">
      <CardHeader>
        <CardTitle>Statement figures</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {STATEMENT_GROUPS.map((group) => {
          const shown = group.fields.filter(({ key, text }) => {
            const value = statement[key as keyof Statement];
            if (value === null) return false; // net pay not stated
            return text
              ? value !== ""
              : key === "net_pay" ||
                  REQUIRED_STATEMENT_FIGURES.includes(key) ||
                  !/^0+(\.0+)?$/.test(value);
          });
          if (shown.length === 0) return null;
          return (
            <section key={group.heading} aria-label={group.heading}>
              <h3 className="mb-2 text-sm font-medium">{group.heading}</h3>
              <dl className="grid gap-4 sm:grid-cols-2">
                {shown.map(({ key, name, text }) => (
                  <Detail key={key} label={name}>
                    {text ? (
                      statement[key as keyof Statement]
                    ) : (
                      <Money amount={statement[key as keyof Statement]} />
                    )}
                  </Detail>
                ))}
              </dl>
            </section>
          );
        })}
      </CardContent>
    </Card>
  );
}

/** Each money figure beside the previous financial year's, with the change. Rows that are 0 in both are left out. */
function ComparedWithLastYear({
  record,
  previous,
}: {
  record: TaxRecord;
  previous: TaxPreviousYear;
}) {
  const names = new Map(STATEMENT_FIGURES);
  const order = STATEMENT_FIGURES.map(([key]) => key);
  const zero = /^0+(\.0+)?$/;
  const rows = previous.changes
    .filter((c) => !zero.test(c.current) || !zero.test(c.previous))
    .sort((a, b) => order.indexOf(a.figure) - order.indexOf(b.figure));
  return (
    <Card aria-label="Compared with last year">
      <CardHeader>
        <CardTitle>Compared with last year</CardTitle>
        <p className="text-sm text-muted-foreground">
          This statement beside{" "}
          <Link
            to="/tax/records/$recordId"
            params={{ recordId: String(previous.record_id) }}
            className="text-primary underline-offset-4 hover:underline"
          >
            {previous.description}
          </Link>{" "}
          (FY {previous.financial_year_label}), the same Member and employer.
          The change is this year minus last year.
        </p>
      </CardHeader>
      <CardContent>
        <Table aria-label={`${record.description} compared with last year`}>
          <TableHeader>
            <TableRow>
              <TableHead className="px-2">Figure</TableHead>
              <TableHead className="px-2 text-right">This year</TableHead>
              <TableHead className="hidden px-2 text-right sm:table-cell">
                Last year
              </TableHead>
              <TableHead className="px-2 text-right">Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => (
              <TableRow key={c.figure} data-testid={`compare-${c.figure}`}>
                <TableCell className="px-2 text-xs sm:text-sm">
                  {names.get(c.figure) ?? c.figure}
                  <p className="text-xs text-muted-foreground sm:hidden">
                    Last year <Money amount={c.previous} />
                  </p>
                </TableCell>
                <TableCell className="px-2 text-right text-xs sm:text-sm">
                  <Money amount={c.current} />
                </TableCell>
                <TableCell className="hidden px-2 text-right text-sm sm:table-cell">
                  <Money amount={c.previous} />
                </TableCell>
                <TableCell className="px-2 text-right text-xs sm:text-sm">
                  <Money amount={c.change} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function RecordDetail({ record }: { record: TaxRecord }) {
  const refresh = useRefreshTax();
  const [deleting, setDeleting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const year = (iso: string) => {
    const [y, m] = iso.split("-").map(Number);
    return m >= 7 ? y + 1 : y;
  };
  const first = year(record.from_on);
  const last = record.to_on ? year(record.to_on) : first;
  // A depreciating asset counts in each year its worksheet has a row for.
  const years = record.asset?.schedule.length
    ? record.asset.schedule.map((row) => row.financial_year)
    : first === last
      ? [first]
      : Array.from({ length: last - first + 1 }, (_, i) => first + i);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink />
        <PageHeader
          title={record.description}
          description={
            <>
              {record.member_name} · {record.label_name}
              {record.return_item && ` (item ${record.return_item})`}
            </>
          }
          action={
            <div className="flex items-center gap-2">
              <TaxRecordSheet
                record={record}
                onSaved={refresh}
                trigger={<Button variant="outline">Edit Tax record</Button>}
              />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    ref={menuRef}
                    variant="outline"
                    aria-label="More actions"
                    className="px-2"
                  >
                    <MoreHorizontal size={16} aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem
                    destructive
                    onSelect={() => setDeleting(true)}
                  >
                    Delete Tax record
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2">
            {record.asset ? (
              <>
                <Detail label="Cost">
                  <Money amount={record.amount} />{" "}
                  {record.work_use_percent &&
                    record.work_use_percent !== "100" && (
                      <Badge variant="info">
                        {record.work_use_percent}% work use
                      </Badge>
                    )}
                </Detail>
                <Detail label="Effective life">
                  {record.asset.effective_life_years} years
                </Detail>
                <Detail label="Method">
                  {record.asset.claim_in_full ? (
                    <Badge variant="info">
                      Claimed in full in the first year
                    </Badge>
                  ) : (
                    "Diminishing value"
                  )}
                </Detail>
                <Detail label="Started">{record.from_on}</Detail>
                <Detail label="Last day used">
                  {record.to_on ?? "Still used"}
                </Detail>
              </>
            ) : record.car ? (
              <>
                <Detail label="Counts toward the total">
                  <Counted record={record} />
                </Detail>
                <Detail label="Trips">{record.car.trips.length}</Detail>
                <Detail label="Dates">{recordDates(record)}</Detail>
              </>
            ) : record.home_office ? (
              <>
                <Detail label="Counts toward the total">
                  <Counted record={record} />
                </Detail>
                <Detail label="Arrangement">
                  {arrangementText(record.home_office)}
                </Detail>
                {record.home_office.hours_per_day != null && (
                  <Detail label="Hours per day">
                    {record.home_office.hours_per_day}
                  </Detail>
                )}
                <Detail label="Dates">{recordDates(record)}</Detail>
              </>
            ) : (
              <>
                <Detail label="Counts toward the total">
                  <Counted record={record} />
                </Detail>
                {record.kind === "money" && record.work_use_percent && (
                  <Detail label="Amount entered">
                    <Money amount={record.amount} />{" "}
                    <Badge variant="info">
                      {record.work_use_percent}% work use
                    </Badge>
                  </Detail>
                )}
                <Detail label="Dates">{recordDates(record)}</Detail>
              </>
            )}
            <Detail label="Financial year">
              {years.map((y, i) => (
                <span key={y}>
                  {i > 0 && ", "}
                  <Link
                    to="/tax/records"
                    search={{ fy: y, label: record.label_key }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    FY {financialYearLabel(y)}
                  </Link>
                </span>
              ))}
            </Detail>
            <Detail label="Type">
              {record.record_type === "line"
                ? "Amount"
                : record.record_type === "fact"
                  ? "Fact"
                  : record.record_type === "depreciating_asset"
                    ? "Depreciating asset"
                    : record.record_type === "home_office_hours"
                      ? "Home office hours"
                      : record.record_type === "car_kilometres"
                        ? "Car kilometres"
                        : "Income statement"}
            </Detail>
            {record.notes && <Detail label="Notes">{record.notes}</Detail>}
          </dl>
        </CardContent>
      </Card>
      {record.statement && <StatementFigures statement={record.statement} />}
      {record.previous_year && (
        <ComparedWithLastYear record={record} previous={record.previous_year} />
      )}
      {record.asset && <AssetSchedule record={record} asset={record.asset} />}
      {record.home_office && (
        <HomeOfficeWorksheet record={record} sheet={record.home_office} />
      )}
      {record.car && <CarSchedule record={record} car={record.car} />}
      <TaxRecordTrace record={record} />
      <LinkedDocuments kind={TAX_RECORD_KIND} id={record.id} />
      <div>
        <Link
          to="/audit"
          search={{ entity_type: "tax_record", entity_id: record.id }}
          className={cn(
            buttonVariants({ variant: "link" }),
            "h-auto max-w-full px-0 text-left whitespace-normal",
          )}
        >
          View audit trail for {record.description}
        </Link>
      </div>
      <DeleteDialog
        key={record.id}
        record={record}
        open={deleting}
        onOpenChange={setDeleting}
        onCloseAutoFocus={(e) => {
          // Opened from a menu item, so hand focus back to the menu button.
          e.preventDefault();
          menuRef.current?.focus();
        }}
      />
    </div>
  );
}

/** `/tax/records/$recordId`: the record's details, with Edit and a confirmed Delete. */
export function TaxRecordDetailPage({ recordId }: { recordId: string }) {
  const id = Number(recordId);
  const valid = Number.isInteger(id) && id > 0;
  const record = useQuery({
    queryKey: ["tax-record", id],
    queryFn: () => fetchTaxRecord(id),
    enabled: valid,
    retry: false,
  });
  if (record.data) return <RecordDetail record={record.data} />;
  if (!valid || record.error) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader
          title="Tax record not found"
          description="There is no Tax record at this address."
        />
        <div>
          <BackLink />
        </div>
      </div>
    );
  }
  return <p className="text-muted-foreground">Loading…</p>;
}

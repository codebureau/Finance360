import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";

import {
  FinancialYearSelect,
  financialYears,
} from "@/components/financial-year-select";
import { inputClass } from "@/components/form-fields";
import { LibraryProblems } from "@/components/library-problems";
import { periodWarningTexts } from "@/components/period-field";
import { LabelTotals } from "@/components/tax-label-totals";
import { TaxRecordSheet } from "@/components/tax-record-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { fetchHousehold } from "@/lib/household";
import {
  factText,
  fetchTaxRecords,
  fetchTaxSetup,
  recordDates,
  shareText,
  type TaxRecord,
  type TaxRecordFilters,
} from "@/lib/tax";

/** Invalidate everything a Tax record change can touch: the register, the totals and the estimate. */
export function useRefreshTax() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["tax-records"] });
    void queryClient.invalidateQueries({ queryKey: ["tax-record"] });
    void queryClient.invalidateQueries({ queryKey: ["tax-label-totals"] });
  };
}

/** What a record counts for: an amount (after any work-use share), a flag or a number of days. */
export function Counted({ record }: { record: TaxRecord }) {
  if (record.asset && record.counted == null) {
    // A depreciating asset counts a different amount each year: with no year chosen, show the cost;
    // with a figure missing, say so rather than showing nothing.
    if (record.asset.missing.length > 0)
      return <Badge variant="flagged">Figure needed</Badge>;
    return (
      <span className="text-muted-foreground">
        Cost <Money amount={record.amount} />
      </span>
    );
  }
  if (record.home_office && record.counted == null) {
    // The worksheet counts in its one year; with the rate missing, say so rather than showing nothing.
    return record.home_office.missing.length > 0 ? (
      <Badge variant="flagged">Figure needed</Badge>
    ) : (
      <Money amount={record.home_office.deduction} />
    );
  }
  if (record.car && record.counted == null) {
    // Counts in its one year; with a figure missing, say so rather than showing nothing.
    return record.car.missing.length > 0 ? (
      <Badge variant="flagged">Figure needed</Badge>
    ) : (
      <Money amount={record.car.deduction} />
    );
  }
  if (record.kind === "money") return <Money amount={record.counted} />;
  return <span>{factText(record)}</span>;
}

function RecordRow({ record }: { record: TaxRecord }) {
  const navigate = useNavigate();
  const share = shareText(record.work_use_percent);
  // A depreciating asset's dates are first use and last use, not a period, so they are not checked.
  const dateWarnings =
    record.record_type === "depreciating_asset"
      ? []
      : periodWarningTexts(record.from_on, record.to_on ?? "");
  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The description is a real link (keyboard, middle-click); let it handle its own click.
        if (!(e.target as HTMLElement).closest("a")) {
          void navigate({
            to: "/tax/records/$recordId",
            params: { recordId: String(record.id) },
          });
        }
      }}
    >
      <TableCell>
        <Link
          to="/tax/records/$recordId"
          params={{ recordId: String(record.id) }}
          className="font-medium hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {record.description}
        </Link>
        <p className="text-xs text-muted-foreground">
          {record.label_name}
          {record.return_item && ` (item ${record.return_item})`}
        </p>
        <p className="text-xs text-muted-foreground sm:hidden">
          {record.member_name} · {recordDates(record)}
        </p>
        {dateWarnings.length > 0 && (
          <p className="mt-0.5 flex flex-col gap-0.5 text-xs">
            <span>
              <Badge variant="flagged">Check dates</Badge>
            </span>
            {dateWarnings.map((text) => (
              <span key={text} className="text-muted-foreground">
                {text}
              </span>
            ))}
          </p>
        )}
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        {record.member_name}
      </TableCell>
      <TableCell className="hidden whitespace-nowrap sm:table-cell">
        {recordDates(record)}
      </TableCell>
      <TableCell className="text-right">
        <Counted record={record} />
        {share && (
          <p className="mt-0.5">
            <Badge variant="info">{share}</Badge>
          </p>
        )}
      </TableCell>
    </TableRow>
  );
}

/**
 * The Tax records register at `/tax/records`: browse by Member, financial year and Return label,
 * search, and drill from a Label total to the records behind it. All the filters are in the URL.
 */
export function TaxRecordsPage({
  filters,
  onFilters,
}: {
  filters: TaxRecordFilters;
  onFilters: (
    patch: Partial<Record<keyof TaxRecordFilters, number | string | undefined>>,
  ) => void;
}) {
  const records = useQuery({
    queryKey: ["tax-records", filters],
    queryFn: () => fetchTaxRecords(filters),
  });
  const household = useQuery({
    queryKey: ["household"],
    queryFn: fetchHousehold,
  });
  const setup = useQuery({ queryKey: ["tax-setup"], queryFn: fetchTaxSetup });
  const refresh = useRefreshTax();
  const members = household.data?.members ?? [];
  const narrowed = Object.keys(filters).length > 0;
  // The box keeps what was typed (a trailing space included); the URL keeps it trimmed.
  const [words, setWords] = useState(filters.q ?? "");
  useEffect(() => {
    setWords((typed) =>
      (filters.q ?? "") === typed.trim() ? typed : (filters.q ?? ""),
    );
  }, [filters.q]);

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <PageHeader
        title="Tax records"
        description="Dated amounts and facts for each Member, against the Return labels. They add up to the totals an estimate uses."
        action={
          <TaxRecordSheet
            defaultMember={filters.member}
            defaultYear={filters.fy}
            onSaved={refresh}
            trigger={
              <Button>
                <Plus size={16} aria-hidden />
                Add Tax record
              </Button>
            }
          />
        }
      />

      <LibraryProblems />

      <form
        role="search"
        aria-label="Filter Tax records"
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(e) => e.preventDefault()}
      >
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Member
          <select
            className={inputClass}
            value={filters.member ?? ""}
            onChange={(e) =>
              onFilters({
                member: e.target.value ? Number(e.target.value) : undefined,
              })
            }
          >
            <option value="">All Members</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Financial year
          <FinancialYearSelect
            value={filters.fy}
            onChange={(fy) => onFilters({ fy })}
            years={financialYears([
              filters.fy,
              ...(setup.data?.years.map((y) => y.financial_year) ?? []),
            ])}
            allLabel="All years"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Return label
          <select
            className={inputClass}
            value={filters.label ?? ""}
            onChange={(e) => onFilters({ label: e.target.value || undefined })}
          >
            <option value="">All labels</option>
            {setup.data?.sections.map((s) => {
              const inSection = setup.data.labels.filter(
                (l) => l.section === s.key,
              );
              return (
                <optgroup key={s.key} label={s.name}>
                  {inSection.map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.name}
                    </option>
                  ))}
                </optgroup>
              );
            })}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Search
          <input
            type="search"
            className={inputClass}
            placeholder="Description, notes, label"
            value={words}
            onChange={(e) => {
              setWords(e.target.value);
              onFilters({ q: e.target.value.trim() || undefined });
            }}
          />
        </label>
      </form>

      {filters.fy && (
        <LabelTotals
          financialYear={filters.fy}
          memberId={filters.member}
          description="Computed from the records below, whose dates overlap the year. Choose a row to see the records behind it."
        />
      )}

      {records.isPending && <p className="text-muted-foreground">Loading…</p>}
      {records.error && (
        <p className="text-destructive">
          Could not load the Tax records: {records.error.message}
        </p>
      )}
      {records.data?.length === 0 && (
        <p className="text-muted-foreground" data-testid="no-records">
          {narrowed
            ? "No Tax record matches these filters."
            : "No Tax records yet. Add one and it counts toward its Return label's total."}
        </p>
      )}
      {records.data && records.data.length > 0 && (
        <Table aria-label="Tax records">
          <TableHeader>
            <TableRow>
              <TableHead>Record</TableHead>
              <TableHead className="hidden sm:table-cell">Member</TableHead>
              <TableHead className="hidden sm:table-cell">Dates</TableHead>
              <TableHead className="text-right">Counts</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {records.data.map((r) => (
              <RecordRow key={r.id} record={r} />
            ))}
          </TableBody>
        </Table>
      )}
      {narrowed && (
        <div>
          <Link
            to="/tax/records"
            search={{}}
            className="text-sm text-primary underline-offset-4 hover:underline"
          >
            Clear filters
          </Link>
        </div>
      )}
    </div>
  );
}

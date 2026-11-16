import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { factText, fetchLabelTotals, financialYearLabel, type TaxLabelTotal } from "@/lib/tax";

function TotalRow({ total, financialYear }: { total: TaxLabelTotal; financialYear: number }) {
  const navigate = useNavigate();
  const search = { fy: financialYear, member: total.member_id, label: total.label_key };
  const count = total.contributions.length;
  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The label is a real link (keyboard, middle-click); let it handle its own click.
        if (!(e.target as HTMLElement).closest("a")) void navigate({ to: "/tax/records", search });
      }}
    >
      <TableCell>
        <Link
          to="/tax/records"
          search={search}
          className="font-medium hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {total.label_name}
        </Link>
        {total.return_item && <span className="ml-2 text-xs text-muted-foreground">item {total.return_item}</span>}
        <p className="text-xs text-muted-foreground sm:hidden">{total.member_name}</p>
      </TableCell>
      <TableCell className="hidden sm:table-cell">{total.member_name}</TableCell>
      <TableCell className="text-right">
        {total.kind === "money" ? <Money amount={total.amount} /> : factText(total)}
      </TableCell>
      <TableCell className="text-right text-muted-foreground">{count}</TableCell>
    </TableRow>
  );
}

/**
 * The Label totals of the Tax records for one financial year, computed now (never stored). Each row
 * drills to the records behind it. `description` says what the totals are used for on this screen.
 */
export function LabelTotals({
  financialYear,
  memberId,
  description,
}: {
  financialYear: number;
  memberId?: number;
  description: ReactNode;
}) {
  const totals = useQuery({
    queryKey: ["tax-label-totals", financialYear, memberId ?? null],
    queryFn: () => fetchLabelTotals(financialYear, memberId),
  });
  return (
    <Card aria-label="Label totals" data-testid="label-totals">
      <CardHeader>
        <CardTitle>Label totals for FY {financialYearLabel(financialYear)}</CardTitle>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent>
        {totals.isPending && <p className="text-muted-foreground">Loading…</p>}
        {totals.error && <p className="text-destructive">Could not load the totals: {totals.error.message}</p>}
        {totals.data && totals.data.uncounted.length > 0 && (
          <div
            role="alert"
            data-testid="uncounted"
            className="mb-4 flex flex-col gap-1 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
          >
            <p className="font-medium">These totals are incomplete</p>
            {totals.data.uncounted.map((u) => (
              <p key={u.record_id}>
                <Link
                  to="/tax/records/$recordId"
                  params={{ recordId: String(u.record_id) }}
                  className="text-primary underline underline-offset-4"
                >
                  {u.description}
                </Link>
                : {u.note}
              </p>
            ))}
          </div>
        )}
        {totals.data && totals.data.totals.length === 0 && totals.data.uncounted.length === 0 && (
          <p className="text-sm text-muted-foreground">No Tax record falls in this financial year yet.</p>
        )}
        {totals.data && totals.data.totals.length > 0 && (
          <Table aria-label="Label totals">
            <TableHeader>
              <TableRow>
                <TableHead>Return label</TableHead>
                <TableHead className="hidden sm:table-cell">Member</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="text-right">Records</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {totals.data.totals.map((t) => (
                <TotalRow key={`${t.member_id}-${t.label_key}`} total={t} financialYear={financialYear} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

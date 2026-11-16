import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RESULT_LINES, type TaxHouseholdResult, type TaxMemberResult } from "@/lib/tax";
import type { components } from "@/api/schema";

type Unmodelled = components["schemas"]["TaxUnmodelledOut"];

/** Result cells are tight on a phone so the Household column stays in view without sideways scroll. */
export const TIGHT = "px-1.5 text-xs sm:px-[var(--cell-px)] sm:text-sm";

export const firstName = (name: string) => name.split(" ")[0];

/** The announced-but-not-yet-law figures an estimate used. */
export function Provisional({ items }: { items: { key: string; note: string }[] }) {
  if (items.length === 0) return null;
  return (
    <div role="status" data-testid="provisional" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
      <p className="font-medium">
        <Badge variant="flagged">Provisional</Badge> This estimate uses figures that are announced but not yet law:
      </p>
      <ul className="mt-1 list-disc pl-5">
        {items.map((p) => (
          <li key={p.key}>
            <span className="font-mono text-xs">{p.key}</span>: {p.note}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Provisional figures, and the return items the engine does not model (they make it incomplete). */
export function EstimateNotices({
  incomplete,
  provisional,
  unmodelled,
}: {
  incomplete: boolean;
  provisional: { key: string; note: string }[];
  unmodelled: Unmodelled[];
}) {
  return (
    <>
      <Provisional items={provisional} />
      {incomplete && (
        <div role="status" data-testid="incomplete" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          <p className="font-medium">
            Incomplete: your Tax records include return items the engine does not model. They are left out of the tax
            below.
          </p>
          <ul className="mt-1 list-disc pl-5">
            {unmodelled.map((u) => (
              <li key={`${u.member_id}-${u.label_key}`}>
                {u.name}: {u.return_item ? `item ${u.return_item}, ` : ""}
                {u.label_name} <Money amount={u.amount} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/** The result lines for each Member and, for a couple, the Household. */
export function ResultTable({
  members,
  household,
}: {
  members: TaxMemberResult[];
  household: TaxHouseholdResult;
}) {
  const couple = members.length > 1;
  return (
    <Table aria-label="Estimate">
      <TableHeader>
        <TableRow>
          <TableHead>
            <span className="sr-only">Item</span>
          </TableHead>
          {members.map((m) => (
            <TableHead key={m.member_id} className="px-1.5 text-right sm:px-[var(--cell-px)]">
              {firstName(m.name)}
            </TableHead>
          ))}
          {couple && <TableHead className="px-1.5 text-right sm:px-[var(--cell-px)]">Household</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {RESULT_LINES.map((line) => (
          <TableRow key={line.key} className={line.key === "net_outcome" ? "font-semibold" : undefined}>
            <TableCell className={`${TIGHT} w-20 sm:w-auto`}>{line.label}</TableCell>
            {members.map((m) => (
              <TableCell key={m.member_id} className={`${TIGHT} text-right`}>
                <Money amount={m[line.key]} />
              </TableCell>
            ))}
            {couple && (
              <TableCell className={`${TIGHT} text-right`}>
                <Money amount={household[line.key]} />
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

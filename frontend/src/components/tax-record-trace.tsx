import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  financialYearLabel,
  type TaxFigureUsed,
  type TaxOperand,
  type TaxRecord,
  type TaxStep,
} from "@/lib/tax";

/** "1482.00" as "1,482.00": text only, so nothing is ever rounded through a float. */
export function groupDigits(value: string): string {
  const [whole, fraction] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? grouped : `${grouped}.${fraction}`;
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** "2026-11-04" as "4 Nov 2026" (read as text, so no time zone can move the day). */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

function Term({ operand }: { operand: TaxOperand }) {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="font-mono text-sm break-words">
        {groupDigits(operand.value)}
        {operand.unit && (
          <span className={operand.unit === "%" ? "" : "ml-1"}>
            {operand.unit}
          </span>
        )}
      </span>
      {operand.label && (
        <span className="text-xs text-muted-foreground">{operand.label}</span>
      )}
    </div>
  );
}

function Operator({ symbol }: { symbol: string }) {
  return (
    <span aria-hidden className="font-mono text-sm text-muted-foreground">
      {symbol}
    </span>
  );
}

export function StepLine({ step }: { step: TaxStep }) {
  return (
    <li className="flex flex-col gap-1.5">
      <p className="text-sm font-medium">{step.title}</p>
      <div className="flex flex-wrap items-start gap-x-2 gap-y-2 rounded-md bg-muted/50 p-3">
        {step.operands.map((operand, i) => (
          <div key={i} className="flex items-start gap-x-2">
            {operand.operator && <Operator symbol={operand.operator} />}
            <Term operand={operand} />
          </div>
        ))}
        <div className="flex items-start gap-x-2">
          <Operator symbol="=" />
          <Term operand={step.result} />
        </div>
      </div>
      {step.note && (
        <p className="text-xs text-muted-foreground">{step.note}</p>
      )}
    </li>
  );
}

export function FigureSource({ figure }: { figure: TaxFigureUsed }) {
  return (
    <li className="flex flex-col gap-1 text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">{figure.name}</span>
        <span className="font-mono">{figure.value}</span>
        <span className="text-muted-foreground">
          FY {figure.financial_year_label}
        </span>
        {figure.provisional && <Badge variant="flagged">Provisional</Badge>}
      </p>
      <p className="text-muted-foreground">
        {figure.url ? (
          <a
            href={figure.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline-offset-4 hover:underline"
          >
            {figure.source}
          </a>
        ) : (
          figure.source
        )}
        {figure.url && !figure.url_confirmed && " (address not confirmed)"}
        {figure.retrieved && `, captured ${shortDate(figure.retrieved)}`}
        {`, Rule pack ${figure.rule_pack_version}`}
      </p>
      {figure.origin === "captured" ? (
        <p className="text-xs text-muted-foreground">
          {figure.captured_at
            ? `Captured here on ${shortDate(figure.captured_at.slice(0, 10))}`
            : "Captured here"}
          {figure.captured_by && ` by the ${figure.captured_by}`}
          {figure.method === "ai" && ". Read by AI"}
          {figure.method === "matched" && ". Matched by words on the page"}
          {figure.method === "carried" &&
            ". Carried from an earlier year, not checked against the page"}
          {figure.quote && `: “${figure.quote}”`}
          {figure.shipped_value &&
            ` The release holds ${figure.shipped_value} for this year.`}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Shipped in {figure.rule_pack_version.split("+")[0].split("/").pop()}
        </p>
      )}
      {figure.provisional_note && (
        <p className="text-xs text-muted-foreground">
          {figure.provisional_note}
        </p>
      )}
    </li>
  );
}

/**
 * "How this is worked out": the record's calculation replayed from exact values, and for each
 * official figure it used, which financial year it is for and where it came from. Read-only, and
 * computed by the server now. Only shown for a record that has a calculation.
 */
export function TaxRecordTrace({ record }: { record: TaxRecord }) {
  const calculation = record.calculation;
  if (!calculation) return null;
  return (
    <Card aria-label="How this is worked out">
      <CardHeader>
        <CardTitle>How this is worked out</CardTitle>
        <p className="text-sm text-muted-foreground">
          The same arithmetic Finance360 uses, step by step, with the official
          figures for the year the record sits in.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {calculation.steps.length > 0 && (
          <ol className="flex flex-col gap-4">
            {calculation.steps.map((step, i) => (
              <StepLine key={i} step={step} />
            ))}
          </ol>
        )}
        {calculation.waiting_for.length > 0 && (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
            <p className="font-medium">Waiting for a figure</p>
            <p>
              The calculation is waiting for{" "}
              {calculation.waiting_for
                .map((w) => `${w.name} (FY ${w.financial_year_label})`)
                .join(", ")}
              . Nothing is guessed, so it is not worked out until it is
              captured.
            </p>
            <p>
              {[
                ...new Set(
                  calculation.waiting_for.map((w) => w.financial_year),
                ),
              ].map((year, i) => (
                <span key={year}>
                  {i > 0 && " "}
                  <Link
                    to="/rule-figures"
                    search={{ fy: year }}
                    className="text-primary underline underline-offset-4"
                  >
                    Capture it on Rule figures (FY {financialYearLabel(year)})
                  </Link>
                  .
                </span>
              ))}
            </p>
          </div>
        )}
        {record.figures_used.length > 0 && (
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">
              Where the figures come from
            </h3>
            <ul className="flex flex-col gap-3">
              {record.figures_used.map((figure) => (
                <FigureSource
                  key={`${figure.key}-${figure.financial_year}`}
                  figure={figure}
                />
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}

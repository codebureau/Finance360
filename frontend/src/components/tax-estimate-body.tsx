import { EstimateNotices } from "@/components/tax-estimate-view";
import { TaxWaterfallCard } from "@/components/tax-waterfall-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { TaxAppliedOverride, TaxEstimateView, TaxPrefill, TaxValue } from "@/lib/tax-estimates";
import { valueWords } from "@/lib/tax-estimates";

/** An amount with `<Money>`, a number of days or a yes or no in words. */
export function ValueText({ value, kind }: { value: TaxValue; kind: string }) {
  const words = valueWords(value, kind);
  return words === null ? <Money amount={value.amount} /> : <>{words}</>;
}

const PREFILL_STATUS: Record<string, string> = {
  counted: "Counted",
  records_win: "Not counted: records used",
  overridden: "Replaced by a what-if",
  no_label: "Not counted",
};

/** The Income stream lines considered for the base: what each adds, or why it is not counted. */
function PrefillCard({ lines }: { lines: TaxPrefill[] }) {
  return (
    <Card data-testid="prefill">
      <CardHeader>
        <CardTitle>Prefilled from Income streams</CardTitle>
        <p className="text-sm text-muted-foreground">
          Where the register has no records for an income and year, the Income stream&apos;s forecast stands in for it.
          Where it has records, they are used and the forecast is not counted. A forecast is never saved as a record,
          and a Tax what-if can state the total instead.
        </p>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {lines.map((l) => (
            <li key={`${l.member_id}-${l.stream_id}`} data-testid="prefill-line">
              <span className="font-medium">{l.member_name.split(" ")[0]}</span>: {l.stream_name}{" "}
              <Badge variant={l.status === "counted" ? "info" : "closed"}>{PREFILL_STATUS[l.status] ?? l.status}</Badge>
              <p className="text-xs text-muted-foreground">
                {l.label_name ? `${l.label_name}. ` : ""}
                {l.status === "counted" && <Money amount={l.value.amount} />} {l.text}
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AppliedOverride({ item }: { item: TaxAppliedOverride }) {
  return (
    <li data-testid="applied-override">
      <p>{item.summary}</p>
      <ul className="mt-0.5 list-disc pl-5 text-xs text-muted-foreground">
        {item.op === "set" && item.value && (
          <li>
            Stated total <Money amount={item.value.amount} />
            {item.replaced ? (
              <>
                , replacing <Money amount={item.replaced.amount} /> that the base counted
              </>
            ) : (
              ", where the records gave none"
            )}
          </li>
        )}
        {item.op === "exclude_label" && (
          <li>
            {item.replaced ? (
              <>
                Left out <Money amount={item.replaced.amount} />
              </>
            ) : (
              "The records gave none for it"
            )}
          </li>
        )}
        {item.contributions.map((c) => (
          <li key={c.label_key}>
            {item.op === "exclude_record" ? "Removes" : "Adds"} {c.label_name}: <Money amount={c.value.amount} />
          </li>
        ))}
      </ul>
    </li>
  );
}

/**
 * An estimate as it is calculated or as it was saved: the result, the Label totals it used with
 * the records behind each, and each override applied. The same for a preview and a saved estimate,
 * because both are built from the same pinned input and provenance.
 */
export function TaxEstimateBody({ view }: { view: TaxEstimateView }) {
  return (
    <div className="flex flex-col gap-4">
      <div data-testid="estimate-result" className="flex flex-col gap-4">
        <EstimateNotices incomplete={view.incomplete} provisional={view.provisional} unmodelled={view.unmodelled} />
        <TaxWaterfallCard
          members={view.members}
          household={view.household}
          incomplete={view.incomplete}
          footnote={
            view.dependants === 0
              ? "No dependent children."
              : `${view.dependants} dependent ${view.dependants === 1 ? "child" : "children"}.`
          }
        />
      </div>

      {view.overrides.length > 0 && (
        <Card data-testid="overrides-applied">
          <CardHeader>
            <CardTitle>Overrides applied</CardTitle>
            <p className="text-sm text-muted-foreground">
              Each is absolute: it replaces what the records gave, it is never a change on top of it.
            </p>
          </CardHeader>
          <CardContent>
            <ol className="list-decimal space-y-2 pl-5 text-sm">
              {view.overrides.map((o, i) => (
                <AppliedOverride key={i} item={o} />
              ))}
            </ol>
          </CardContent>
        </Card>
      )}

      {view.prefill.length > 0 && <PrefillCard lines={view.prefill} />}

      <Card data-testid="totals-used">
        <CardHeader>
          <CardTitle>Label totals used</CardTitle>
          <p className="text-sm text-muted-foreground">
            What the estimate was worked out from, with the records behind each total. A hypothetical record is marked
            and is in no register.
          </p>
        </CardHeader>
        <CardContent>
          <Table aria-label="Label totals used">
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>Label</TableHead>
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {view.totals.map((t) => (
                <TableRow key={`${t.member_id}-${t.label_key}`}>
                  <TableCell className="align-top whitespace-nowrap">{t.member_name.split(" ")[0]}</TableCell>
                  <TableCell className="align-top">
                    {t.label_name}
                    {t.return_item ? <span className="text-muted-foreground"> (item {t.return_item})</span> : null}
                    {t.overridden && (
                      <Badge variant="info" className="ml-2">
                        Stated
                      </Badge>
                    )}
                    {t.source === "prefill" && (
                      <Badge variant="info" className="ml-2">
                        Prefilled from Income streams
                      </Badge>
                    )}
                    {t.contributions.length > 0 && (
                      <ul className="mt-1 text-xs text-muted-foreground">
                        {t.contributions.map((c) => (
                          <li key={c.record_id}>
                            {c.description}: <ValueText value={c.value} kind={t.kind} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </TableCell>
                  <TableCell className="text-right align-top">
                    <ValueText value={t.value} kind={t.kind} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

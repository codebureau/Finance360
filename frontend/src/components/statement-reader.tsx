import { useMutation } from "@tanstack/react-query";
import { FileUp } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { canJumpToPage, DocumentFrame } from "@/components/document-frame";
import { FieldMessage, GeneralError } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  INCOME_STATEMENT_TARGET,
  readDocument,
  type Reading,
} from "@/lib/documents";
import { STATEMENT_GROUPS } from "@/lib/tax";

/** The form's name for a field, from the ATO statement's own headings. */
const NAMES: Record<string, string> = Object.fromEntries(
  STATEMENT_GROUPS.flatMap((g) => g.fields).map((f) => [f.key, f.name]),
);
const nameOf = (key: string) => NAMES[key] ?? key;
const list = (keys: string[]) => keys.map(nameOf).join(", ");

/** The figures a Reading fills into the form: only fields the form has (not the period). */
export function fillsOf(reading: Reading): Record<string, string> {
  return Object.fromEntries(
    reading.found.filter((f) => f.key in NAMES).map((f) => [f.key, f.value]),
  );
}

/** Whether any check of the Reading disagrees: the person must tick before saving. */
export function disagrees(reading: Reading | null): boolean {
  return !!reading?.checks.some((c) => c.status === "disagree");
}

function checkText(check: Reading["checks"][number]): string {
  if (check.kind === "period") {
    return "This statement is for a different financial year from the form's dates.";
  }
  const [total, ...parts] = check.fields;
  return `The parts do not add up to ${nameOf(total).toLowerCase()}: ${list(parts)}.`;
}

/**
 * "Read from the ATO PDF" on the income statement form (ADR-0024). The PDF is read on the server
 * with nothing stored; the figures found are handed to `fill` for the person to check, and the
 * file is held here until the form is saved, when the form files it in the Library and links it to the record.
 */
export function useStatementReader(opts: {
  /** The form's financial year (the year it ends in), to check the statement's period. */
  financialYear: number | null;
  fill: (figures: Record<string, string>) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  // The chosen PDF: always shown beside the form (even when nothing could be read from it), so the
  // figures can be copied from it. Only a statement the reader recognised is filed on save.
  const [shown, setShown] = useState<File | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [filled, setFilled] = useState<Record<string, "read" | "absent">>({});
  const [checked, setChecked] = useState(false);
  // The page of the figure last asked about, and a count so asking again re-opens it.
  const [page, setPage] = useState<number | null>(null);
  const [jump, setJump] = useState(0);
  const pages = useMemo(
    () => Object.fromEntries((reading?.found ?? []).map((f) => [f.key, f.page])),
    [reading],
  );
  // The source rows of a figure that adds up repeated rows, for the trace under the field.
  const rowsOf = useMemo(
    () =>
      Object.fromEntries(
        (reading?.found ?? [])
          .filter((f) => f.kind === "money" && f.parts.length > 1)
          .map((f) => [f.key, f.parts]),
      ),
    [reading],
  );
  // The file as the browser can show it, before anything is stored (ADR-0024): an object URL that
  // lives only as long as the file is held here.
  const preview = useMemo(() => (shown ? URL.createObjectURL(shown) : null), [shown]);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  const read = useMutation({
    mutationFn: (chosen: File) =>
      readDocument(chosen, INCOME_STATEMENT_TARGET, opts.financialYear),
    onSuccess: (result, chosen) => {
      setReading(result);
      setChecked(false);
      setPage(null);
      setShown(chosen);
      // A claimed statement (even one with a required figure missing) is the record's evidence;
      // a PDF nothing recognised is only shown, not filed.
      setFile(result.outcome === "unrecognised" ? null : chosen);
      setFilled(
        Object.fromEntries(
          result.found
            .filter((f) => f.key in NAMES)
            .map((f) => [f.key, f.how === "absent" ? "absent" : "read"]),
        ),
      );
      if (result.found.length > 0) opts.fill(fillsOf(result));
    },
  });

  const needsTick = disagrees(reading) && !!shown;

  const missing = reading?.not_found.filter((n) => n.key in NAMES) ?? [];
  const requiredMissing = missing.filter((n) => n.required).map((n) => n.key);
  const readCount = Object.keys(filled).length;

  const panel: ReactNode = (
    <section
      aria-label="Read from the ATO PDF"
      className="flex flex-col gap-2 rounded-md border p-3"
    >
      <p className="text-sm font-medium">Read from the ATO PDF</p>
      <p className="text-xs text-muted-foreground">
        Print the income statement page from the ATO website to PDF, then choose
        it here. The figures are read on this server and filled in below for
        you to check. Nothing is sent anywhere or stored until you save; a PDF
        that was read is then kept in the Library, linked to the record.
      </p>
      <div>
        <Button
          type="button"
          variant="outline"
          disabled={read.isPending}
          onClick={() => input.current?.click()}
        >
          <FileUp size={16} aria-hidden />
          {read.isPending
            ? "Reading…"
            : shown
              ? "Read a different PDF"
              : "Choose the PDF"}
        </Button>
        <input
          ref={input}
          type="file"
          className="hidden"
          aria-label="Choose the ATO income statement PDF"
          accept=".pdf,application/pdf"
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            e.target.value = ""; // so choosing the same file again still fires
            if (chosen) read.mutate(chosen);
          }}
        />
      </div>
      <FieldMessage error={read.error} field="file" />
      <GeneralError error={read.error} />
      {reading?.outcome === "unrecognised" && (
        <p role="status" className="text-sm">
          This does not look like the ATO income statement, so nothing was
          filled in. The PDF is beside the form: copy the figures from it.
        </p>
      )}
      {reading?.outcome === "needs_repair" && (
        <p role="status" className="text-sm">
          Some figures could not be read: copy them from the statement beside
          the form.
          {requiredMissing.length > 0 &&
            ` Still to enter: ${list(requiredMissing)}.`}
        </p>
      )}
      {shown && reading && reading.outcome !== "unrecognised" && (
        <div role="status" className="flex flex-col gap-1 text-sm">
          <p>
            Read {readCount} {readCount === 1 ? "figure" : "figures"} from the
            PDF. Check them against the statement before saving. Allowances,
            net pay and the reconciliation figures are not read: enter them
            yourself.
          </p>
          {reading.checks
            .filter((c) => c.status === "disagree")
            .map((c) => (
              <p key={c.kind} className="text-warning" data-testid="reading-warning">
                {checkText(c)}
              </p>
            ))}
          {reading.checks
            .filter((c) => c.status === "skipped")
            .map((c) => (
              <p key={`${c.kind}-skipped`} className="text-muted-foreground">
                The parts could not be checked against the total: a figure is
                missing.
              </p>
            ))}
          {reading.checks
            .filter((c) => c.status === "agree" && c.assumed_zero.length > 0)
            .map((c) => (
              <p key={`${c.kind}-zero`} className="text-muted-foreground">
                Not on the statement, counted as 0 in the check:{" "}
                {list(c.assumed_zero)}.
              </p>
            ))}
          {missing.length > 0 && (
            <p className="text-muted-foreground">
              Not found on the statement, left blank:{" "}
              {list(missing.map((n) => n.key))}.
            </p>
          )}
        </div>
      )}
      {needsTick && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
          <span>I have checked these figures against the statement.</span>
        </label>
      )}
    </section>
  );

  return {
    panel,
    /** The PDF to file in the Library and link on save, once the figures have been read from it. */
    file,
    /**
     * The PDF being filled from, for the pane beside the form (ADR-0026 section 5): the chosen
     * file's object URL, so nothing is stored until Save; `null` until a PDF is chosen. It shows
     * for every PDF, including one nothing could be read from.
     */
    documentPane: preview ? (
      <DocumentFrame
        src={preview}
        title="The ATO income statement PDF"
        page={page}
        jump={jump}
      />
    ) : null,
    /** Changes each time a page label is clicked, so the layout can show the Document tab. */
    jump,
    /** True while a sum or period warning is not yet ticked. */
    blocksSave: needsTick && !checked,
    /** The note under a field filled from the PDF; `null` for any other field. */
    mark(key: string): ReactNode {
      const how = filled[key];
      if (!how) return null;
      if (how === "absent") {
        return <Badge variant="flagged">Not on the statement: taken as 0</Badge>;
      }
      const from = pages[key] ?? 0;
      const rows = rowsOf[key];
      return (
        <span className="flex flex-wrap items-center gap-2">
          {rows && (
            <span className="text-xs text-muted-foreground">
              Added up from {rows.length} rows on the statement:{" "}
              {rows.map((p) => p.value).join(" + ")}
            </span>
          )}
          <Badge variant="info">Read from the PDF</Badge>
          {from > 0 &&
            (canJumpToPage() ? (
              <Button
                type="button"
                variant="link"
                className="text-xs underline"
                aria-label={`Show page ${from} of the PDF`}
                onClick={() => {
                  setPage(from);
                  setJump((n) => n + 1);
                }}
              >
                page {from}
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">page {from}</span>
            ))}
        </span>
      );
    },
    /** A field was typed over: it is no longer "read". */
    edited(key: string) {
      setFilled((was) => {
        if (!(key in was)) return was;
        const rest = { ...was };
        delete rest[key];
        return rest;
      });
    },
  };
}

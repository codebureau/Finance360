import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import {
  FieldMessage,
  GeneralError,
  inputClass,
} from "@/components/form-fields";
import { ScheduledChange } from "@/components/rule-figures-carry";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  fetchAiStatus,
  readerPausedText,
  RULE_CAPTURE_STATUS_KEY,
} from "@/lib/ask";
import { ApiError } from "@/lib/household";
import {
  captureRuleFigure,
  correctRuleFigure,
  figureText,
  groupFigures,
  isCarried,
  PAGE_TEXT_MAX,
  proposeRuleFigures,
  todayIso,
  type RuleFigure,
  type RuleFigureInput,
  type RuleFigureProposal,
  type RuleFigureReading,
} from "@/lib/rule-figures";
import { financialYearLabel } from "@/lib/tax";

/** What a row of the results has been through since the page was read. */
type RowState = {
  saving: boolean;
  done: string | null;
  error: unknown;
  /** The person has seen last year's figure and says a large change is right. */
  jumpOk: boolean;
  /** The figure typed into a "not found" row. */
  typed: string;
};

const BLANK: RowState = {
  saving: false,
  done: null,
  error: null,
  jumpOk: false,
  typed: "",
};

const CONFIDENCE_BADGE = {
  high: "ok",
  medium: "info",
  low: "flagged",
} as const;

/** Who found a figure, in the words the screen and the trace card use (ADR-0023 section 6). */
export const ORIGIN_LABEL = {
  ai: "Read by AI",
  matched: "Matched by words on the page",
} as const;

/**
 * The pages the figures still to supply live on, each with the figures it covers, in the screen's
 * order. A figure is still to supply when it is missing or carried from an earlier year and not yet
 * checked against the page.
 */
function pagesOf(figures: RuleFigure[]) {
  const wanted = figures.filter((f) => f.state === "missing" || isCarried(f));
  const pages = new Map<string, RuleFigure[]>();
  for (const figure of wanted) {
    pages.set(figure.page_url, [...(pages.get(figure.page_url) ?? []), figure]);
  }
  return [...pages.entries()].map(([url, group]) => {
    const titles = [...new Set(groupFigures(group).map((g) => g.title))];
    const missing = group.filter((f) => f.state === "missing").length;
    const carried = group.length - missing;
    const parts = [
      missing > 0 && `${missing} missing`,
      carried > 0 && `${carried} carried`,
    ].filter(Boolean);
    return {
      url,
      figures: group,
      label: `${titles.join(", ")}: ${parts.join(", ")} figure${group.length === 1 ? "" : "s"}`,
    };
  });
}

/**
 * The carried figure a page reading disagrees with, or `null`: the reading agrees, or the figure
 * was not carried. A figure that changed from the carried one needs the same explicit tick as a
 * large change before it can be confirmed (ADR-0022 section 4).
 */
function changedFromCarried(
  figure: RuleFigure | undefined,
  proposal: RuleFigureProposal,
): string | null {
  return figure && isCarried(figure) && figure.value !== proposal.value
    ? figure.value
    : null;
}

function Candidate({
  proposal,
  method,
  figure,
  row,
  onJump,
  onConfirm,
}: {
  proposal: RuleFigureProposal;
  method: RuleFigureReading["method"];
  figure: RuleFigure | undefined;
  row: RowState;
  onJump: (ok: boolean) => void;
  onConfirm: () => void;
}) {
  const shown = figure
    ? figureText(figure, proposal.value ?? "")
    : (proposal.value ?? "");
  const carried = figure != null && isCarried(figure);
  const changed = changedFromCarried(figure, proposal);
  const needsTick = !!proposal.jump || changed != null;
  return (
    <li
      data-testid={`proposal-${proposal.key}`}
      className="flex flex-col gap-2 rounded-md border p-3 text-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="font-medium">{proposal.label}</p>
          <p className="font-mono break-all">{shown}</p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge variant={method === "ai" ? "info" : "closed"}>
            {ORIGIN_LABEL[method]}
          </Badge>
          {proposal.confidence && (
            <Badge variant={CONFIDENCE_BADGE[proposal.confidence]}>
              {proposal.confidence[0].toUpperCase() +
                proposal.confidence.slice(1)}{" "}
              confidence
            </Badge>
          )}
          {proposal.provisional_note && (
            <Badge variant="flagged">Provisional</Badge>
          )}
        </div>
      </div>
      <blockquote className="border-l-2 pl-3 text-muted-foreground">
        “{proposal.quote}”
      </blockquote>
      {proposal.note && (
        <p className="text-xs text-muted-foreground">{proposal.note}</p>
      )}
      {carried && !changed && !row.done && (
        <p className="text-xs text-muted-foreground">
          The page agrees with the carried figure.
        </p>
      )}
      {figure?.scheduled_change && carried && !row.done && (
        <ScheduledChange text={figure.scheduled_change} />
      )}
      {needsTick && !row.done && (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3"
        >
          <p className="font-medium">
            {proposal.jump
              ? "This is a large change"
              : "This differs from the carried figure"}
          </p>
          <p>
            {changed != null && figure
              ? `Carried: ${figureText(figure, changed)}; the page says ${shown}. `
              : figure?.previous
                ? `FY ${figure.previous.financial_year_label} holds ${figureText(figure, figure.previous.value)}; the page says ${shown}. `
                : ""}
            {proposal.jump}
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={row.jumpOk}
              onChange={(e) => onJump(e.target.checked)}
            />
            Yes, this is right
          </label>
        </div>
      )}
      <GeneralError error={row.error} />
      <FieldMessage error={row.error} field="value" />
      <FieldMessage error={row.error} field="quote" />
      <div className="flex justify-end">
        {row.done ? (
          <Badge variant="ok">{row.done}</Badge>
        ) : (
          <Button
            variant="outline"
            aria-label={`Confirm ${proposal.label}`}
            disabled={row.saving || (needsTick && !row.jumpOk)}
            onClick={onConfirm}
          >
            Confirm
          </Button>
        )}
      </div>
    </li>
  );
}

function NotFound({
  proposal,
  figure,
  row,
  onTyped,
  onJump,
  onSave,
}: {
  proposal: RuleFigureProposal;
  figure: RuleFigure | undefined;
  row: RowState;
  onTyped: (value: string) => void;
  onJump: (ok: boolean) => void;
  onSave: () => void;
}) {
  const jump =
    row.error instanceof ApiError ? row.error.fields.confirm_jump : undefined;
  return (
    <li
      data-testid={`proposal-${proposal.key}`}
      className="flex flex-col gap-2 rounded-md border p-3 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{proposal.label}</p>
        <Badge variant="closed">Not found; enter it</Badge>
      </div>
      {proposal.reason && (
        <p className="text-xs text-muted-foreground">{proposal.reason}</p>
      )}
      {row.done ? (
        <Badge variant="ok" className="self-start">
          {row.done}
        </Badge>
      ) : (
        <>
          <label className="flex flex-col gap-1">
            <span className="sr-only">Enter {proposal.label}</span>
            <input
              className={inputClass}
              inputMode="decimal"
              autoComplete="off"
              aria-label={`Enter ${proposal.label}`}
              placeholder={figure?.entry_hint}
              value={row.typed}
              onChange={(e) => onTyped(e.target.value)}
            />
            {figure && (
              <span className="text-xs text-muted-foreground">
                Enter {figure.entry_hint}. Plausible range:{" "}
                {figure.plausible_range}.
              </span>
            )}
          </label>
          <FieldMessage error={row.error} field="value" />
          {jump && (
            <div
              role="alert"
              className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3"
            >
              <p className="font-medium">This is a large change</p>
              <p>{jump}</p>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={row.jumpOk}
                  onChange={(e) => onJump(e.target.checked)}
                />
                Yes, this is right
              </label>
            </div>
          )}
          <GeneralError error={row.error} />
          <div className="flex justify-end">
            <Button
              variant="outline"
              aria-label={`Capture ${proposal.label}`}
              disabled={
                row.saving || row.typed.trim() === "" || (!!jump && !row.jumpOk)
              }
              onClick={onSave}
            >
              Capture figure
            </Button>
          </div>
        </>
      )}
    </li>
  );
}

/**
 * "Read the page for me" (ADR-0022 section 5, ADR-0023 section 6): paste the official page's text and
 * each missing figure is proposed with the sentence it came from, by a model when one is set up and
 * may be used, otherwise by matching words (no AI, nothing leaves the server). Deterministic checks
 * gate every proposal, and nothing is stored until the person presses Confirm. The pasted text and
 * the results live here, not in the Sheet, so closing the Sheet or a failed read never loses them.
 * The button is never greyed out for want of AI; typed entry on each row is unchanged.
 */
export function RuleFigureReader({
  figures,
  year,
  onCaptured,
}: {
  figures: RuleFigure[];
  year: number;
  /** A figure was stored: refresh whatever shows it. */
  onCaptured: (message: string) => void;
}) {
  const status = useQuery({
    queryKey: RULE_CAPTURE_STATUS_KEY,
    queryFn: () => fetchAiStatus("rule_capture"),
    staleTime: 0,
    refetchOnMount: "always",
  });
  const [open, setOpen] = useState(false);
  const [pageUrl, setPageUrl] = useState("");
  const [text, setText] = useState("");
  const [reading, setReading] = useState<RuleFigureReading | null>(null);
  const results = reading?.proposals ?? null;
  const [rows, setRows] = useState<Record<string, RowState>>({});

  const pages = pagesOf(figures);
  const page = pages.find((p) => p.url === pageUrl) ?? pages[0];
  const row = (key: string): RowState => rows[key] ?? BLANK;
  const patch = (key: string, change: Partial<RowState>) =>
    setRows((all) => ({
      ...all,
      [key]: { ...(all[key] ?? BLANK), ...change },
    }));
  const figureOf = (key: string) => figures.find((f) => f.key === key);

  const read = useMutation({
    mutationFn: () =>
      proposeRuleFigures(
        year,
        (page?.figures ?? []).map((f) => f.key),
        text,
      ),
    onSuccess: (read) => {
      setReading(read);
      setRows({});
    },
  });

  // Reading never needs AI. When a model is set up but paused (capped or failing), say why the page
  // is read by matching words instead.
  const paused = status.data ? readerPausedText(status.data) : null;
  const byAi = status.data?.enabled === true;
  const nothingMissing = figures.length > 0 && pages.length === 0;
  const tooLong = text.trim().length > PAGE_TEXT_MAX;

  /** Store one figure through the same write as typed entry; true when it was stored. */
  async function save(
    proposal: RuleFigureProposal,
    input: RuleFigureInput,
  ): Promise<boolean> {
    const figure = figureOf(proposal.key);
    patch(proposal.key, { saving: true, error: null });
    try {
      const body = {
        page_url: figure?.page_url ?? null,
        retrieved_on: todayIso(),
        ...input,
      };
      // A carried figure already holds the year's one active row: the page's figure supersedes
      // it through the same correction as the row's menu (ADR-0022 section 4).
      const carried = figure?.capture != null && isCarried(figure);
      if (carried && figure?.capture) {
        await correctRuleFigure(figure.capture.id, body);
      } else {
        await captureRuleFigure(year, proposal.key, body);
      }
      const verb = carried ? "Confirmed" : "Captured";
      patch(proposal.key, { saving: false, done: verb });
      onCaptured(
        `${verb} ${proposal.label} for FY ${financialYearLabel(year)}.`,
      );
      return true;
    } catch (error) {
      patch(proposal.key, { saving: false, error });
      return false;
    }
  }

  const confirm = (proposal: RuleFigureProposal) =>
    save(proposal, {
      value: proposal.value ?? "",
      quote: proposal.quote,
      provisional_note: proposal.provisional_note,
      confirm_jump: row(proposal.key).jumpOk,
      // A model's proposal goes back with its log entry; the matcher's has none (it is not an AI call).
      ...(reading?.method === "matched"
        ? { matched_proposal: proposal.proposal }
        : { ai_log_id: proposal.ai_log_id, ai_proposal: proposal.proposal }),
    });

  const candidates = (results ?? []).filter((p) => p.status === "candidate");
  const waiting = candidates.filter((p) => !row(p.key).done);
  const blockedByJump = waiting.some(
    (p) =>
      (p.jump || changedFromCarried(figureOf(p.key), p)) && !row(p.key).jumpOk,
  );
  const absent = (results ?? []).filter(
    (p) => p.status === "not_found" && !p.reason,
  );

  async function confirmAll() {
    for (const proposal of waiting) {
      if (!(await confirm(proposal))) break; // the row shows why; the rest wait
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button disabled={pages.length === 0} onClick={() => setOpen(true)}>
        Read the page for me
      </Button>
      {paused && (
        <p
          className="max-w-xs text-right text-xs text-muted-foreground"
          data-testid="reader-paused"
        >
          {paused}
        </p>
      )}
      {nothingMissing && (
        <p className="max-w-xs text-right text-xs text-muted-foreground">
          Nothing is missing for FY {financialYearLabel(year)}.
        </p>
      )}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="sm:max-w-xl">
          <SheetTitle className="text-lg font-semibold">
            Read the page for me
          </SheetTitle>
          <SheetDescription className="text-sm text-muted-foreground">
            For FY {financialYearLabel(year)}. Paste the text of the official
            page and{" "}
            {byAi
              ? "AI will find each missing figure"
              : "Finance360 will find each missing figure by matching words on it"}{" "}
            and show the sentence it came from. Nothing is saved until you
            confirm a figure.
          </SheetDescription>
          <div className="flex flex-col gap-4 overflow-y-auto pb-2">
            {pages.length > 1 && (
              <label className="flex flex-col gap-1 text-sm">
                Which page did you copy?
                <select
                  className={inputClass}
                  value={page?.url ?? ""}
                  onChange={(e) => {
                    setPageUrl(e.target.value);
                    setReading(null);
                  }}
                >
                  {pages.map((p) => (
                    <option key={p.url} value={p.url}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {page && (
              <p className="text-sm">
                <a
                  href={page.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline underline-offset-4"
                >
                  Open the official page
                </a>
                <span className="mt-1 block text-xs text-muted-foreground">
                  Looking for: {page.figures.map((f) => f.label).join("; ")}.
                </span>
              </p>
            )}
            <label className="flex flex-col gap-1 text-sm">
              Page text
              <textarea
                className={`${inputClass} py-2 font-mono`}
                rows={8}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <span
                className={
                  tooLong
                    ? "text-xs text-destructive"
                    : "text-xs text-muted-foreground"
                }
              >
                {text.trim().length.toLocaleString("en-AU")} of{" "}
                {PAGE_TEXT_MAX.toLocaleString("en-AU")} characters.
                {tooLong &&
                  " Paste only the part of the page with the figures."}
              </span>
            </label>
            {byAi ? (
              <p className="text-xs text-muted-foreground">
                Names and e-mail addresses are removed before the text is sent;
                the figures are kept so they can be read. You can see exactly
                what was sent in the{" "}
                <Link
                  to="/settings/ai-log"
                  className="text-primary underline underline-offset-4"
                >
                  AI log
                </Link>
                .
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                The page is read on this server by matching words. Nothing is
                sent anywhere.
              </p>
            )}
            {read.error && (
              <div
                role="alert"
                data-testid="reader-error"
                className="flex flex-col gap-1 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
              >
                <p>{read.error.message}</p>
                <p className="text-muted-foreground">
                  Your pasted text is still here. You can type each figure from
                  its row&apos;s menu on the Rule figures screen.
                </p>
              </div>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <SheetClose asChild>
                <Button variant="ghost">Cancel</Button>
              </SheetClose>
              <Button
                variant={reading ? "outline" : "default"}
                disabled={
                  read.isPending || tooLong || text.trim() === "" || !page
                }
                onClick={() => read.mutate()}
              >
                {read.isPending
                  ? "Reading…"
                  : reading
                    ? "Read again"
                    : "Read the page"}
              </Button>
            </div>

            {results && (
              <section
                aria-label="What the page says"
                className="flex flex-col gap-3"
              >
                {reading?.fallback && (
                  <p
                    className="text-sm text-muted-foreground"
                    data-testid="reader-fallback"
                  >
                    {reading.fallback}
                  </p>
                )}
                <p role="status" className="text-sm font-medium">
                  {reading ? `${ORIGIN_LABEL[reading.method]}: found` : "Found"}{" "}
                  {candidates.length} of {results.length} figures. Check each
                  sentence against the page, then confirm.
                </p>
                <ul className="flex flex-col gap-3">
                  {results.map((p) => {
                    if (p.status === "candidate")
                      return (
                        <Candidate
                          key={p.key}
                          proposal={p}
                          method={reading?.method ?? "matched"}
                          figure={figureOf(p.key)}
                          row={row(p.key)}
                          onJump={(jumpOk) => patch(p.key, { jumpOk })}
                          onConfirm={() => void confirm(p)}
                        />
                      );
                    if (!p.reason) return null;
                    return (
                      <NotFound
                        key={p.key}
                        proposal={p}
                        figure={figureOf(p.key)}
                        row={row(p.key)}
                        onTyped={(typed) => patch(p.key, { typed })}
                        onJump={(jumpOk) => patch(p.key, { jumpOk })}
                        onSave={() =>
                          void save(p, {
                            value: row(p.key).typed.trim(),
                            confirm_jump: row(p.key).jumpOk,
                          })
                        }
                      />
                    );
                  })}
                </ul>
                {absent.length > 0 && (
                  <p
                    className="text-sm text-muted-foreground"
                    data-testid="not-on-page"
                  >
                    Not on the page you pasted:{" "}
                    {absent.map((p) => p.label).join("; ")}. Type each from its
                    row&apos;s menu on the Rule figures screen.
                  </p>
                )}
                {waiting.length > 0 && (
                  <div className="flex justify-end">
                    <Button
                      disabled={blockedByJump}
                      onClick={() => void confirmAll()}
                    >
                      Confirm all ({waiting.length})
                    </Button>
                  </div>
                )}
              </section>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

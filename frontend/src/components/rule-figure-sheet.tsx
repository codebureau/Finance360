import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import {
  FieldMessage,
  GeneralError,
  inputClass,
} from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { ApiError } from "@/lib/household";
import {
  captureRuleFigure,
  correctRuleFigure,
  figureText,
  todayIso,
  type RuleFigure,
  type RuleFigureInput,
} from "@/lib/rule-figures";

function blankToNull(text: string): string | null {
  return text.trim() === "" ? null : text.trim();
}

function FigureForm({
  figure,
  onDone,
}: {
  figure: RuleFigure;
  onDone: (message: string) => void;
}) {
  const capture = figure.capture;
  const correcting = capture != null;
  const [value, setValue] = useState(correcting ? (figure.value ?? "") : "");
  const [quote, setQuote] = useState(capture?.quote ?? "");
  const [pageUrl, setPageUrl] = useState(capture?.page_url ?? figure.page_url);
  // A carried figure's note ("Same as ..., not checked") is not the page's: correcting it against
  // the page makes it firm unless the page itself says it is announced.
  const carried = capture?.method === "carried";
  const [provisional, setProvisional] = useState(
    !!capture?.provisional_note && !carried,
  );
  const [note, setNote] = useState(carried ? "" : (capture?.provisional_note ?? ""));
  const [retrievedOn, setRetrievedOn] = useState(todayIso());
  const [confirmJump, setConfirmJump] = useState(false);

  const save = useMutation({
    mutationFn: () => {
      const input: RuleFigureInput = {
        value: value.trim(),
        quote: blankToNull(quote),
        page_url: blankToNull(pageUrl),
        provisional_note: provisional ? blankToNull(note) : null,
        retrieved_on: retrievedOn || null,
        confirm_jump: confirmJump,
      };
      return capture
        ? correctRuleFigure(capture.id, input)
        : captureRuleFigure(figure.financial_year, figure.key, input);
    },
    onSuccess: () => onDone(correcting ? "Corrected" : "Captured"),
  });

  // The server flags a large change from last year; the person must say it is right, then resubmit.
  const jump =
    save.error instanceof ApiError ? save.error.fields.confirm_jump : undefined;
  const dirtyProps = useDirtyFormProps();

  return (
    <form
      {...dirtyProps}
      aria-label={`${correcting ? "Correct" : "Capture"} ${figure.label}`}
      className="flex flex-col gap-4 overflow-y-auto pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm">
        <a
          href={figure.page_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline underline-offset-4"
        >
          Open the official page
        </a>
        {!figure.url_confirmed && (
          <span className="text-muted-foreground">
            {" "}
            (address not confirmed)
          </span>
        )}
        <span className="mt-1 block text-xs text-muted-foreground">
          {figure.where}
        </span>
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Value
        <input
          className={inputClass}
          inputMode="decimal"
          autoComplete="off"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setConfirmJump(false); // a changed value needs its own confirmation
          }}
        />
        <span className="text-xs text-muted-foreground">
          Enter {figure.entry_hint}. Plausible range: {figure.plausible_range}.
        </span>
        <FieldMessage error={save.error} field="value" />
      </label>
      {figure.previous && (
        <div className="-mt-2 flex flex-wrap items-center gap-2 text-sm">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setValue(figure.previous?.value ?? "");
              setConfirmJump(false);
            }}
          >
            Use FY {figure.previous.financial_year_label}&apos;s figure
          </Button>
          <span className="text-xs text-muted-foreground">
            Fills the value with {figureText(figure, figure.previous.value)}.
            Check it against the page before you save.
          </span>
        </div>
      )}
      {jump && (
        <div
          role="alert"
          data-testid="jump-check"
          className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm"
        >
          <p className="font-medium">This is a large change</p>
          <p>
            {figure.previous
              ? `FY ${figure.previous.financial_year_label} holds ${figureText(figure, figure.previous.value)}; you entered ${value.trim()}. `
              : ""}
            {jump}
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={confirmJump}
              onChange={(e) => setConfirmJump(e.target.checked)}
            />
            Yes, this is right
          </label>
        </div>
      )}
      <label className="flex flex-col gap-1 text-sm">
        Quote (optional)
        <textarea
          className={`${inputClass} py-2`}
          rows={3}
          value={quote}
          onChange={(e) => setQuote(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          The sentence on the page that states it.
        </span>
        <FieldMessage error={save.error} field="quote" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Page address
        <input
          className={inputClass}
          type="url"
          inputMode="url"
          value={pageUrl}
          onChange={(e) => setPageUrl(e.target.value)}
        />
        <FieldMessage error={save.error} field="page_url" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Retrieved on
        <input
          className={inputClass}
          type="date"
          value={retrievedOn}
          onChange={(e) => setRetrievedOn(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          The day you read the page.
        </span>
        <FieldMessage error={save.error} field="retrieved_on" />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={provisional}
          onChange={(e) => setProvisional(e.target.checked)}
        />
        This is announced but not yet law
      </label>
      {provisional && (
        <label className="flex flex-col gap-1 text-sm">
          Provisional note
          <input
            className={inputClass}
            value={note}
            placeholder="What was announced, and where"
            onChange={(e) => setNote(e.target.value)}
          />
          <FieldMessage error={save.error} field="provisional_note" />
        </label>
      )}
      <FieldMessage error={save.error} field="key" />
      <FieldMessage error={save.error} field="fy" />
      <FieldMessage error={save.error} field="id" />
      <GeneralError error={save.error} />
      <div className="flex justify-end gap-2">
        <SheetClose asChild>
          <Button variant="ghost">Cancel</Button>
        </SheetClose>
        <Button
          type="submit"
          disabled={save.isPending || (!!jump && !confirmJump)}
        >
          {correcting ? "Save correction" : "Capture figure"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Capture (a shipped or missing figure) or correct (a captured one) in a Sheet. The form mounts only
 * while open, so each opening starts from the figure as it is now. Clicking outside never closes it.
 */
export function RuleFigureSheet({
  figure,
  open,
  onOpenChange,
  onDone,
}: {
  figure: RuleFigure;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: (message: string) => void;
}) {
  const correcting = figure.capture != null;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">
          {correcting ? "Correct" : "Capture"} {figure.label}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          For FY {figure.financial_year_label}.{" "}
          {figure.capture?.method === "carried"
            ? "This figure was carried from an earlier year and has not been checked against the page. Saving replaces it with the figure you enter; the carried one stays in the history."
            : correcting
              ? "The corrected figure replaces the capture; the original stays in the history."
            : "Read it from the official page and type it here. It applies to this financial year only."}
        </SheetDescription>
        <FigureForm figure={figure} onDone={onDone} />
      </SheetContent>
    </Sheet>
  );
}

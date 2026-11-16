import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useExpandSheet } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

/**
 * The generic pre-fill layout (ADR-0026 section 5), shared by every Fill target: the form and the
 * document it is being filled from, side by side.
 *
 * With no `document` it is just the form. With one, the Sheet opens full width and the two panes
 * sit side by side, each scrolling on its own. The split follows the width of the space the form
 * has (a container query), not the window, so a collapsed Sheet falls back to the phone layout:
 * two tabs, **Form** then **Document**. Both panes stay mounted and the inactive one is only
 * hidden, so nothing typed is lost by switching.
 *
 * `jump` changes when the person asks to see a page: the Document tab is then shown.
 */
export function PrefillLayout({
  form,
  document,
  jump,
}: {
  form: ReactNode;
  /** The document pane's content, or `null` when there is nothing to show beside the form. */
  document: ReactNode | null;
  jump?: number;
}) {
  const [tab, setTab] = useState<"form" | "document">("form");
  const has = document !== null;
  useExpandSheet(has);
  useEffect(() => {
    if (jump) setTab("document");
  }, [jump]);

  // Not an early return: the form keeps its place in the tree when a document appears beside it,
  // so it is never remounted (it keeps focus and every control's state).
  const showForm = !has || tab === "form";
  const tabClass = (active: boolean) =>
    cn(
      "-mb-px rounded-none border-b-2",
      active
        ? "border-primary text-foreground"
        : "border-transparent text-muted-foreground hover:text-foreground",
    );

  return (
    <div className="@container flex min-h-0 flex-1 flex-col gap-3">
      {has && (
        <div
          role="tablist"
          aria-label="Form and document"
          className="flex border-b @2xl:hidden"
        >
          <Button
            type="button"
            variant="ghost"
            role="tab"
            id="prefill-tab-form"
            aria-selected={tab === "form"}
            aria-controls="prefill-panel-form"
            className={tabClass(tab === "form")}
            onClick={() => setTab("form")}
          >
            Form
          </Button>
          <Button
            type="button"
            variant="ghost"
            role="tab"
            id="prefill-tab-document"
            aria-selected={tab === "document"}
            aria-controls="prefill-panel-document"
            className={tabClass(tab === "document")}
            onClick={() => setTab("document")}
          >
            Document
          </Button>
        </div>
      )}
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col",
          has && "@2xl:grid @2xl:grid-cols-2 @2xl:gap-6",
        )}
      >
        <div
          role={has ? "tabpanel" : undefined}
          id={has ? "prefill-panel-form" : undefined}
          aria-labelledby={has ? "prefill-tab-form" : undefined}
          className={cn(
            "min-h-0",
            showForm ? "flex flex-1 flex-col" : "hidden",
            has && "@2xl:flex @2xl:flex-col @2xl:overflow-y-auto @2xl:pr-2",
          )}
        >
          {form}
        </div>
        {has && (
          <div
            role="tabpanel"
            id="prefill-panel-document"
            aria-labelledby="prefill-tab-document"
            className={cn(
              "h-[calc(100dvh-14rem)] min-h-[24rem] @2xl:block @2xl:h-auto @2xl:min-h-0",
              tab === "document" ? "block" : "hidden",
            )}
          >
            {document}
          </div>
        )}
      </div>
    </div>
  );
}

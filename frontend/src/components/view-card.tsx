import { useId, type ReactNode } from "react";

import { FiguresDisclosure } from "@/components/figures-disclosure";
import { Card } from "@/components/ui/card";

/**
 * The standard Designed view (ADR-0031 section 6): a title naming what it answers, a headline
 * computed from the data, the chart switch where offered, the chart or stat tile, then "Show
 * figures". Neutral surface: colour lives in the chart.
 */
export function ViewCard({
  title,
  headline,
  toolbar,
  figures,
  figuresOpen,
  figuresName,
  children,
}: {
  title: string;
  headline?: ReactNode;
  /** Controls at the right of the title row (the chart switch, a by-Member toggle). */
  toolbar?: ReactNode;
  /** The exact figures behind the view; omit where the body already is the figures. */
  figures?: ReactNode;
  /** Open at first only where the table is the task. */
  figuresOpen?: boolean;
  /** What the figures hold, where a page has several: part of the disclosure button's name. */
  figuresName?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <Card>
      <section aria-labelledby={titleId} className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="flex min-w-0 flex-col gap-1">
            <h2 id={titleId} className="text-base font-semibold">
              {title}
            </h2>
            {headline && <p className="text-sm text-muted-foreground">{headline}</p>}
          </div>
          {toolbar && <div className="flex flex-wrap items-center gap-2">{toolbar}</div>}
        </div>
        {children}
        {figures && <FiguresDisclosure defaultOpen={figuresOpen} name={figuresName}>{figures}</FiguresDisclosure>}
      </section>
    </Card>
  );
}

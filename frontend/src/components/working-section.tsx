import { useId, type ReactNode } from "react";

import { FiguresDisclosure } from "@/components/figures-disclosure";
import { Card } from "@/components/ui/card";

/**
 * "The working": one card holding every piece of the working behind a result, each a
 * `WorkingSection` divided from the next, so the detail is one card rather than a stack of them.
 */
export function WorkingGroup({ children }: { children: ReactNode }) {
  const titleId = useId();
  return (
    <Card>
      <section
        aria-labelledby={titleId}
        data-testid="the-working"
        className="flex flex-col"
      >
        <div className="flex flex-col gap-1 p-4 pb-2">
          <h2 id={titleId} className="text-lg font-semibold">
            The working
          </h2>
          <p className="text-sm text-muted-foreground">
            What this result is built from. Each part is closed until you open
            it.
          </p>
        </div>
        <div className="flex flex-col divide-y divide-border">{children}</div>
      </section>
    </Card>
  );
}

/**
 * A piece of the working behind a result (ADR-0031 section 6), inside `WorkingGroup`: a titled
 * section whose body is closed behind "Show working". The title, a one-line note and any editor
 * `actions` stay visible, so a way to change what the section shows is never hidden with it.
 */
export function WorkingSection({
  title,
  note,
  actions,
  testId,
  children,
}: {
  title: string;
  note?: ReactNode;
  actions?: ReactNode;
  testId?: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      data-testid={testId}
      className="flex flex-col gap-3 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id={titleId} className="text-base font-semibold">
            {title}
          </h3>
          {note && <p className="text-sm text-muted-foreground">{note}</p>}
        </div>
        {actions}
      </div>
      <FiguresDisclosure label="working" name={title}>
        {children}
      </FiguresDisclosure>
    </section>
  );
}

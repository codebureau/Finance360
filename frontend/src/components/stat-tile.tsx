import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";

/**
 * The `figure` shape (ADR-0031 section 6): one hero value with up to two beside it. Neutral (no
 * severity): a surface that carries no state. Severity, a delta and a drill-in arrive with the
 * first screen that needs them (What's due, #101).
 */
export function StatTile({
  label,
  hero,
  headline,
  supporting,
  children,
}: {
  label: string;
  /** The hero value, usually a `<Money>`. */
  hero: ReactNode;
  headline?: ReactNode;
  supporting: { label: string; value: ReactNode }[];
  /** Below the figures: a note that belongs to the tile (what is not counted). */
  children?: ReactNode;
}) {
  return (
    <Card>
      <section aria-label={label} className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-muted-foreground">{label}</span>
            <span className="text-3xl leading-tight font-semibold tabular-nums" data-testid="stat-hero">
              {hero}
            </span>
          </div>
          <dl className="flex gap-x-8 text-sm">
            {supporting.slice(0, 2).map((s) => (
              <div key={s.label} className="flex flex-col gap-1">
                <dt className="text-muted-foreground">{s.label}</dt>
                <dd className="text-lg tabular-nums">{s.value}</dd>
              </div>
            ))}
          </dl>
        </div>
        {headline && <p className="text-sm">{headline}</p>}
        {children}
      </section>
    </Card>
  );
}

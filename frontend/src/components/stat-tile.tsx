import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { toneBg, type Tone } from "@/lib/entity-colour";
import { cn } from "@/lib/utils";

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
  accent,
  flat = false,
  compact = false,
  children,
}: {
  label: string;
  /** The hero value, usually a `<Money>`. */
  hero: ReactNode;
  headline?: ReactNode;
  supporting: { label: string; value: ReactNode }[];
  /** An entity's colour as a small accent bar on the left (a Scenario's tile), never a status. */
  accent?: Tone;
  /** Sits inside another card: no shadow of its own. */
  flat?: boolean;
  /** One of a set in a grid (a value per Scenario): smaller hero and padding, so the set stays short on a phone. */
  compact?: boolean;
  /** Below the figures: a note that belongs to the tile (what is not counted). */
  children?: ReactNode;
}) {
  return (
    <Card className={cn("relative overflow-hidden", flat && "shadow-none")}>
      {accent && <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1", toneBg(accent))} />}
      <section
        aria-label={label}
        className={cn("flex flex-col", compact ? "gap-1 p-3" : "gap-3 p-4", accent && (compact ? "pl-4" : "pl-5"))}
      >
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-muted-foreground">{label}</span>
            <span
              className={cn("leading-tight font-semibold tabular-nums", compact ? "text-lg" : "text-3xl")}
              data-testid="stat-hero"
            >
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

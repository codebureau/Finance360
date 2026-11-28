import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { toneBg, type Tone } from "@/lib/entity-colour";
import { cn } from "@/lib/utils";

/** The status tones (ADR-0031 section 5): the accent bar and the badge each tone wears. */
export type SeverityTone = "success" | "warning" | "destructive" | "info" | "muted";
const SEVERITY_BAR: Record<SeverityTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  info: "bg-info",
  muted: "bg-muted-foreground/55",
};
export const SEVERITY_BADGE = {
  success: "ok",
  warning: "flagged",
  destructive: "destructive",
  info: "info",
  muted: "closed",
} as const;
export type Severity = { tone: SeverityTone; icon: ReactNode; word: string };

/**
 * The `figure` shape (ADR-0031 section 6): one hero value with up to two beside it. Neutral unless
 * it has a severity: a surface that carries no state.
 *
 * With a `severity` (a Question card, ADR-0032) it adds a status-toned left accent bar, an icon and
 * a word badge: colour is never the only cue. The severity comes from a state, never from a score.
 */
export function StatTile({
  label,
  hero,
  headline,
  supporting,
  accent,
  severity,
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
  /** A status: its tone as the accent bar, with an icon and a word. Replaces `accent`. */
  severity?: Severity;
  /** Sits inside another card: no shadow of its own. */
  flat?: boolean;
  /** One of a set in a grid (a value per Scenario): smaller hero and padding, so the set stays short on a phone. */
  compact?: boolean;
  /** Below the figures: a note that belongs to the tile (what is not counted). */
  children?: ReactNode;
}) {
  return (
    <Card className={cn("relative overflow-hidden", flat && "shadow-none")}>
      {severity ? (
        <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1", SEVERITY_BAR[severity.tone])} />
      ) : (
        accent && <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1", toneBg(accent))} />
      )}
      <section
        aria-label={label}
        className={cn("flex flex-col", compact ? "gap-1 p-3" : "gap-3 p-4", (accent || severity) && (compact ? "pl-4" : "pl-5"))}
      >
        {severity && (
          <Badge variant={SEVERITY_BADGE[severity.tone]} className="w-fit items-center gap-1" data-testid="stat-severity">
            {severity.icon}
            {severity.word}
          </Badge>
        )}
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

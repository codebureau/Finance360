import { ChartBar, ChartBarStacked, ChartPie, TriangleAlert } from "lucide-react";
import { Fragment, lazy, Suspense, useId, useMemo, type ReactNode } from "react";

import { SegmentedControl, type SegmentOption } from "@/components/ui/segmented-control";
import { Button } from "@/components/ui/button";
import { ViewCard } from "@/components/view-card";
import {
  buildShareOption,
  drawnRows,
  formsFor,
  shareChartHeight,
  shareChartLabel,
  type ShareForm,
  type ShareRow,
} from "@/lib/charts/share";
import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { useDisplayPreference } from "@/lib/display-preferences";
import { TOKEN_BG, type Tone } from "@/lib/entity-colour";
import { useChartTheme } from "@/lib/use-chart-theme";
import { cn } from "@/lib/utils";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

const FORM_OPTIONS: SegmentOption<ShareForm>[] = [
  { value: "donut", label: "Donut", icon: ChartPie },
  { value: "stacked-bar", label: "Stacked bar", icon: ChartBarStacked },
  { value: "bars", label: "Bars", icon: ChartBar },
];

function Swatch({ tone }: { tone: Tone }) {
  return (
    <span
      aria-hidden
      className={cn(
        "mt-1 inline-block size-3 shrink-0 rounded-sm",
        tone === "warning" ? "bg-warning/85" : tone === "muted" ? "bg-muted-foreground/55" : TOKEN_BG[tone.token % TOKEN_BG.length],
      )}
    />
  );
}

function LegendLine({ row, nested = false }: { row: ShareRow; nested?: boolean }) {
  return (
    <li className={cn("flex items-baseline gap-2 text-sm", nested && "pl-5 text-xs text-muted-foreground")}>
      {!nested && <Swatch tone={row.tone} />}
      <span className="min-w-0 flex-1 break-words">
        {row.tone === "warning" && <TriangleAlert aria-hidden className="mr-1 inline size-3.5 text-warning" />}
        {row.name}
      </span>
      <span className="tabular-nums">{row.percentText}</span>
      {row.amountText && <span className="min-w-[6.5rem] text-right font-mono tabular-nums">{row.amountText}</span>}
    </li>
  );
}

/** The legend: the text equivalent of the chart. A folded "Other" lists the parts it holds. */
function Legend({ rows, label }: { rows: ShareRow[]; label: string }) {
  return (
    <ul aria-label={label} className="flex min-w-0 flex-col gap-1.5" data-testid="share-legend">
      {rows.map((r) => (
        <Fragment key={r.key}>
          <LegendLine row={r} />
          {r.parts && (
            <li>
              <ul className="flex flex-col gap-0.5">
                {r.parts.map((part) => (
                  <LegendLine key={part.key} row={part} nested />
                ))}
              </ul>
            </li>
          )}
        </Fragment>
      ))}
    </ul>
  );
}

/**
 * A `share` as a view card (ADR-0031): the headline, the form switch with its own "Use as my
 * default", the chart in the chosen form, a legend with the exact figures beside it and, behind
 * "Show figures", whatever table the caller holds. The switch keeps the view's form under
 * `viewKey`; the default for every `share` changes only through "Use as my default".
 */
export function ShareChart({
  viewKey,
  title,
  headline,
  rows,
  toolbar,
  figures,
  chartLabel,
  offered,
  after,
}: {
  viewKey: string;
  title: string;
  headline: string;
  /** Every part, largest first, already folded to at most six (`foldRows`). */
  rows: ShareRow[];
  /** Controls placed before the switch (a by-Member toggle). */
  toolbar?: ReactNode;
  figures?: ReactNode;
  /** The chart's accessible label, naming what is shown; the form is added to it. */
  chartLabel?: string;
  /** The forms this view offers, first is its own default (default: the kit order). */
  offered?: ShareForm[];
  /** Content under the chart and legend, above "Show figures" (a figure that sits beside the chart). */
  after?: ReactNode;
}) {
  const theme = useChartTheme();
  // Several cards can share one viewKey (one card per Member), so each radio group needs its own name.
  const groupId = useId();
  const forms = useMemo(() => formsFor(rows, offered), [rows, offered]);
  const pref = useDisplayPreference<ShareForm>(viewKey, "share", forms);
  const form = pref.form;
  const palette = useMemo(() => categoricalPaletteFromTokens(theme), [theme]);
  const option = useMemo(() => buildShareOption(form, rows, palette), [form, rows, palette]);
  const legend = useMemo(() => drawnRows(form, rows), [form, rows]);
  const switchable = FORM_OPTIONS.filter((o) => forms.includes(o.value));
  // A donut sits beside its legend; a stacked bar is wide, so its legend goes below it.
  const sideBySide = form === "donut";

  return (
    <ViewCard
      title={title}
      headline={headline}
      figures={figures}
      toolbar={
        <>
          {toolbar}
          {switchable.length > 1 && (
            <>
              <SegmentedControl
                label="Chart type"
                name={`${viewKey}-form-${groupId}`}
                value={form}
                options={switchable}
                onChange={pref.choose}
              />
              {pref.canUseAsDefault && (
                <Button variant="outline" onClick={pref.makeDefault}>
                  Use as my default
                </Button>
              )}
            </>
          )}
        </>
      }
    >
      <div className={cn("grid items-start gap-4", sideBySide && "sm:grid-cols-[minmax(0,14rem)_1fr]")}>
        {pref.ready ? (
          <div className="w-full min-w-0" data-testid="share-chart" data-form={form}>
            <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
              <EChart
                option={option}
                label={`${shareChartLabel(form, headline)}${chartLabel ? ` ${chartLabel}` : ""}`}
                className="w-full"
                height={shareChartHeight(form, rows.length)}
              />
            </Suspense>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Loading chart</p>
        )}
        <Legend rows={legend} label={`${title}: legend`} />
      </div>
      {after}
    </ViewCard>
  );
}

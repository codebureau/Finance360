import { ChartArea, ChartLine } from "lucide-react";
import { lazy, Suspense, useMemo } from "react";

import { Button } from "@/components/ui/button";
import { SegmentedControl, type SegmentOption } from "@/components/ui/segmented-control";
import { ViewCard } from "@/components/view-card";
import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import {
  buildOverTimeOption,
  headlineOf,
  overTimeChartLabel,
  overTimeForms,
  shapeProjection,
  type OverTimeForm,
} from "@/lib/charts/share-over-time";
import { useDisplayPreference } from "@/lib/display-preferences";
import type { ScenarioProjection } from "@/lib/scenarios";
import { useChartTheme } from "@/lib/use-chart-theme";
import { cn } from "@/lib/utils";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

const FORM_OPTIONS: SegmentOption<OverTimeForm>[] = [
  { value: "stacked-area", label: "Stacked area", icon: ChartArea },
  { value: "lines", label: "Lines", icon: ChartLine },
];

const KEY_LINE: Record<string, string> = {
  retirement: "border-solid",
  preservation_age: "border-dashed",
  age_pension_age: "border-dotted",
};

/**
 * "How the balances move" (ADR-0031, `share-over-time`): balances by Category at the end of each
 * year in Category colours, with the Life events that matter marked and the years spending is
 * short shaded. The Year by year table is the text equivalent, behind "Show working".
 */
export function BalancesChart({ p, viewKey = "projection.balances" }: { p: ScenarioProjection; viewKey?: string }) {
  const theme = useChartTheme();
  const model = useMemo(() => shapeProjection(p), [p]);
  const forms = useMemo(() => overTimeForms(model), [model]);
  const pref = useDisplayPreference<OverTimeForm>(viewKey, "share-over-time", forms);
  const form = pref.form;
  const palette = useMemo(() => categoricalPaletteFromTokens(theme), [theme]);
  const option = useMemo(() => buildOverTimeOption(model, form, palette), [model, form, palette]);
  const headline = useMemo(() => headlineOf(p), [p]);
  const switchable = FORM_OPTIONS.filter((o) => forms.includes(o.value));
  const afterHorizon = (p.life_events ?? []).filter(
    (e) => ["retirement", "preservation_age", "age_pension_age"].includes(e.kind) && e.year >= p.rows.length,
  ).length;

  return (
    <ViewCard
      title="How the balances move"
      headline={headline}
      toolbar={
        switchable.length > 1 && (
          <>
            <SegmentedControl
              label="Chart type"
              name={`${viewKey}-form`}
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
        )
      }
    >
      {pref.ready ? (
        <div className="w-full min-w-0" data-testid="balances-chart" data-form={form}>
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
            <EChart option={option} label={overTimeChartLabel(form, headline)} className="w-full" height={300} />
          </Suspense>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Loading chart</p>
      )}
      {(model.markers.length > 0 || model.shortSpans.length > 0) && (
        <ul aria-label="Marked on the chart" className="flex flex-col gap-1 text-xs" data-testid="chart-markers">
          {model.markers.map((m) => (
            <li key={`${m.kind}-${m.label}`} className="flex items-center gap-2">
              <span aria-hidden className={cn("inline-block w-6 border-t-2 border-foreground", KEY_LINE[m.kind])} />
              <span>
                {m.label}: {m.on}
              </span>
            </li>
          ))}
          {model.shortSpans.map((s) => (
            <li key={s.from} className="flex items-center gap-2" data-testid="chart-short">
              <span aria-hidden className="inline-block h-3 w-6 rounded-sm bg-warning/30" />
              <span>
                Short: spending is not met in full {model.labels[s.from] === model.labels[s.to]
                  ? `in ${model.labels[s.from]}`
                  : `from ${model.labels[s.from]} to ${model.labels[s.to]}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {afterHorizon > 0 && (
        <p className="text-xs text-muted-foreground">
          {afterHorizon === 1 ? "One marked event falls" : `${afterHorizon} marked events fall`} after the horizon, so{" "}
          {afterHorizon === 1 ? "it is" : "they are"} not drawn. The Life events list has the dates.
        </p>
      )}
    </ViewCard>
  );
}

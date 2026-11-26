import { lazy, Suspense, useMemo } from "react";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { buildSeriesOption, seriesChartLabel, type SeriesModel } from "@/lib/charts/series";
import { useChartTheme } from "@/lib/use-chart-theme";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

/**
 * A `series` (ADR-0031): overlaid lines with a legend, end labels and a crosshair tooltip. One
 * form, so no switch. The caller's table is the text equivalent, behind "Show figures".
 */
export function SeriesChart({
  model,
  what,
  headline,
  height,
  testId = "series-chart",
}: {
  model: SeriesModel;
  /** What the lines show, for the accessible name ("Net position over time"). */
  what: string;
  headline?: string | null;
  height?: number;
  testId?: string;
}) {
  const theme = useChartTheme();
  const option = useMemo(() => buildSeriesOption(model, categoricalPaletteFromTokens(theme)), [model, theme]);
  return (
    <div className="w-full min-w-0" data-testid={testId}>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
        <EChart option={option} label={seriesChartLabel(model, what, headline)} className="w-full" height={height ?? 300 + Math.max(0, model.lines.length - 3) * 24} />
      </Suspense>
    </div>
  );
}

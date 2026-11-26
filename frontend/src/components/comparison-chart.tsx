import { lazy, Suspense, useMemo } from "react";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { buildComparisonOption, comparisonChartLabel, type ComparisonModel } from "@/lib/charts/comparison";
import { useChartTheme } from "@/lib/use-chart-theme";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

/** A `comparison` (ADR-0031): grouped bars, one form, so no switch. The table is behind "Show figures". */
export function ComparisonChart({
  model,
  headline,
  testId = "comparison-chart",
}: {
  model: ComparisonModel;
  headline?: string | null;
  testId?: string;
}) {
  const theme = useChartTheme();
  const option = useMemo(() => buildComparisonOption(model, categoricalPaletteFromTokens(theme)), [model, theme]);
  // Each item gets a band tall enough to read, so a long list does not squash its bars.
  const height = 56 + model.items.length * model.measures.length * 30;
  return (
    <div className="w-full min-w-0" data-testid={testId}>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
        <EChart option={option} label={comparisonChartLabel(model, headline)} className="w-full" height={height} />
      </Suspense>
    </div>
  );
}

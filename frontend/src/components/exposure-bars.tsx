import { lazy, Suspense, useMemo } from "react";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { barChartHeight, buildBarOption, type ShareRow } from "@/lib/charts/share";
import { useChartTheme } from "@/lib/use-chart-theme";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

/** Shares of a whole as one horizontal bar per label; the exact figures sit behind "Show figures". */
export function ExposureBars({ rows, label }: { rows: ShareRow[]; label: string }) {
  const theme = useChartTheme();
  const option = useMemo(() => buildBarOption(rows, categoricalPaletteFromTokens(theme)), [rows, theme]);
  return (
    <div className="w-full min-w-0">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
        <EChart option={option} label={label} className="w-full" height={barChartHeight(rows.length)} />
      </Suspense>
    </div>
  );
}

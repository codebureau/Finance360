import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { categoricalPaletteFromTokens } from "@/lib/chart-palette";
import { buildBuildUpOption, buildUpChartLabel, type BuildUpLayout, type BuildUpModel } from "@/lib/charts/build-up";
import { useChartTheme } from "@/lib/use-chart-theme";

// ECharts is a large dependency, so it loads on demand.
const EChart = lazy(() => import("@/components/echart"));

/** Below this width (a phone) labels and names are turned so none overprint. */
const COMPACT_BELOW = 560;

/**
 * A `build-up` (ADR-0031): a waterfall of signed steps, one form, so no switch. The caller's table
 * is the text equivalent, behind "Show figures".
 */
export function BuildUpChart({
  model,
  what,
  headline,
  testId = "build-up-chart",
}: {
  model: BuildUpModel;
  /** What the steps build up to, for the accessible name. */
  what: string;
  headline?: string | null;
  testId?: string;
}) {
  const theme = useChartTheme();
  const box = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<BuildUpLayout>("wide");
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const read = () => setLayout(el.clientWidth > 0 && el.clientWidth < COMPACT_BELOW ? "compact" : "wide");
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const option = useMemo(
    () => buildBuildUpOption(model, categoricalPaletteFromTokens(theme), layout),
    [model, theme, layout],
  );
  return (
    <div ref={box} className="w-full min-w-0" data-testid={testId}>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
        <EChart
          option={option}
          label={buildUpChartLabel(model, what, headline)}
          className="w-full"
          height={layout === "compact" ? 380 : 320}
        />
      </Suspense>
    </div>
  );
}

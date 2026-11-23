import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use as register, type ECharts, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

// Modular ECharts: only the line, bar and pie charts, the components they need and the canvas renderer.
register([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

/**
 * A minimal ECharts host: creates the chart once, applies each new ``option`` (so a theme change,
 * which changes the option's colours, re-themes it), resizes with its container and disposes on
 * unmount. Loaded lazily so the charting library stays out of the first screen's bundle.
 */
export default function EChart({
  option,
  label,
  className,
  height,
}: {
  option: EChartsCoreOption;
  label: string;
  className?: string;
  /** Pixel height when the class does not fix one. */
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<ECharts | null>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const instance = init(el);
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={host} role="img" aria-label={label} className={className} style={height ? { height } : undefined} />;
}

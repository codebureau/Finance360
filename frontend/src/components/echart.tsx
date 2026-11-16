import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { init, use as register, type ECharts, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

// Modular ECharts: only the line chart, the components it needs and the canvas renderer.
register([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

/**
 * A minimal ECharts host: creates the chart once, applies each new ``option`` (so a theme change,
 * which changes the option's colours, re-themes it), resizes with its container and disposes on
 * unmount. Loaded lazily so the charting library stays out of the first screen's bundle.
 */
export default function EChart({
  option,
  label,
  className,
}: {
  option: EChartsCoreOption;
  label: string;
  className?: string;
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

  return <div ref={host} role="img" aria-label={label} className={className} />;
}

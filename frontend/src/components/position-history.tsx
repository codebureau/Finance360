import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { GeneralError } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { paletteFromTokens } from "@/lib/chart-palette";
import { buildOption, type ChartTheme } from "@/lib/history-chart";
import {
  fetchPositionHistory,
  seriesFor,
  type HistoryPoint,
  type HistorySeries,
  type PositionView,
} from "@/lib/position";

// ECharts is a large dependency, so it loads on demand, after the table is already on screen.
const EChart = lazy(() => import("@/components/echart"));

/** The current theme, following the header toggle (it flips the ``dark`` class on <html>). */
function useTheme(): ChartTheme {
  const read = (): ChartTheme =>
    document.documentElement.classList.contains("dark") ? "dark" : "light";
  const [theme, setTheme] = useState<ChartTheme>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

function Chart({ points, series }: { points: HistoryPoint[]; series: HistorySeries[] }) {
  const theme = useTheme();
  // The palette is read from the theme tokens (re-read when the theme flips).
  const option = useMemo(
    () => buildOption(points, series, theme, paletteFromTokens(theme)),
    [points, series, theme],
  );
  return (
    <div className="w-full min-w-0" data-testid="history-chart">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart</p>}>
        <EChart
          option={option}
          label={`Line chart of ${series.map((s) => s.label).join(", ")} over time. The table beside it has the same figures.`}
          className="h-64 w-full"
        />
      </Suspense>
    </div>
  );
}

function HistoryTable({ points, series }: { points: HistoryPoint[]; series: HistorySeries[] }) {
  return (
    <div
      className="max-h-64 min-w-0 overflow-auto"
      tabIndex={0}
      role="region"
      aria-label="Net position history table"
      data-testid="history-table"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">Net position at each month-end</caption>
        <thead className="sticky top-0 bg-card text-muted-foreground">
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1 pr-4 text-xs font-medium">
              As at
            </th>
            {series.map((s) => (
              <th key={s.key} scope="col" className="px-2 py-1 text-right text-xs font-medium">
                {s.label}
              </th>
            ))}
            <th scope="col" className="px-2 py-1 text-right text-xs font-medium">
              Not counted
            </th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.on} data-flagged={p.flagged > 0 ? "true" : undefined}>
              <th scope="row" className="py-1 pr-4 text-left font-normal whitespace-nowrap">
                {p.on}
              </th>
              {series.map((s) => (
                <td key={s.key} className="px-2 py-1 text-right whitespace-nowrap">
                  <Money amount={s.value(p)} />
                </td>
              ))}
              <td className="px-2 py-1 text-right whitespace-nowrap">
                {p.flagged > 0 ? (
                  <Badge variant="flagged">{p.flagged} (incomplete)</Badge>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Assets, Liabilities and Net over time for the chosen view and Tag, in a card that starts
 * collapsed so it never pushes the Position down. The table is the accessible fallback.
 */
export function PositionHistory({ tag, view }: { tag: string; view: PositionView }) {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["position-history", tag],
    queryFn: () => fetchPositionHistory(tag),
    placeholderData: (previous) => previous,
    enabled: open,
  });
  const h = history.data;
  const series = useMemo(() => (h ? seriesFor(view, h.members) : []), [h, view]);
  return (
    <Card className="mt-6">
      <section aria-label="Net position history">
        <h2 className="text-base font-semibold">
          <Button
            variant="ghost"
            aria-expanded={open}
            className="w-full justify-start rounded-xl px-4 py-3"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <ChevronDown aria-hidden className="size-4" /> : <ChevronRight aria-hidden className="size-4" />}
            History
          </Button>
        </h2>
        {open && (
          <div className="flex flex-col gap-3 px-4 pb-4">
            <GeneralError error={history.error} />
            {history.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
            {h && h.points.length === 0 && (
              <p className="text-sm text-muted-foreground">No Valuations recorded yet.</p>
            )}
            {h && h.points.length > 0 && (
              <>
                <div className="grid gap-4 lg:grid-cols-2">
                  <Chart points={h.points} series={series} />
                  <HistoryTable points={h.points} series={series} />
                </div>
                {h.points.some((p) => p.flagged > 0) && (
                  <p className="text-xs text-muted-foreground">
                    Hollow markers are incomplete: some Accounts had no Valuation or FX rate by then
                    and are not counted.
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </section>
    </Card>
  );
}

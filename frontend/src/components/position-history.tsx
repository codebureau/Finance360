import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { GeneralError } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { SeriesChart } from "@/components/series-chart";
import { ViewCard } from "@/components/view-card";
import { formatMoney } from "@/lib/money";
import {
  fetchPositionHistory,
  historyModel,
  seriesFor,
  type HistoryPoint,
  type HistorySeries,
  type PositionView,
} from "@/lib/position";

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

/** What to notice: where Net position started and where it is now, as the exact figures. */
function headlineOf(points: HistoryPoint[], series: HistorySeries[]): string | null {
  const net = series.find((s) => s.key === "net");
  if (!net || points.length === 0) return null;
  const first = points[0];
  const last = points[points.length - 1];
  const label = net.label === "Net" ? "Net position" : net.label;
  if (points.length === 1) return `${label} was ${formatMoney(net.value(last))} on ${last.on}.`;
  return `${label} moved from ${formatMoney(net.value(first))} on ${first.on} to ${formatMoney(net.value(last))} on ${last.on}.`;
}

/**
 * Assets, Liabilities and Net over time for the chosen view and Tag (shape `series`: lines only,
 * so no switch). The table is the text equivalent, behind "Show figures".
 */
export function PositionHistory({ tag, view }: { tag: string; view: PositionView }) {
  const history = useQuery({
    queryKey: ["position-history", tag],
    queryFn: () => fetchPositionHistory(tag),
    placeholderData: (previous) => previous,
  });
  const h = history.data;
  const series = useMemo(() => (h ? seriesFor(view, h.members) : []), [h, view]);
  const model = useMemo(() => (h ? historyModel(h.points, series) : { labels: [], lines: [] }), [h, series]);
  const headline = h ? headlineOf(h.points, series) : null;
  return (
    <ViewCard
      title="Over time"
      headline={headline ?? undefined}
      figures={h && h.points.length > 0 ? <HistoryTable points={h.points} series={series} /> : undefined}
    >
      <GeneralError error={history.error} />
      {history.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {h && h.points.length === 0 && (
        <p className="text-sm text-muted-foreground">No Valuations recorded yet.</p>
      )}
      {h && h.points.length > 0 && (
        <SeriesChart
          model={model}
          what="Assets, Liabilities and Net over time"
          headline={headline}
          height={280}
          testId="history-chart"
        />
      )}
      {h && h.points.some((p) => p.flagged > 0) && (
        <p className="text-xs text-muted-foreground">
          Hollow markers are incomplete: some Accounts had no Valuation or FX rate by then and are
          not counted.
        </p>
      )}
    </ViewCard>
  );
}

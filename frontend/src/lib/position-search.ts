import type { PositionView } from "@/lib/position";

/** The Position screen's linkable state: `/position?on=2026-09-30&view=member&tag=x&layout=table`. */
export type PositionSearch = {
  /** As-at date (YYYY-MM-DD); absent means today in the Household time zone, decided server-side. */
  on?: string;
  tag?: string;
  view?: Exclude<PositionView, "household">;
  layout?: "table";
};

/** Only non-default values are kept, so the plain URL stays `/position`. */
export function parsePositionSearch(raw: Record<string, unknown>): PositionSearch {
  const out: PositionSearch = {};
  if (typeof raw.on === "string" && /^\d{4}-\d{2}-\d{2}$/.test(raw.on)) out.on = raw.on;
  if (typeof raw.tag === "string" && raw.tag) out.tag = raw.tag;
  if (raw.view === "member" || raw.view === "joint") out.view = raw.view;
  if (raw.layout === "table") out.layout = "table";
  return out;
}

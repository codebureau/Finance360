import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import type { SeriesModel } from "@/lib/charts/series";
import { toneOf, type Tone } from "@/lib/entity-colour";
import { toApiError } from "@/lib/household";

export type Position = components["schemas"]["PositionOut"];
export type PositionClass = components["schemas"]["ClassOut"];
export type PositionCategory = components["schemas"]["CategoryOut"];
export type PositionAccount = components["schemas"]["AccountLineOut"];
export type Figures = components["schemas"]["FiguresOut"];

export type PositionView = "household" | "member" | "joint";

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** `on` empty means "today in the Household time zone" (the server decides). */
export async function fetchPosition(on: string, tag: string): Promise<Position> {
  const query: { on?: string; tag?: string } = {};
  if (on) query.on = on;
  if (tag) query.tag = tag;
  const { data, error, response } = await client.GET("/api/position", { params: { query } });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export type Column = {
  key: string;
  label: string;
  figure: (f: Figures) => string;
  line: (a: PositionAccount) => string | null;
};

/** The figure columns for a view: one for Household or Joint, one per Member plus Household. */
export function columnsFor(view: PositionView, members: Position["members"]): Column[] {
  const household: Column = {
    key: "household",
    label: "Household",
    figure: (f) => f.household,
    line: (a) => a.value,
  };
  if (view === "household") return [household];
  if (view === "joint") {
    return [
      {
        key: "joint",
        label: "Joint",
        figure: (f) => f.joint,
        line: (a) => (a.ownership === "joint" ? a.value : null),
      },
    ];
  }
  return [
    ...members.map((m) => ({
      key: `member-${m.id}`,
      label: m.name,
      figure: (f: Figures) => f.members.find((x) => x.member_id === m.id)?.total ?? "0.00",
      line: (a: PositionAccount) => a.shares[String(m.id)] ?? null,
    })),
    household,
  ];
}

export type PositionHistory = components["schemas"]["PositionHistoryOut"];
export type HistoryPoint = components["schemas"]["HistoryPointOut"];

/** No range: the server spans the earliest Valuation to today in the Household time zone. */
export async function fetchPositionHistory(tag: string): Promise<PositionHistory> {
  const query: { tag?: string } = {};
  if (tag) query.tag = tag;
  const { data, error, response } = await client.GET("/api/position/history", {
    params: { query },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export type HistorySeries = {
  key: string;
  label: string;
  /**
   * Colour follows the entity (ADR-0031 section 5): Assets, Liabilities and Net have a fixed
   * `position-part` tone, a Member line its Member's token. Never a status colour.
   */
  tone: Tone;
  /** The answer line (Net) is drawn heavier. */
  bold?: boolean;
  /** Liabilities keeps its dash, so identity never depends on colour alone. */
  dash?: "dashed";
  /** The money string for this series at a point; never parsed here. */
  value: (p: HistoryPoint) => string;
};

/**
 * What the chart draws for a view. Household and Joint: Assets, Liabilities and Net. By Member:
 * each Member's Net and the Household's Net (Assets and Liabilities are not split per Member).
 */
export function seriesFor(
  view: PositionView,
  members: PositionHistory["members"],
): HistorySeries[] {
  const net = toneOf("position-part", "net");
  if (view === "member") {
    return [
      ...members.map((m, i) => ({
        key: `member-${m.id}`,
        label: `${m.name} Net`,
        tone: toneOf("member", String(m.id), i),
        value: (p: HistoryPoint) => p.net.members.find((x) => x.member_id === m.id)?.total ?? "0.00",
      })),
      { key: "net", label: "Household Net", tone: net, bold: true, value: (p: HistoryPoint) => p.net.household },
    ];
  }
  const pick = (f: Figures) => (view === "joint" ? f.joint : f.household);
  return [
    { key: "assets", label: "Assets", tone: toneOf("position-part", "assets"), value: (p) => pick(p.assets) },
    {
      key: "liabilities",
      label: "Liabilities",
      tone: toneOf("position-part", "liabilities"),
      dash: "dashed",
      value: (p) => pick(p.liabilities),
    },
    { key: "net", label: "Net", tone: net, bold: true, value: (p) => pick(p.net) },
  ];
}

/** The `series` kit's model for the history: the same exact strings the table shows. */
export function historyModel(points: HistoryPoint[], series: HistorySeries[]): SeriesModel {
  return {
    labels: points.map((p) => p.on),
    lines: series.map((s) => ({
      key: s.key,
      name: s.label,
      tone: s.tone,
      bold: s.bold,
      dash: s.dash,
      values: points.map((p) => s.value(p)),
      incomplete: points.map((p) => p.flagged > 0),
    })),
    notes: points.map((p) =>
      p.flagged > 0 ? [`(incomplete: ${p.flagged} ${p.flagged === 1 ? "Account" : "Accounts"} not counted)`] : [],
    ),
  };
}

/** Why an Account is not in the totals, in words. */
export function flagText(a: PositionAccount, asAt: string): string | null {
  if (a.status === "no_valuation") return `No Valuation on or before ${asAt}`;
  if (a.status === "no_rate") return `No FX rate for ${a.currency} on or before ${asAt}`;
  return null;
}

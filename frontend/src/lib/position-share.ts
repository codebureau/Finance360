import { foldRows, type ShareRow } from "@/lib/charts/share";
import { toneOf } from "@/lib/entity-colour";
import { formatMoney, toMinor } from "@/lib/money";
import type { Figures, Position, PositionView } from "@/lib/position";

/**
 * Shapes the Position response into the `share` rows of "What it is made of" (a pure function over
 * the existing response; nothing is recalculated). Liabilities are never a part: only the asset
 * class is read. Money rule: amounts stay the API's exact strings; shares are integer parts per
 * million of the sum of the parts drawn.
 */

export type Composition = "category" | "member";

type Part = { key: string; name: string; amount: string; tone: ShareRow["tone"] };

/** Tenths of a percent as text from parts per million, integers only ("52.4%", "14%"). */
export function percentText(ppm: number): string {
  const tenths = Math.round(Math.abs(ppm) / 1000);
  const whole = Math.trunc(tenths / 10);
  const frac = tenths % 10;
  return `${ppm < 0 ? "-" : ""}${whole}${frac ? `.${frac}` : ""}%`;
}

function rowsOf(parts: Part[]): ShareRow[] {
  const minors = parts.map((p) => toMinor(p.amount) ?? 0n);
  const whole = minors.reduce((sum, m) => sum + m, 0n);
  const rows: ShareRow[] = [];
  parts.forEach((p, i) => {
    if (minors[i] === 0n) return; // nothing to draw; the figures keep the line
    const ppm = whole > 0n ? Number((minors[i] * 1_000_000n) / whole) : 0;
    rows.push({
      key: p.key,
      name: p.name,
      ppm,
      percentText: percentText(ppm),
      amountText: formatMoney(p.amount),
      tone: p.tone,
    });
  });
  return rows;
}

const pick = (f: Figures, view: PositionView) => (view === "joint" ? f.joint : f.household);

/** Assets by Category, in the view's figures, folded to at most six parts for the picture. */
export function categoryShareRows(p: Position, view: PositionView): ShareRow[] {
  const assets = p.classes.find((c) => c.class === "asset");
  if (!assets) return [];
  const parts = assets.categories.map((k, i) => ({
    key: k.key,
    name: k.name,
    amount: pick(k.figures, view),
    tone: toneOf("category", k.key, i),
  }));
  return foldRows(rowsOf(parts), []);
}

/** Assets by Member (a Joint Account counts by each Member's share), folded like the Categories. */
export function memberShareRows(p: Position, view: PositionView): ShareRow[] {
  const assets = p.classes.find((c) => c.class === "asset");
  if (!assets) return [];
  const parts = p.members.map((m, i) => {
    const f = assets.figures.members.find((x) => x.member_id === m.id);
    return {
      key: `member-${m.id}`,
      name: m.name,
      amount: (view === "joint" ? f?.joint_share : f?.total) ?? "0.00",
      tone: toneOf("member", String(m.id), i),
    };
  });
  return foldRows(rowsOf(parts), []);
}

/** What to notice in the picture: the largest share and the next, from the rows drawn. */
export function compositionHeadline(rows: ShareRow[], by: Composition): string {
  const named = rows.filter((r) => r.key !== "other");
  if (named.length === 0) return "Nothing is valued yet.";
  const [top, next] = named;
  const lead =
    by === "member"
      ? `${top.name} holds the largest share of assets, ${top.percentText}`
      : `${top.name} is the largest share, ${top.percentText}`;
  return next ? `${lead}; ${next.name} is ${next.percentText}.` : `${lead}.`;
}

/** The stat tile's sentence: Net position and the largest Category, from the response. */
export function netHeadline(p: Position, view: PositionView): string {
  const net = formatMoney(pick(p.net, view));
  const [top] = categoryShareRows(p, view).filter((r) => r.key !== "other");
  return top ? `Net position is ${net}; ${top.name} is the largest share of assets.` : `Net position is ${net}.`;
}

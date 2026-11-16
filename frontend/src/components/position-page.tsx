import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type CSSProperties } from "react";

import { GeneralError, inputClass } from "@/components/form-fields";
import { PositionHistory } from "@/components/position-history";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatMoney } from "@/lib/money";
import {
  columnsFor,
  fetchPosition,
  flagText,
  type Column,
  type Position,
  type PositionAccount,
  type PositionView,
} from "@/lib/position";
import type { PositionSearch } from "@/lib/position-search";
import { cn } from "@/lib/utils";

type Row = {
  id: string;
  /** Ids of the rows above this one; a collapsed ancestor hides it. */
  ancestors: string[];
  depth: 0 | 1 | 2;
  kind: "class" | "category" | "account";
  label: string;
  accountId?: number;
  detail?: string;
  flag?: string | null;
  cells: (string | null)[];
};

const CLASS_LABEL = { asset: "Assets", liability: "Liabilities" } as const;

function detailOf(a: PositionAccount): string | undefined {
  if (a.valuation_as_at === null) return undefined;
  const parts = [`as at ${a.valuation_as_at}`, a.source ?? ""];
  if (a.currency !== "AUD" && a.original !== null && a.rate !== null) {
    parts.push(`${formatMoney(a.original, a.currency)} @ ${a.rate} (rate of ${a.rate_as_at}${a.rate_source ? `, ${a.rate_source}` : ""})`);
  }
  return parts.filter(Boolean).join(" · ");
}

/** One flat list of rows for the chosen view; the tree and the table both render it. */
function buildRows(p: Position, view: PositionView, columns: Column[]): Row[] {
  const rows: Row[] = [];
  for (const c of p.classes) {
    const classId = `c:${c.class}`;
    const categoryRows: Row[] = [];
    for (const k of c.categories) {
      const categoryId = `k:${c.class}:${k.key}`;
      const visible = k.accounts.filter((a) => view !== "joint" || a.ownership === "joint");
      if (visible.length === 0) continue;
      categoryRows.push({
        id: categoryId,
        ancestors: [classId],
        depth: 1,
        kind: "category",
        label: k.name,
        cells: columns.map((col) => col.figure(k.figures)),
      });
      for (const a of visible) {
        categoryRows.push({
          id: `a:${a.account_id}`,
          ancestors: [classId, categoryId],
          depth: 2,
          kind: "account",
          label: a.name,
          accountId: a.account_id,
          detail: detailOf(a),
          flag: flagText(a, p.as_at),
          cells: columns.map((col) => col.line(a)),
        });
      }
    }
    rows.push({
      id: classId,
      ancestors: [],
      depth: 0,
      kind: "class",
      label: CLASS_LABEL[c.class],
      cells: columns.map((col) => col.figure(c.figures)),
    });
    rows.push(...categoryRows);
  }
  return rows;
}

/** An Account's name is a link to its detail page (Valuations, audit trail live there). */
function AccountLink({ row }: { row: Row }) {
  return (
    <Link
      to="/accounts/$accountId"
      params={{ accountId: String(row.accountId) }}
      className="font-normal hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      {row.label}
    </Link>
  );
}

/** Flagged: a badge plus the reason in words; the amount is a dash, never zero. */
function Flag({ text }: { text: string }) {
  return (
    <>
      <Badge variant="flagged" className="ml-2">
        Not counted
      </Badge>
      <span className="block text-xs text-warning">{text}</span>
    </>
  );
}

/**
 * Classes open, Categories closed: one level on first load. A row's open state is its default
 * flipped by whether the user has toggled it, so it survives a refetch.
 */
function Tree({ rows, columns }: { rows: Row[]; columns: Column[] }) {
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const isOpen = (r: Row) => (r.kind === "category" ? false : true) !== toggled.has(r.id);
  const toggle = (id: string) =>
    setToggled((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const byId = new Map(rows.map((r) => [r.id, r]));
  const grid: CSSProperties = {
    // The name keeps a readable minimum; with several Member columns the tree scrolls sideways
    // inside its own box (min-width), never the page.
    gridTemplateColumns: `minmax(9rem, 1fr) repeat(${columns.length}, minmax(6.5rem, auto))`,
  };
  const cell = "text-right whitespace-nowrap";

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card" data-testid="position-tree">
      <div style={{ minWidth: columns.length > 1 ? `${9 + columns.length * 7.5}rem` : undefined }}>
      <div
        className="grid gap-x-3 border-b border-border bg-muted/50 px-3 py-2 text-xs font-medium text-muted-foreground"
        style={grid}
      >
        <span>Class / Category / Account</span>
        {columns.map((c) => (
          <span key={c.key} className={cell}>
            {c.label}
          </span>
        ))}
      </div>
      <ul className="divide-y divide-border">
        {rows
          .filter((r) => r.ancestors.every((id) => isOpen(byId.get(id)!)))
          .map((r) => (
            <li
              key={r.id}
              className={cn(
                "grid items-baseline gap-x-3 px-3 py-[var(--cell-py)] text-sm",
                r.kind === "class" && "bg-muted/30 font-semibold",
                r.kind === "category" && "font-medium",
              )}
              style={grid}
            >
              <span className="min-w-0 break-words" style={{ paddingLeft: `${r.depth * 1.25}rem` }}>
                {r.kind === "account" ? (
                  <AccountLink row={r} />
                ) : r.kind === "category" ? (
                  <Button
                    variant="ghost"
                    aria-expanded={isOpen(r)}
                    className="-ml-2 min-h-0 gap-1 px-2 py-0.5 whitespace-normal"
                    onClick={() => toggle(r.id)}
                  >
                    {isOpen(r) ? (
                      <ChevronDown aria-hidden className="size-4 shrink-0" />
                    ) : (
                      <ChevronRight aria-hidden className="size-4 shrink-0" />
                    )}
                    {r.label}
                  </Button>
                ) : (
                  r.label
                )}
                {r.flag && <Flag text={r.flag} />}
                {r.detail && <span className="block text-xs font-normal text-muted-foreground">{r.detail}</span>}
              </span>
              {r.cells.map((value, i) => (
                <span key={columns[i].key} className={cell}>
                  <Money amount={value} />
                </span>
              ))}
            </li>
          ))}
      </ul>
      </div>
    </div>
  );
}

function PositionTable({ rows, columns }: { rows: Row[]; columns: Column[] }) {
  return (
    <div data-testid="position-table">
      <Table>
        <caption className="sr-only">Position by Class, Category and Account</caption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead scope="col">Class / Category / Account</TableHead>
            {columns.map((c) => (
              <TableHead key={c.key} scope="col" className="text-right">
                {c.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.id} className={cn(r.kind === "class" && "bg-muted/30 font-semibold")}>
              <th
                scope="row"
                className={cn(
                  "px-[var(--cell-px)] py-[var(--cell-py)] text-left",
                  r.kind === "account" ? "font-normal" : "font-semibold",
                )}
                style={{ paddingLeft: `${r.depth * 1.25 + 0.75}rem` }}
              >
                {r.kind === "account" ? <AccountLink row={r} /> : r.label}
                {r.flag && <Flag text={r.flag} />}
                {r.detail && <span className="block text-xs font-normal text-muted-foreground">{r.detail}</span>}
              </th>
              {r.cells.map((value, i) => (
                <TableCell key={columns[i].key} className="text-right whitespace-nowrap">
                  <Money amount={value} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Assets, Liabilities and Net for the view, pinned while the Position scrolls under it. */
function TotalsBar({ p, view }: { p: Position; view: PositionView }) {
  const pick = (f: Position["net"]) => (view === "joint" ? f.joint : f.household);
  const [assets, liabilities] = p.classes;
  const items: [string, string][] = [
    ["Assets", pick(assets.figures)],
    ["Liabilities", pick(liabilities.figures)],
    ["Net", pick(p.net)],
  ];
  return (
    <section
      aria-label="Totals"
      data-testid="totals-bar"
      className="sticky top-14 z-30 -mx-4 mb-4 border-b border-border bg-background/95 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6 md:top-0"
    >
      <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
        {items.map(([label, amount]) => (
          <div key={label} className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className={cn("text-base", label === "Net" && "font-semibold")}>
              <Money amount={amount} />
            </dd>
          </div>
        ))}
        {p.flagged > 0 && (
          <Badge variant="flagged" role="status">
            {p.flagged} not counted
          </Badge>
        )}
      </dl>
    </section>
  );
}

/** Per-column Net (By Member), the exactness check and the flagged note. */
function Totals({ p, columns, view }: { p: Position; columns: Column[]; view: PositionView }) {
  const sole = p.net.members.map((m) => ({
    name: p.members.find((x) => x.id === m.member_id)?.name ?? `Member ${m.member_id}`,
    amount: m.sole,
  }));
  return (
    <section aria-label="Reconciliation" className="mt-4 flex flex-col gap-2 text-sm">
      {view === "member" && (
        <div data-testid="position-totals">
          <Table>
            <caption className="sr-only">Net by Member</caption>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col">Net</TableHead>
                {columns.map((c) => (
                  <TableHead key={c.key} scope="col" className="text-right">
                    {c.label}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <th scope="row" className="px-[var(--cell-px)] py-[var(--cell-py)] text-left font-medium">
                  Net
                </th>
                {columns.map((c) => (
                  <TableCell key={c.key} className="text-right whitespace-nowrap">
                    <Money amount={c.figure(p.net)} />
                  </TableCell>
                ))}
              </TableRow>
            </TableBody>
          </Table>
        </div>
      )}
      <p data-testid="reconciliation" className="text-muted-foreground">
        {sole.map((s) => `${s.name} (held solely) ${formatMoney(s.amount)}`).join(" + ")} + Joint{" "}
        {formatMoney(p.net.joint)} = Household {formatMoney(p.net.household)}
      </p>
      {p.flagged > 0 && (
        <p className="font-medium text-warning">
          {p.flagged} {p.flagged === 1 ? "Account is" : "Accounts are"} not in these totals: no
          Valuation or FX rate on or before {p.as_at}.
        </p>
      )}
    </section>
  );
}

const VIEWS: { key: PositionView; label: string }[] = [
  { key: "household", label: "Household" },
  { key: "member", label: "By Member" },
  { key: "joint", label: "Joint" },
];

/** The Position: as-at date, view, Tag and layout are URL search params (`onSearch` patches them). */
export function PositionPage({
  search,
  onSearch,
}: {
  search: PositionSearch;
  onSearch: (patch: Partial<Record<keyof PositionSearch, string | undefined>>) => void;
}) {
  const on = search.on ?? "";
  const tag = search.tag ?? "";
  const view: PositionView = search.view ?? "household";
  const layout = search.layout ?? "tree";
  const position = useQuery({
    queryKey: ["position", on, tag],
    queryFn: () => fetchPosition(on, tag),
    placeholderData: (previous) => previous,
  });
  const p = position.data;
  const columns = p ? columnsFor(view, p.members) : [];

  return (
    <div className="flex flex-col">
      <PageHeader title="Position" description="What the household owns and owes." />
      <div className="mb-4 flex flex-wrap items-end gap-x-4 gap-y-3">
        <label className="flex flex-col gap-1 text-sm">
          As at
          <input
            className={inputClass}
            type="date"
            value={on || p?.as_at || ""}
            onChange={(e) => onSearch({ on: e.target.value || undefined })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Tag
          <select
            className={inputClass}
            value={tag}
            onChange={(e) => onSearch({ tag: e.target.value || undefined })}
          >
            <option value="">All Accounts</option>
            {(p?.tags ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
          <legend className="mb-1">View</legend>
          {VIEWS.map((v) => (
            <label key={v.key} className="flex items-center gap-1">
              <input
                type="radio"
                name="position-view"
                checked={view === v.key}
                onChange={() => onSearch({ view: v.key === "household" ? undefined : v.key })}
              />
              {v.label}
            </label>
          ))}
        </fieldset>
        <fieldset className="flex gap-3 text-sm">
          <legend className="mb-1">Layout</legend>
          {(["tree", "table"] as const).map((l) => (
            <label key={l} className="flex items-center gap-1">
              <input
                type="radio"
                name="position-layout"
                checked={layout === l}
                onChange={() => onSearch({ layout: l === "tree" ? undefined : l })}
              />
              {l === "tree" ? "Tree" : "Table"}
            </label>
          ))}
        </fieldset>
      </div>
      <GeneralError error={position.error} />
      {position.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {p && (
        <>
          <TotalsBar p={p} view={view} />
          {(() => {
            const rows = buildRows(p, view, columns);
            return layout === "tree" ? (
              <Tree rows={rows} columns={columns} />
            ) : (
              <PositionTable rows={rows} columns={columns} />
            );
          })()}
          <Totals p={p} columns={columns} view={view} />
        </>
      )}
      <PositionHistory tag={tag} view={view} />
    </div>
  );
}

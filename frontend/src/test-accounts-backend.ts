import { vi } from "vitest";

/** A tiny in-memory Accounts and Valuations backend behind fetch, with the API's shapes. */
export type Owner = { member_id: number; member_name: string; share_bp: number };
export type A = {
  id: number;
  name: string;
  category_key: string;
  category_name: string;
  account_type_key: string;
  account_type_name: string;
  currency: string;
  notes: string | null;
  opened_on: string | null;
  closed_on: string | null;
  ownership: "sole" | "joint";
  owners: Owner[];
  tags: string[];
};
export type V = {
  id: number;
  account_id: number;
  as_at: string;
  amount: string;
  amount_minor: number;
  currency: string;
  source: string;
  provenance_note: string | null;
  recorded_at: string;
  status: "current" | "corrected" | "removed";
  superseded_at: string | null;
  superseded_by_id: number | null;
};
export type Call = { method: string; path: string; search: string; body?: Record<string, unknown> };

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const MEMBERS = [
  { id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" },
  { id: 2, name: "Jordan Sample", date_of_birth: "1986-09-30" },
];
export const CATALOGUE = {
  version: 1,
  classes: [
    {
      class: "asset",
      categories: [
        {
          key: "cash",
          name: "Cash",
          account_types: [
            { key: "transaction_account", name: "Transaction account", behaviour_key: "simple_balance" },
            { key: "savings_account", name: "Savings account", behaviour_key: "simple_balance" },
          ],
        },
        {
          key: "vehicles",
          name: "Vehicles",
          account_types: [{ key: "vehicle", name: "Vehicle", behaviour_key: "simple_balance" }],
        },
      ],
    },
    {
      class: "liability",
      categories: [
        {
          key: "loans",
          name: "Loans",
          account_types: [{ key: "mortgage", name: "Mortgage", behaviour_key: "simple_balance" }],
        },
      ],
    },
  ],
};

export const everyday: A = {
  id: 10,
  name: "Everyday account",
  category_key: "cash",
  category_name: "Cash",
  account_type_key: "transaction_account",
  account_type_name: "Transaction account",
  currency: "AUD",
  notes: null,
  opened_on: null,
  closed_on: null,
  ownership: "sole",
  owners: [{ member_id: 1, member_name: "Alex Sample", share_bp: 10000 }],
  tags: ["everyday"],
};
export const shared: A = {
  ...everyday,
  id: 11,
  name: "Joint savings",
  ownership: "joint",
  owners: [
    { member_id: 1, member_name: "Alex Sample", share_bp: 5000 },
    { member_id: 2, member_name: "Jordan Sample", share_bp: 5000 },
  ],
  tags: ["emergency fund"],
};
export const oldCar: A = { ...everyday, id: 12, name: "Old car", closed_on: "2025-01-01", tags: [] };
export const homeLoan: A = {
  ...everyday,
  id: 13,
  name: "Home loan",
  category_key: "loans",
  category_name: "Loans",
  account_type_key: "mortgage",
  account_type_name: "Mortgage",
  tags: [],
};

export const valuation = (id: number, over: Partial<V>): V => ({
  id,
  account_id: 10,
  as_at: "2026-01-31",
  amount: "100.00",
  amount_minor: 10000,
  currency: "AUD",
  source: "statement",
  provenance_note: "January statement",
  recorded_at: "2026-02-01T00:00:00",
  status: "current",
  superseded_at: null,
  superseded_by_id: null,
  ...over,
});

/** An Account identifier as the API returns it: masked. `secret` is what only reveal returns. */
export type Ident = { id: number; kind: string; masked_value: string; institution: string | null; secret: string };

export function fakeBackend(
  seed: A[],
  opts: { valuations?: V[]; identifiers?: Ident[]; rejectCreate?: boolean; needsConfirm?: boolean } = {},
) {
  let accounts = [...seed];
  let rows = [...(opts.valuations ?? [])];
  let idents = [...(opts.identifiers ?? [])];
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const hasBody = request.method === "POST" || request.method === "PUT";
      const body = hasBody
        ? ((await request.json().catch(() => undefined)) as Record<string, unknown> | undefined)
        : undefined;
      calls.push({ method: request.method, path, search: url.search, body });
      if (path === "/api/household")
        return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: MEMBERS });
      if (path === "/api/catalogue") return json(CATALOGUE);
      if (path === "/api/tags") return json(["emergency fund", "everyday", "retirement"]);
      if (path === "/api/accounts" && request.method === "GET")
        return json(
          accounts.filter((a) => url.searchParams.get("include_closed") === "true" || !a.closed_on),
        );
      if (path === "/api/accounts" && request.method === "POST") {
        if (opts.rejectCreate)
          return json({ errors: [{ field: "name", message: "Account name is required." }] }, 422);
        const created = { ...everyday, id: 99, ...body } as unknown as A;
        accounts.push(created);
        return json(created, 201);
      }
      const ident = /^\/api\/accounts\/(\d+)\/identifiers(?:\/(\d+))?(\/reveal)?$/.exec(path);
      if (ident) {
        const shown = (i: Ident) => ({ id: i.id, kind: i.kind, masked_value: i.masked_value, institution: i.institution });
        if (ident[3]) return json({ value: idents.find((i) => i.id === Number(ident[2]))?.secret });
        if (request.method === "GET") return json(idents.map(shown));
        if (request.method === "POST") {
          if (!String(body?.value).trim())
            return json({ errors: [{ field: "value", message: "Enter the identifier." }] }, 422);
          const value = String(body?.value);
          const made: Ident = {
            id: 70,
            kind: String(body?.kind),
            masked_value: `••• ${value.slice(-4)}`,
            institution: (body?.institution as string | null) ?? null,
            secret: value,
          };
          idents = [...idents, made];
          return json(shown(made), 201);
        }
        if (request.method === "DELETE") {
          idents = idents.filter((i) => i.id !== Number(ident[2]));
          return new Response(null, { status: 204 });
        }
      }
      const close = /^\/api\/accounts\/(\d+)\/close$/.exec(path);
      if (close) {
        if (opts.needsConfirm && body?.confirm_non_zero === false)
          return json({ message: "The latest Valuation is AUD 100.00 at 2026-01-31, not zero." }, 409);
        accounts = accounts.map((a) =>
          a.id === Number(close[1]) ? { ...a, closed_on: String(body?.closed_on) } : a,
        );
        return json(accounts.find((a) => a.id === Number(close[1])));
      }
      const vlist = /^\/api\/accounts\/(\d+)\/valuations$/.exec(path);
      if (vlist && request.method === "GET")
        return json(rows.filter((r) => r.account_id === Number(vlist[1])));
      if (vlist && request.method === "POST") {
        if (!String(body?.provenance_note).trim())
          return json(
            { errors: [{ field: "provenance_note", message: "Say where this figure came from." }] },
            422,
          );
        const created = valuation(50, {
          account_id: Number(vlist[1]),
          as_at: String(body?.as_at),
          amount: String(body?.amount),
          source: String(body?.source),
          provenance_note: String(body?.provenance_note),
        });
        rows = [created, ...rows];
        return json(created, 201);
      }
      const v = /^\/api\/valuations\/(\d+)$/.exec(path);
      if (v && request.method === "PUT") {
        const old = rows.find((r) => r.id === Number(v[1]))!;
        const fresh = valuation(60, {
          account_id: old.account_id,
          as_at: String(body?.as_at),
          amount: String(body?.amount),
          source: String(body?.source),
          provenance_note: String(body?.provenance_note),
        });
        rows = [
          fresh,
          ...rows.map((r) =>
            r === old
              ? { ...r, status: "corrected" as const, superseded_at: "2026-10-17T01:00:00", superseded_by_id: 60 }
              : r,
          ),
        ];
        return json(fresh);
      }
      if (v && request.method === "DELETE") {
        rows = rows.map((r) =>
          r.id === Number(v[1]) ? { ...r, status: "removed" as const, superseded_at: "2026-10-17T01:00:00" } : r,
        );
        return new Response(null, { status: 204 });
      }
      const one = /^\/api\/accounts\/(\d+)$/.exec(path);
      if (one && request.method === "GET") {
        const found = accounts.find((a) => a.id === Number(one[1]));
        return found ? json(found) : json({ message: "Account not found." }, 404);
      }
      if (one && request.method === "PUT") {
        if (body?.name === "") return json({ errors: [{ field: "name", message: "Account name is required." }] }, 422);
        const owners = (body?.owners as { member_id: number; share_bp?: number }[] | undefined)?.map((o) => ({
          member_id: o.member_id,
          member_name: MEMBERS.find((m) => m.id === o.member_id)?.name ?? "",
          share_bp: o.share_bp ?? 10000,
        }));
        accounts = accounts.map((a) =>
          a.id === Number(one[1]) ? ({ ...a, ...body, ...(owners ? { owners } : {}) } as A) : a,
        );
        return json(accounts.find((a) => a.id === Number(one[1])));
      }
      return new Response("?", { status: 500 });
    }),
  );
  return calls;
}

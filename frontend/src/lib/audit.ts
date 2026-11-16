import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type AuditEntry = components["schemas"]["AuditEntryOut"];
export type AuditPage = components["schemas"]["AuditPage"];
export type AuthEntry = components["schemas"]["AuthEntryOut"];
export type AuthPage = components["schemas"]["AuthPage"];

/** What narrows the trail. `account_id` means an Account's own records plus its Valuations'. */
export type AuditFilter = {
  entity_type?: string;
  entity_id?: number;
  account_id?: number;
  date_from?: string;
  date_to?: string;
};

/**
 * The Audit trail page's state as URL search params, so a filter survives refresh, back and
 * forward and can be linked to. `tab` is absent for Changes.
 */
export type AuditSearch = AuditFilter & { tab?: "sign-ins"; kind?: string; offset?: number };

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Validate untrusted search params: anything malformed is dropped, never thrown on. */
export function parseAuditSearch(search: Record<string, unknown>): AuditSearch {
  const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : undefined);
  const str = (v: unknown) => (typeof v === "string" && v !== "" ? v : undefined);
  const date = (v: unknown) => (typeof v === "string" && DATE.test(v) ? v : undefined);
  const parsed: AuditSearch = {
    tab: search.tab === "sign-ins" ? "sign-ins" : undefined,
    entity_type: str(search.entity_type),
    entity_id: int(search.entity_id),
    account_id: int(search.account_id),
    date_from: date(search.date_from),
    date_to: date(search.date_to),
    kind: str(search.kind),
    offset: int(search.offset) || undefined,
  };
  // Keys stay present as undefined: the router merges this over the raw params, so an invalid raw
  // value must be overwritten, not merely omitted.
  return parsed;
}

export const PAGE_SIZE = 25;

export const ENTITY_TYPES = [
  { key: "household", label: "Household" },
  { key: "member", label: "Member" },
  { key: "account", label: "Account" },
  { key: "valuation", label: "Valuation" },
  { key: "super_statement", label: "Super statement" },
  { key: "fx_rate", label: "FX rate" },
  { key: "instrument", label: "Instrument" },
  { key: "price", label: "Price" },
  { key: "scenario", label: "Scenario" },
  { key: "assumption", label: "Assumption" },
  { key: "spending_phase", label: "Spending phase" },
  { key: "income_stream", label: "Income stream" },
  { key: "tax_record", label: "Tax record" },
  { key: "library_document", label: "Library document" },
  { key: "document_link", label: "Document link" },
  { key: "projection_run", label: "Projection run" },
] as const;

export const AUTH_KINDS = [
  { key: "login", label: "Signed in" },
  { key: "login_failed", label: "Sign-in failed" },
  { key: "lockout", label: "Locked out" },
  { key: "logout", label: "Signed out" },
  { key: "password_changed", label: "Password changed" },
  { key: "password_reset", label: "Password reset" },
  { key: "totp_confirmed", label: "Authenticator enabled" },
  { key: "totp_disabled", label: "Authenticator disabled" },
] as const;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

const clean = <T extends object>(q: T) =>
  Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== "")) as T;

export async function fetchAudit(filter: AuditFilter, offset: number): Promise<AuditPage> {
  const { data, error, response } = await client.GET("/api/audit", {
    params: { query: clean({ ...filter, limit: PAGE_SIZE, offset }) },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchAuthEvents(
  filter: { kind?: string; date_from?: string; date_to?: string },
  offset: number,
): Promise<AuthPage> {
  const { data, error, response } = await client.GET("/api/audit/auth-events", {
    params: { query: clean({ ...filter, limit: PAGE_SIZE, offset }) },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** A UTC timestamp as local date and time in an IANA zone (the Household's). */
export function formatWhen(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-AU", {
    timeZone,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

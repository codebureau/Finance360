import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";
import { STATEMENT_GROUPS } from "@/lib/tax";

export type ProfileSummary = components["schemas"]["ProfileSummaryOut"];
export type ProfileDetail = components["schemas"]["ProfileDetailOut"];
export type ProfileTest = components["schemas"]["ProfileTestOut"];
export type ProfileActivated = components["schemas"]["ProfileActivatedOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export const PROFILES_KEY = ["document-profiles"] as const;

export async function fetchProfiles(): Promise<ProfileSummary[]> {
  const { data, error, response } = await client.GET("/api/document-profiles");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchProfile(id: number): Promise<ProfileDetail> {
  const { data, error, response } = await client.GET("/api/document-profiles/{profile_id}", {
    params: { path: { profile_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchShippedProfile(key: string): Promise<ProfileDetail> {
  const { data, error, response } = await client.GET("/api/document-profiles/shipped/{key}", {
    params: { path: { key } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** A saved profile re-run, live, over the document it was tested on. Nothing is stored. */
export async function reviewProfile(id: number): Promise<ProfileTest> {
  const { data, error, response } = await client.GET("/api/document-profiles/{profile_id}/review", {
    params: { path: { profile_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Validate a pasted draft and read a Library document with it. Nothing is stored. */
export async function testProfile(profileJson: string, documentId: number): Promise<ProfileTest> {
  const { data, error, response } = await client.POST("/api/document-profiles/test", {
    body: { profile_json: profileJson, document_id: documentId },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Save a pasted draft as a Proposed profile (it is tested again on the server). */
export async function saveProfile(profileJson: string, documentId: number): Promise<ProfileDetail> {
  const { data, error, response } = await client.POST("/api/document-profiles", {
    body: { profile_json: profileJson, document_id: documentId },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function activateProfile(id: number): Promise<ProfileActivated> {
  const { data, error, response } = await client.POST("/api/document-profiles/{profile_id}/activate", {
    params: { path: { profile_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function retireProfile(id: number): Promise<ProfileDetail> {
  const { data, error, response } = await client.POST("/api/document-profiles/{profile_id}/retire", {
    params: { path: { profile_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** How the screen names a status, and the Badge variant that goes with it (same state, same badge). */
export const STATUS: Record<string, { label: string; variant: "ok" | "info" | "flagged" | "closed" }> = {
  shipped: { label: "Shipped", variant: "info" },
  proposed: { label: "Proposed", variant: "flagged" },
  active: { label: "Active", variant: "ok" },
  retired: { label: "Retired", variant: "closed" },
  failed_to_load: { label: "Failed to load", variant: "flagged" },
  draft: { label: "Draft", variant: "info" },
};

export const SOURCE_TEXT: Record<string, string> = {
  shipped: "Shipped with Finance360",
  pasted: "Pasted in Settings",
  mcp: "Proposed from Claude Code",
  ai: "Proposed by the AI",
};

/** Why a figure was not found, in words. */
export const NOT_FOUND_TEXT: Record<string, string> = {
  label_not_found: "its label is not in the document",
  ambiguous: "its label matches more than once",
  no_value: "the label has no value beside it",
  section_not_found: "its section is not in the document",
  not_an_amount: "what is beside it is not an amount",
  not_a_year: "what is beside it is not a year",
  negative: "it is negative and the profile does not allow that",
  too_long: "the text beside it is too long",
};

const TAX_NAMES: Record<string, string> = {
  period: "Period",
  ...Object.fromEntries(STATEMENT_GROUPS.flatMap((g) => g.fields).map((f) => [f.key, f.name])),
};

/** The form's name for a field of a Fill target; the key itself where the form is not known here. */
export function fieldName(target: string, key: string): string {
  return target === "tax.income_statement" ? (TAX_NAMES[key] ?? key) : key;
}

/** Whether a reading is good enough to save or activate. */
export const usable = (outcome: string) => outcome === "ok" || outcome === "warnings";

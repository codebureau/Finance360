import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type ExposureDimension = components["schemas"]["DimensionOut"];
export type ExposureProfile = components["schemas"]["ProfileOut"];
export type ExposureProfileInput = components["schemas"]["ProfileIn"];
export type ExposureResolution = components["schemas"]["ResolutionOut"];
export type LookThrough = components["schemas"]["LookThroughOut"];
export type LookThroughLine = components["schemas"]["LookThroughLineOut"];
export type LookThroughPart = components["schemas"]["LookThroughPartOut"];
export type ExposureReference = components["schemas"]["ReferenceOut"];

/** The named tolerance (ADR-0030): a Dimension may total 100% plus or minus this many ppm. The
 * server decides; the form only previews it. */
export const WEIGHT_TOLERANCE_PPM = 10_000;
const PPM = 1_000_000;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchExposureVocabulary(): Promise<ExposureDimension[]> {
  const { data, error, response } = await client.GET("/api/exposure/vocabulary");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchExposureProfiles(instrumentId: number): Promise<ExposureProfile[]> {
  const { data, error, response } = await client.GET(
    "/api/instruments/{instrument_id}/exposure-profiles",
    { params: { path: { instrument_id: instrumentId } } },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function recordExposureProfile(
  instrumentId: number,
  input: ExposureProfileInput,
): Promise<ExposureProfile> {
  const { data, error, response } = await client.POST(
    "/api/instruments/{instrument_id}/exposure-profiles",
    { params: { path: { instrument_id: instrumentId } }, body: input },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The Dimension the Account panel opens on when the URL names none. */
export const DEFAULT_DIMENSION = "asset_class";

export async function fetchLookThrough(
  accountId: number,
  asAt: string | undefined,
  dimension: string | undefined,
): Promise<LookThrough> {
  const { data, error, response } = await client.GET("/api/accounts/{account_id}/look-through", {
    params: {
      path: { account_id: accountId },
      query: { ...(asAt ? { on: asAt } : {}), ...(dimension ? { dimension } : {}) },
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** A percent string with at most 4 decimal places to parts per million, by integer arithmetic
 * only (never a float). `null` when it is not such a number. */
export function percentToPpm(text: string): number | null {
  const match = /^(\d{1,3})(?:\.(\d{1,4}))?$/.exec(text.trim());
  if (!match) return null;
  return Number(match[1]) * 10_000 + Number((match[2] ?? "").padEnd(4, "0"));
}

/** Any ppm count as a percent string (`"99.5"`, `"-0.5"`, `"0"`), integers only. */
export function ppmToPercent(ppm: number): string {
  const whole = Math.trunc(Math.abs(ppm) / 10_000);
  const frac = String(Math.abs(ppm) % 10_000)
    .padStart(4, "0")
    .replace(/0+$/, "");
  return `${ppm < 0 ? "-" : ""}${whole}${frac ? `.${frac}` : ""}`;
}

export type TotalState = "empty" | "exact" | "short" | "over" | "rejected";

/** The sum of entered percent strings in ppm (blank and invalid count as nothing). */
export function sumPpm(texts: string[]): number {
  return texts.reduce((sum, t) => sum + (percentToPpm(t.trim()) ?? 0), 0);
}

/**
 * The running total of one Dimension's entered weights and how the server will judge it. A
 * fund-of-funds' references (`referencesPpm`) count towards every Dimension that has weights; a
 * Dimension with none entered is left out, whatever the references total.
 */
export function judgeTotal(
  texts: string[],
  referencesPpm = 0,
): { totalPpm: number; state: TotalState; differencePpm: number } {
  const entered = texts.map((t) => t.trim()).filter((t) => t !== "");
  const totalPpm = sumPpm(entered) + (entered.length > 0 ? referencesPpm : 0);
  const differencePpm = totalPpm - PPM;
  if (entered.length === 0) return { totalPpm, state: "empty", differencePpm };
  if (Math.abs(differencePpm) > WEIGHT_TOLERANCE_PPM) return { totalPpm, state: "rejected", differencePpm };
  if (differencePpm === 0) return { totalPpm, state: "exact", differencePpm };
  return { totalPpm, state: differencePpm < 0 ? "short" : "over", differencePpm };
}

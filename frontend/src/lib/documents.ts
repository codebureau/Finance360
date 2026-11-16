import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Reading = components["schemas"]["ReadingOut"];
export type ReadingFound = components["schemas"]["FoundOut"];
export type ReadingNotFound = components["schemas"]["NotFoundOut"];
export type ReadingCheck = components["schemas"]["CheckOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** The form a Reading feeds, as the API names it. */
export const INCOME_STATEMENT_TARGET = "tax.income_statement";

const rawBody = (body: unknown) => body as BodyInit;

/**
 * Read a PDF against the Document profiles for `target`. Read-only: the server
 * stores nothing. The file travels as the raw request body, its Content-Type the media type.
 */
export async function readDocument(
  file: File,
  target: string,
  financialYear: number | null,
): Promise<Reading> {
  const { data, error, response } = await client.POST("/api/documents/read", {
    params: {
      query: {
        target,
        financial_year: financialYear ?? undefined,
      },
    },
    body: file as unknown as string,
    bodySerializer: rawBody,
    headers: { "Content-Type": "application/pdf" },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

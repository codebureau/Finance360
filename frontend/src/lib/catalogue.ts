import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";

export type Catalogue = components["schemas"]["CatalogueOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchCatalogue(): Promise<Catalogue> {
  const { data, response } = await client.GET("/api/catalogue");
  if (!data) throw new Error(`Request failed (${response.status})`);
  return data;
}

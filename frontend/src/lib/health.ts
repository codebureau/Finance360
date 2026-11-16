import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";

export type Health = components["schemas"]["Health"];

// Same-origin in production (FastAPI serves the UI); Vite proxies in dev.
// `fetch` is resolved per call (not captured at import) so tests can stub it.
const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchHealth(): Promise<Health> {
  const { data, response } = await client.GET("/health");
  if (!data) throw new Error(`Health check failed (${response.status})`);
  return data;
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import createClient from "openapi-fetch";
import { useCallback } from "react";

import type { paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

/**
 * Display preferences (ADR-0031 section 3): which form a chart takes, remembered per Login in the
 * instance. The server keeps opaque keys and short values; this module owns what they mean, and
 * ignores a value the view no longer offers.
 */

export type Preferences = Record<string, string>;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export const PREFERENCES_KEY = ["preferences"] as const;

async function fetchPreferences(): Promise<Preferences> {
  const { data, error, response } = await client.GET("/api/preferences");
  if (!data || typeof data.preferences !== "object") throw toApiError(error, response.status);
  return data.preferences;
}

async function putPreferences(changes: Record<string, string | null>): Promise<Preferences> {
  const { data, error, response } = await client.PUT("/api/preferences", {
    body: { preferences: changes },
  });
  if (!data) throw toApiError(error, response.status);
  return data.preferences;
}

/** The key under which a shape's default is stored (`default.share`). */
export const defaultKey = (shape: string) => `default.${shape}`;

/**
 * The form a view shows, first match wins: the view's own stored choice, then the stored default
 * for the shape, then the view's first offered form. Each is used only if the view offers it.
 */
export function resolveForm<F extends string>(
  prefs: Preferences,
  viewKey: string,
  shape: string,
  allowed: readonly F[],
): F {
  const offered = (value: string | undefined): value is F => allowed.includes(value as F);
  const own = prefs[viewKey];
  if (offered(own)) return own;
  const fallback = prefs[defaultKey(shape)];
  return offered(fallback) ? fallback : allowed[0];
}

/** The form a view would take with no choice of its own (the stored default, else the first). */
export function defaultForm<F extends string>(prefs: Preferences, shape: string, allowed: readonly F[]): F {
  return resolveForm(prefs, "", shape, allowed);
}

/** The Login's map, read once and kept. A failed read leaves every view on its default form. */
export function useDisplayPreferences() {
  return useQuery({
    queryKey: PREFERENCES_KEY,
    queryFn: fetchPreferences,
    staleTime: Infinity,
    retry: false,
  });
}

/**
 * The form of one view. `choose` stores the view's choice only; `makeDefault` (shown when
 * `canUseAsDefault`) stores the shape's default. Both update at once and save in the background;
 * if the read or a save fails the choice still holds for this page view.
 */
export function useDisplayPreference<F extends string>(viewKey: string, shape: string, allowed: readonly F[]) {
  const query = useDisplayPreferences();
  const queryClient = useQueryClient();
  const prefs = query.data ?? {};
  const save = useMutation({
    mutationFn: putPreferences,
    onMutate: (changes) => {
      queryClient.setQueryData<Preferences>(PREFERENCES_KEY, (old) => {
        const next = { ...(old ?? {}) };
        for (const [key, value] of Object.entries(changes)) {
          if (value === null) delete next[key];
          else next[key] = value;
        }
        return next;
      });
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: PREFERENCES_KEY }),
  });
  const { mutate } = save;
  const form = resolveForm(prefs, viewKey, shape, allowed);
  const choose = useCallback((next: F) => mutate({ [viewKey]: next }), [mutate, viewKey]);
  const makeDefault = useCallback(() => mutate({ [defaultKey(shape)]: form }), [mutate, shape, form]);
  return {
    form,
    /** False until the map has been read (or the read failed): a chart waits as for its data. */
    ready: !query.isPending,
    choose,
    makeDefault,
    canUseAsDefault: form !== defaultForm(prefs, shape, allowed),
  };
}

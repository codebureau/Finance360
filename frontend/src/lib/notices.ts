import { useSyncExternalStore } from "react";
import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";
import { formatMoney } from "@/lib/money";

export type Notice = components["schemas"]["NoticeOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export const NOTICES_KEY = ["notices"] as const;
/** Home shows the last ten undismissed (ADR-0033 section 6). */
export const HOME_NOTICES = [...NOTICES_KEY, "home"] as const;
export const ALL_NOTICES = [...NOTICES_KEY, "all"] as const;

export async function fetchNotices(includeDismissed: boolean, limit: number): Promise<Notice[]> {
  const { data, error, response } = await client.GET("/api/notices", {
    params: { query: { include_dismissed: includeDismissed, limit } },
  });
  if (!data) throw toApiError(error, response.status);
  return data.items;
}

export async function dismissNotice(id: number): Promise<void> {
  const { error, response } = await client.POST("/api/notices/{notice_id}/dismiss", {
    params: { path: { notice_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/**
 * The exact net change as a sign and a figure ("+1,200.00"), from the decimal string the API
 * sends: never a float. `null` when the Notice carries no figure (only a Question changed).
 */
export function signedDelta(notice: Pick<Notice, "delta">): string | null {
  const delta = notice.delta;
  if (!delta) return null;
  const plain = delta.amount.replace(/^-/, "");
  return `${delta.minor < 0 ? "-" : "+"}${formatMoney(plain, delta.currency)}`;
}

// A tiny store for the toast: any write that returns a Notice announces it with `announce`, and the
// one `NoticeToaster` in the shell shows it. No provider, so a screen under test needs nothing.
let current: Notice | null = null;
const listeners = new Set<() => void>();

export function announce(notice: Notice | null | undefined): void {
  if (!notice) return;
  current = notice;
  listeners.forEach((l) => l());
}

export function clearAnnouncement(): void {
  current = null;
  listeners.forEach((l) => l());
}

export function useAnnouncement(): Notice | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}

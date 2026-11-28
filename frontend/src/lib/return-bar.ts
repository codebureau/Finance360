import { FROM_WHATS_DUE } from "@/lib/due";

/**
 * The return mechanism (ADR-0032 section 3): a fix link carries `?from=due` (What's due) or
 * `?from=q.<key>` (a Question). The shell remembers the marker per browser tab, so the way back
 * survives moving within the fix and a refresh, which the URL marker alone does not.
 */
const SLOT = "finance360.return";

/** A marker this app writes: `due`, or `q.` and a Question key (a slug). Anything else is ignored. */
export function parseMarker(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  return value === FROM_WHATS_DUE || /^q\.[a-z0-9-]+$/.test(value) ? value : undefined;
}

export function readMarker(): string | undefined {
  try {
    return parseMarker(window.sessionStorage.getItem(SLOT));
  } catch {
    return undefined; // storage blocked: the URL marker still works for one screen
  }
}

export function writeMarker(marker: string | undefined): void {
  try {
    if (marker) window.sessionStorage.setItem(SLOT, marker);
    else window.sessionStorage.removeItem(SLOT);
  } catch {
    // storage blocked: nothing to remember it in
  }
}

/** The screen a marker returns to: Home for What's due, the Question page for a Question. */
export function markerPath(marker: string): string {
  return marker === FROM_WHATS_DUE ? "/" : `/questions/${marker.slice(2)}`;
}

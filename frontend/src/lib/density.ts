export type Density = "normal" | "compact";

const KEY = "density";

/** The saved density; storage can be blocked (private mode), so any failure means "normal". */
export function readDensity(): Density {
  try {
    return localStorage.getItem(KEY) === "compact" ? "compact" : "normal";
  } catch {
    return "normal";
  }
}

export function applyDensity(density: Density): void {
  document.documentElement.dataset.density = density;
}

/** Applies the density to `<html>` and persists it (best effort) like the theme. */
export function saveDensity(density: Density): void {
  applyDensity(density);
  try {
    localStorage.setItem(KEY, density);
  } catch {
    /* storage unavailable: the choice lasts for this page view only */
  }
}

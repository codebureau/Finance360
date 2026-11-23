import { useEffect, useState } from "react";

import type { ChartTheme } from "@/lib/history-chart";

/** The current theme, following the header toggle (it flips the ``dark`` class on <html>). */
export function useChartTheme(): ChartTheme {
  const read = (): ChartTheme => (document.documentElement.classList.contains("dark") ? "dark" : "light");
  const [theme, setTheme] = useState<ChartTheme>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

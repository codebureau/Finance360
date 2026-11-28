import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom has no canvas: every test gets a stand-in for the ECharts host. A test that inspects the
// option it was given mocks "@/components/echart" itself (position.test.tsx, exposure.test.tsx).
vi.mock("@/components/echart", () => ({
  default: ({ label }: { label: string }) => createElement("div", { "data-testid": "mock-echart", role: "img", "aria-label": label }),
}));

// Newer Node versions ship an experimental global localStorage that shadows jsdom's; use a
// plain in-memory Storage so tests behave the same everywhere.
beforeEach(() => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
  });
  // The same for the per-tab return marker (ADR-0032): every test starts with none.
  const tab = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => tab.get(k) ?? null,
    setItem: (k: string, v: string) => void tab.set(k, String(v)),
    removeItem: (k: string) => void tab.delete(k),
    clear: () => tab.clear(),
  });
});

// Radix popper/menus measure with ResizeObserver, which jsdom lacks.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

if (typeof window !== "undefined") window.scrollTo = () => {};

afterEach(cleanup);

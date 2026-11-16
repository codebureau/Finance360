import { render, screen } from "@testing-library/react";
import { createMemoryHistory, RouterProvider, createRootRoute, createRouter } from "@tanstack/react-router";
import { expect, test } from "vitest";

import { Markdown, parseBlocks } from "@/components/markdown";

test("parseBlocks reads headings, paragraphs, lists, callouts and block directives", () => {
  const blocks = parseBlocks(
    ["## Heading", "", "One line", "and its next.", "", "- first", "  continued", "- second", "", "1. a", "2. b", "", "> note", "", ":::pieces"].join(
      "\n",
    ),
  );
  expect(blocks).toEqual([
    { t: "h", level: 2, text: "Heading" },
    { t: "p", text: "One line and its next." },
    { t: "ul", items: ["first continued", "second"] },
    { t: "ol", items: ["a", "b"] },
    { t: "quote", text: "note" },
    { t: "block", name: "pieces" },
  ]);
});

test("inline markup becomes bold, code and links; outside links open in a new tab", async () => {
  const root = createRootRoute({
    component: () => (
      <Markdown source={"A **bold** word, `code`, [Tax](/tax) and [out](https://example.test/x)."} />
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterProvider router={router} />);
  expect((await screen.findByText("bold")).tagName).toBe("STRONG");
  expect(screen.getByText("code").tagName).toBe("CODE");
  expect(screen.getByRole("link", { name: "Tax" })).toHaveAttribute("href", "/tax");
  const out = screen.getByRole("link", { name: "out" });
  expect(out).toHaveAttribute("href", "https://example.test/x");
  expect(out).toHaveAttribute("target", "_blank");
});

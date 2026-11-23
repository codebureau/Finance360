import { expect, test } from "vitest";

import { defaultForm, resolveForm } from "@/lib/display-preferences";

const FORMS = ["donut", "stacked-bar", "bars"] as const;

test("the view's own choice wins, then the shape's default, then the first offered form", () => {
  expect(resolveForm({ "a.view": "bars", "default.share": "stacked-bar" }, "a.view", "share", FORMS)).toBe("bars");
  expect(resolveForm({ "default.share": "stacked-bar" }, "a.view", "share", FORMS)).toBe("stacked-bar");
  expect(resolveForm({}, "a.view", "share", FORMS)).toBe("donut");
});

test("a value the view does not offer is ignored and falls to the next layer", () => {
  expect(resolveForm({ "a.view": "hexagon", "default.share": "bars" }, "a.view", "share", FORMS)).toBe("bars");
  expect(resolveForm({ "a.view": "hexagon", "default.share": "also-gone" }, "a.view", "share", FORMS)).toBe("donut");
  // a view that narrowed its forms: the stored default is only used if the view offers it
  expect(resolveForm({ "default.share": "donut" }, "a.view", "share", ["bars"] as const)).toBe("bars");
});

test("the default form is the stored default where offered, else the first form", () => {
  expect(defaultForm({ "default.share": "bars" }, "share", FORMS)).toBe("bars");
  expect(defaultForm({ "a.view": "bars" }, "share", FORMS)).toBe("donut");
});

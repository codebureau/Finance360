import { expect, test } from "vitest";

import { toneOf } from "@/lib/entity-colour";

test("a Category and an asset class keep their fixed colour, whatever the order, and Cash is teal", () => {
  const categories = ["investments", "property", "cash", "superannuation", "overseas_pensions", "vehicles"];
  expect(categories.map((k) => toneOf("category", k))).toEqual([0, 1, 2, 3, 4, 5].map((token) => ({ token })));
  expect(toneOf("category", "cash", 5)).toEqual({ token: 2 }); // order is ignored for a fixed key
  expect(toneOf("asset-class", "cash")).toEqual({ token: 2 });
  expect(toneOf("asset-class", "property")).toEqual(toneOf("category", "property")); // matched on purpose
});

test("a key with no fixed colour takes its stable order modulo six, and never a seventh hue", () => {
  expect(toneOf("category", "boat", 6)).toEqual({ token: 0 });
  expect(toneOf("member", "7", 1)).toEqual({ token: 1 });
  expect(toneOf("dimension", "usd", 13)).toEqual({ token: 1 });
  expect(toneOf("scenario", "likely", 2)).toEqual({ token: 2 });
});

test("other and anything with no place are muted, never a chart token", () => {
  expect(toneOf("dimension", "other", 3)).toBe("muted");
  expect(toneOf("asset-class", "alternatives")).toBe("muted");
  expect(toneOf("dimension", "x", -1)).toBe("muted");
});

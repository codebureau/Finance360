import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { areaOf } from "@/components/area-chip";
import { PageHeader } from "@/components/ui/page-header";
import { SIDEBAR_GROUPS } from "@/components/sidebar-nav";

test("each sidebar group has a fixed token (ADR-0031 section 5) and Reference and system stays muted", () => {
  expect(Object.fromEntries(SIDEBAR_GROUPS.map((g) => [g.heading, g.tone]))).toEqual({
    "Where we stand": 0,
    "Where we're heading": 3,
    "This tax year": 5,
    "Keep it current": 2,
    "Your documents": 1,
    "Reference and system": null,
  });
});

test("a page takes the group of the longest matching sidebar item, and exact items only on their own path", () => {
  expect(areaOf("/position")?.item.label).toBe("Position");
  expect(areaOf("/accounts/4")?.item.label).toBe("Accounts");
  expect(areaOf("/accounts/4")?.tone).toBe(2);
  expect(areaOf("/tax/records/9")?.item.label).toBe("Tax records");
  expect(areaOf("/tax")?.item.label).toBe("Tax estimate");
  expect(areaOf("/audit")?.tone).toBeNull();
  expect(areaOf("/login")).toBeNull();
});

test("a header shown outside the router has no chip and still renders its title", () => {
  render(<PageHeader title="Anywhere" />);
  expect(screen.getByRole("heading", { name: "Anywhere" })).toBeInTheDocument();
  expect(screen.queryByTestId("area-chip")).not.toBeInTheDocument();
});

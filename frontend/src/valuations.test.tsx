import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { HouseholdPage } from "@/components/household-page";
import { todayIn } from "@/lib/household";
import { describeAmount, groupDigits } from "@/lib/valuations";
import { everyday, fakeBackend, json, MEMBERS, valuation, type A } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** The Account detail page's Valuations section (rows have one overflow menu, opened by keyboard). */
async function openDetail(account: A = everyday) {
  const view = await renderAppAt(`/accounts/${account.id}`);
  const region = await screen.findByRole("region", { name: `Valuations of ${account.name}` });
  return { ...view, region: within(region) };
}

async function openRowMenu(asAt: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for Valuation of ${asAt}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

test("amounts are grouped as strings, in the Account's currency, without floats", () => {
  expect(groupDigits("1234567.50")).toBe("1,234,567.50");
  expect(groupDigits("-1234.05")).toBe("-1,234.05");
  expect(groupDigits("999")).toBe("999");
  expect(groupDigits("12345678901234567890.12")).toBe("12,345,678,901,234,567,890.12");
  expect(describeAmount({ amount: "250000.99", currency: "GBP" })).toBe("GBP 250,000.99");
});

test("todayIn is the date in the Household time zone, not the UTC date", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-17T20:00:00Z"));
  expect(todayIn("Australia/Melbourne")).toBe("2026-10-18");
  expect(todayIn("UTC")).toBe("2026-10-17");
  expect(todayIn("Pacific/Honolulu")).toBe("2026-10-17");
});

test("the history shows each Valuation with source and provenance, superseded ones badged", async () => {
  fakeBackend([everyday], {
    valuations: [
      valuation(2, { as_at: "2026-02-28", amount: "1234.56", source: "manual", provenance_note: "Checked the app" }),
      valuation(3, {
        as_at: "2026-01-31",
        status: "corrected",
        superseded_at: "2026-02-02T00:00:00",
        superseded_by_id: 9,
        amount: "99.00",
      }),
      valuation(1, { as_at: "2026-01-31" }),
    ],
  });
  const { region } = await openDetail();
  expect(await region.findByText("1,234.56")).toBeInTheDocument();
  expect(region.getByText("Checked the app")).toBeInTheDocument();
  expect(region.getByText("manual")).toBeInTheDocument();
  expect(region.getAllByText("January statement")).toHaveLength(2);
  const badge = region.getByText("Superseded: corrected 2026-02-02");
  expect(badge).toHaveClass("text-muted-foreground"); // the `superseded` Badge variant
  expect(region.getByText("99.00")).toBeInTheDocument(); // the original stays visible
  // Only the current rows offer actions.
  expect(region.getAllByRole("button", { name: /^Actions for/ })).toHaveLength(2);
});

test("a foreign Account's amounts are shown and entered in its own currency", async () => {
  const uk: A = { ...everyday, name: "UK Pension", currency: "GBP" };
  fakeBackend([uk], {
    valuations: [valuation(1, { currency: "GBP", amount: "250000.99", provenance_note: "Provider statement" })],
  });
  const { region } = await openDetail(uk);
  expect(await region.findByText("GBP 250,000.99")).toBeInTheDocument();
  fireEvent.click(region.getByRole("button", { name: "Add Valuation" }));
  const form = within(await screen.findByRole("form", { name: "Add Valuation to UK Pension" }));
  expect(form.getByText("Amount (GBP)")).toBeInTheDocument();
});

test("an empty history says there is no value, not zero", async () => {
  fakeBackend([everyday]);
  const { region } = await openDetail();
  expect(await region.findByText(/no value \(not zero\)/)).toBeInTheDocument();
});

test("a long history shows the latest 10 and offers all of them", async () => {
  const many = Array.from({ length: 12 }, (_, i) =>
    valuation(i + 1, { as_at: `2026-01-${String(i + 1).padStart(2, "0")}` }),
  );
  fakeBackend([everyday], { valuations: many });
  const { region } = await openDetail();
  await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(11)); // header + 10
  fireEvent.click(region.getByRole("button", { name: "Show all 12 Valuations" }));
  expect(region.getAllByRole("row")).toHaveLength(13);
});

test("adding a Valuation opens a Dialog, defaults to today in the Household zone and sends the form", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-17T20:00:00Z"));
  const calls = fakeBackend([everyday]);
  const { region } = await openDetail();
  fireEvent.click(await region.findByRole("button", { name: "Add Valuation" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a Valuation to Everyday account" });
  const form = within(within(dialog).getByRole("form", { name: "Add Valuation to Everyday account" }));
  await waitFor(() => expect(form.getByLabelText("As at")).toHaveValue("2026-10-18"));
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "42.50" } });
  fireEvent.change(form.getByLabelText("Source"), { target: { value: "estimate" } });
  fireEvent.change(form.getByLabelText("Provenance note"), { target: { value: "Rough guess" } });
  fireEvent.click(form.getByRole("button", { name: "Add Valuation" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/valuations"))).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    as_at: "2026-10-18",
    amount: "42.50",
    source: "estimate",
    provenance_note: "Rough guess",
    currency: "AUD",
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("a rejected Valuation shows the field message and the Dialog stays open", async () => {
  fakeBackend([everyday]);
  const { region } = await openDetail();
  fireEvent.click(await region.findByRole("button", { name: "Add Valuation" }));
  const form = within(await screen.findByRole("form", { name: "Add Valuation to Everyday account" }));
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "1" } });
  fireEvent.click(form.getByRole("button", { name: "Add Valuation" }));
  expect(await screen.findByText("Say where this figure came from.")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("correcting opens a Dialog from the row menu; the original stays visible as superseded", async () => {
  const calls = fakeBackend([everyday], { valuations: [valuation(1, { amount: "100.00" })] });
  const { region } = await openDetail();
  await openRowMenu("2026-01-31");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Correct Valuation" }));
  const form = within(await screen.findByRole("form", { name: "Correct Valuation of Everyday account" }));
  expect(form.getByLabelText("Amount (AUD)")).toHaveValue("100.00");
  fireEvent.change(form.getByLabelText("Amount (AUD)"), { target: { value: "110.00" } });
  fireEvent.click(form.getByRole("button", { name: "Save correction" }));
  expect(await region.findByText("110.00")).toBeInTheDocument();
  expect(region.getByText("100.00")).toBeInTheDocument();
  expect(region.getByText(/Superseded: corrected/)).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT" && c.path === "/api/valuations/1")?.body?.amount).toBe("110.00");
});

test("removing confirms in an AlertDialog naming it; Cancel changes nothing, confirming supersedes it", async () => {
  const calls = fakeBackend([everyday], { valuations: [valuation(1, {})] });
  const { region } = await openDetail();
  const trigger = await openRowMenu("2026-01-31");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Valuation" }));
  const alert = await screen.findByRole("alertdialog", {
    name: "Remove the 2026-01-31 Valuation of Everyday account?",
  });
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  await waitFor(() => expect(trigger).toHaveFocus());

  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove Valuation" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove Valuation" }));
  expect(await region.findByText(/Superseded: removed/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/valuations/1")).toBe(true);
});

test("the Household page shows the time zone and changes it", async () => {
  const calls: { method: string; body?: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const body = request.method === "PUT" ? ((await request.json()) as Record<string, unknown>) : undefined;
      calls.push({ method: request.method, body });
      if (request.method === "PUT") {
        if (body?.time_zone === "Mars/Base")
          return json({ errors: [{ field: "time_zone", message: "Enter an IANA time zone name." }] }, 422);
        return json({ name: "The Sample Household", time_zone: body?.time_zone, members: MEMBERS });
      }
      return json({ name: "The Sample Household", time_zone: "Australia/Melbourne", members: MEMBERS });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HouseholdPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Australia/Melbourne")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Change time zone" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Change time zone" }));
  fireEvent.change(dialog.getByLabelText(/^Time zone/), { target: { value: "Mars/Base" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Enter an IANA time zone name.")).toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText(/^Time zone/), { target: { value: "Pacific/Auckland" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({
      name: "The Sample Household",
      time_zone: "Pacific/Auckland",
    }),
  );
});

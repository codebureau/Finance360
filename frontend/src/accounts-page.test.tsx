import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { readDensity, saveDensity } from "@/lib/density";
import { everyday, fakeBackend, homeLoan, oldCar, shared, valuation } from "@/test-accounts-backend";
import { renderAppAt } from "@/test-utils";

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => {
  document.documentElement.removeAttribute("data-density");
});

async function openNewSheet() {
  const add = await screen.findByRole("button", { name: "Add account" });
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  const sheet = await screen.findByRole("dialog", { name: "New Account" });
  return { sheet, form: within(await within(sheet).findByRole("form", { name: "New Account" })) };
}

async function openMenu() {
  const trigger = await screen.findByRole("button", { name: "More actions" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

test("lists open Accounts as a table with no per-row buttons; closed ones appear via the URL filter", async () => {
  const calls = fakeBackend([everyday, shared, oldCar]);
  const { router } = await renderAppAt("/accounts");
  const table = await screen.findByRole("table", { name: "Accounts" });
  expect(await within(table).findByText("Everyday account")).toBeInTheDocument();
  expect(within(table).getByText("Member: Alex Sample")).toBeInTheDocument();
  expect(within(table).getByText("Joint: Alex Sample 50%, Jordan Sample 50%")).toBeInTheDocument();
  expect(within(table).getByText("emergency fund")).toBeInTheDocument();
  expect(within(table).queryByText("Old car")).not.toBeInTheDocument();
  // The only button in the list page content is the single primary action and the filter.
  expect(within(table).queryAllByRole("button")).toHaveLength(0);
  expect(screen.getAllByRole("button", { name: "Add account" })).toHaveLength(1);

  fireEvent.click(screen.getByLabelText("Show closed"));
  expect(await screen.findByText("Old car")).toBeInTheDocument();
  expect(screen.getByText("Closed 2025-01-01")).toBeInTheDocument();
  expect(calls.some((c) => c.search.includes("include_closed=true"))).toBe(true);
  expect(router.state.location.search).toEqual({ closed: true });
});

test("a deep link with ?closed=true lists closed Accounts with the filter ticked", async () => {
  fakeBackend([everyday, oldCar]);
  await renderAppAt("/accounts?closed=true");
  expect(await screen.findByText("Old car")).toBeInTheDocument();
  expect(screen.getByLabelText("Show closed")).toBeChecked();
});

test("clicking a row (not just the name) opens the detail page, and back returns to the list", async () => {
  fakeBackend([everyday, shared], { valuations: [valuation(1, {})] });
  const { router } = await renderAppAt("/accounts");
  const row = (await screen.findByText("Joint: Alex Sample 50%, Jordan Sample 50%")).closest("tr")!;
  fireEvent.click(row);
  expect(await screen.findByRole("heading", { name: "Joint savings" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/accounts/11");

  act(() => router.history.back());
  await waitFor(() => expect(router.state.location.pathname).toBe("/accounts"));
  expect(await screen.findByRole("table", { name: "Accounts" })).toBeInTheDocument();
});

test("the detail page loads by deep link with details, Valuations, identifiers and an audit link", async () => {
  fakeBackend([shared], {
    valuations: [valuation(1, { account_id: 11, amount: "1234.56", provenance_note: "Checked the app" })],
  });
  await renderAppAt("/accounts/11");
  expect(await screen.findByRole("heading", { name: "Joint savings" })).toBeInTheDocument();
  expect(screen.getByText(/Cash · Savings account|Cash · Transaction account/)).toBeInTheDocument();
  expect(screen.getByText("Joint: Alex Sample 50%, Jordan Sample 50%")).toBeInTheDocument();
  expect(screen.getByText("emergency fund")).toBeInTheDocument();
  const history = within(await screen.findByRole("table", { name: "Valuation history" }));
  expect(await history.findByText("1,234.56")).toBeInTheDocument();
  expect(history.getByText("Checked the app")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Identifiers" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View audit trail for Joint savings/ })).toHaveAttribute(
    "href",
    "/audit?account_id=11",
  );
  // No inline edit form and no bare Close button on the page.
  expect(screen.queryByRole("form")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Close/ })).not.toBeInTheDocument();
});

test("an unknown Account says so and links back", async () => {
  fakeBackend([everyday]);
  await renderAppAt("/accounts/999");
  expect(await screen.findByRole("heading", { name: "Account not found" })).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Accounts" }).length).toBeGreaterThan(0);
});

test("a joint Account defaults to 50/50, saves 60/40 and rejects 60/50 without calling the API", async () => {
  const calls = fakeBackend([everyday]);
  await renderAppAt("/accounts");
  const { form } = await openNewSheet();
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Shared pot" } });
  fireEvent.click(form.getByLabelText("Joint"));
  const alex = form.getByLabelText("Alex Sample share (%)") as HTMLInputElement;
  const jordan = form.getByLabelText("Jordan Sample share (%)") as HTMLInputElement;
  expect([alex.value, jordan.value]).toEqual(["50", "50"]);

  fireEvent.change(alex, { target: { value: "60" } });
  fireEvent.click(form.getByRole("button", { name: "Create Account" }));
  expect(await form.findByText("Shares must add up to 100% (they add up to 110%).")).toBeInTheDocument();
  expect(calls.some((c) => c.method === "POST")).toBe(false);

  fireEvent.change(jordan, { target: { value: "40" } });
  fireEvent.click(form.getByRole("button", { name: "Create Account" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
    name: "Shared pot",
    ownership: "joint",
    currency: "AUD",
    owners: [
      { member_id: 1, share_bp: 6000 },
      { member_id: 2, share_bp: 4000 },
    ],
  });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New Account" })).toBeNull());
});

test("the Account type choices follow the chosen Category", async () => {
  fakeBackend([]);
  await renderAppAt("/accounts");
  const { form } = await openNewSheet();
  const types = () =>
    within(form.getByLabelText("Account type") as HTMLElement)
      .getAllByRole("option")
      .map((o) => o.textContent);
  expect(types()).toEqual(["Transaction account", "Savings account"]);
  fireEvent.change(form.getByLabelText("Category"), { target: { value: "vehicles" } });
  expect(types()).toEqual(["Vehicle"]);
});

test("Tags autocomplete from existing Tags and new ones are sent with the Account", async () => {
  const calls = fakeBackend([]);
  await renderAppAt("/accounts");
  const { form } = await openNewSheet();
  await waitFor(() =>
    expect(
      Array.from(document.querySelectorAll("#tag-suggestions option")).map((o) => o.getAttribute("value")),
    ).toEqual(["emergency fund", "everyday", "retirement"]),
  );
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Tagged" } });
  fireEvent.change(form.getByLabelText("Add a Tag"), { target: { value: "retirement" } });
  fireEvent.click(form.getByRole("button", { name: "Add Tag" }));
  fireEvent.change(form.getByLabelText("Add a Tag"), { target: { value: "brand new" } });
  fireEvent.click(form.getByRole("button", { name: "Create Account" })); // pending text is kept
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(calls.find((c) => c.method === "POST")?.body?.tags).toEqual(["retirement", "brand new"]);
});

test("a server rejection shows beside the field and the Sheet stays open", async () => {
  fakeBackend([], { rejectCreate: true });
  await renderAppAt("/accounts");
  const { form } = await openNewSheet();
  fireEvent.click(form.getByRole("button", { name: "Create Account" }));
  expect(await form.findByText("Account name is required.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "New Account" })).toBeInTheDocument();
});

test("Edit account opens a Sheet with the stored shares; Cancel discards, Save sends and updates the page", async () => {
  const calls = fakeBackend([shared]);
  await renderAppAt("/accounts/11");
  const edit = await screen.findByRole("button", { name: "Edit account" });
  await waitFor(() => expect(edit).toBeEnabled());

  // Cancel changes nothing and returns focus to the trigger.
  fireEvent.click(edit);
  let form = within(await screen.findByRole("form", { name: "Edit Joint savings" }));
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Throwaway" } });
  fireEvent.click(form.getByRole("button", { name: "Cancel" }));
  // something was typed, so Cancel asks before throwing it away
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit Joint savings" })).toBeNull());
  expect(calls.some((c) => c.method === "PUT")).toBe(false);
  await waitFor(() => expect(edit).toHaveFocus());

  fireEvent.click(edit);
  form = within(await screen.findByRole("form", { name: "Edit Joint savings" }));
  expect(form.getByLabelText("Name")).toHaveValue("Joint savings"); // fresh, not "Throwaway"
  expect((form.getByLabelText("Alex Sample share (%)") as HTMLInputElement).value).toBe("50");
  fireEvent.change(form.getByLabelText("Alex Sample share (%)"), { target: { value: "70" } });
  fireEvent.change(form.getByLabelText("Jordan Sample share (%)"), { target: { value: "30" } });
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "Renamed savings" } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("heading", { name: "Renamed savings" })).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({
    name: "Renamed savings",
    owners: [
      { member_id: 1, share_bp: 7000 },
      { member_id: 2, share_bp: 3000 },
    ],
  });
  expect(screen.getByText("Joint: Alex Sample 70%, Jordan Sample 30%")).toBeInTheDocument();
});

test("a failed edit keeps the Sheet open with the message beside the field", async () => {
  fakeBackend([everyday]);
  await renderAppAt("/accounts/10");
  const edit = await screen.findByRole("button", { name: "Edit account" });
  await waitFor(() => expect(edit).toBeEnabled());
  fireEvent.click(edit);
  const form = within(await screen.findByRole("form", { name: "Edit Everyday account" }));
  fireEvent.change(form.getByLabelText("Name"), { target: { value: "" } });
  fireEvent.click(form.getByRole("button", { name: "Save" }));
  expect(await form.findByText("Account name is required.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Edit Everyday account" })).toBeInTheDocument();
});

test("Close account is in the overflow menu and cannot proceed without the AlertDialog; Cancel leaves it open", async () => {
  const calls = fakeBackend([everyday]);
  await renderAppAt("/accounts/10");
  const trigger = await openMenu();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Close account" }));
  const alert = within(await screen.findByRole("alertdialog", { name: "Close Everyday account?" }));
  expect(calls.some((c) => c.path.endsWith("/close"))).toBe(false); // opening the menu closed nothing
  fireEvent.click(alert.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.path.endsWith("/close"))).toBe(false);
  expect(screen.queryByText(/^Closed /)).not.toBeInTheDocument();
  await waitFor(() => expect(trigger).toHaveFocus());

  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Close account" }));
  const confirm = within(await screen.findByRole("alertdialog"));
  fireEvent.change(confirm.getByLabelText("Closed on"), { target: { value: "2026-06-30" } });
  fireEvent.click(confirm.getByRole("button", { name: "Close account" }));
  expect(await screen.findByText("Closed 2026-06-30")).toBeInTheDocument();
  expect(calls.find((c) => c.path === "/api/accounts/10/close")?.body).toEqual({
    closed_on: "2026-06-30",
    confirm_non_zero: false,
  });
  // Once closed the menu no longer offers it again.
  fireEvent.keyDown(await screen.findByRole("button", { name: "More actions" }), { key: "Enter" });
  expect(await screen.findByRole("menuitem", { name: "Already closed" })).toBeInTheDocument();
});

test("closing over a non-zero balance asks 'Close anyway?' inside the same AlertDialog", async () => {
  const calls = fakeBackend([everyday], { needsConfirm: true });
  await renderAppAt("/accounts/10");
  await openMenu();
  fireEvent.click(await screen.findByRole("menuitem", { name: "Close account" }));
  const alert = within(await screen.findByRole("alertdialog"));
  fireEvent.click(alert.getByRole("button", { name: "Close account" }));
  expect(await alert.findByText(/not zero/)).toBeInTheDocument();
  expect(alert.getByText("Close anyway?")).toBeInTheDocument();
  expect(screen.getByRole("alertdialog")).toBeInTheDocument(); // still open, nothing closed yet
  fireEvent.click(alert.getByRole("button", { name: "Close anyway" }));
  await waitFor(() =>
    expect(calls.filter((c) => c.path.endsWith("/close")).map((c) => c.body?.confirm_non_zero)).toEqual([
      false,
      true,
    ]),
  );
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
});

test("a liability's Valuation is shown negative with a minus sign", async () => {
  fakeBackend([homeLoan], { valuations: [valuation(1, { account_id: 13, amount: "250000.00" })] });
  await renderAppAt("/accounts/13");
  const amount = await screen.findByText("-250,000.00");
  expect(amount).toHaveAttribute("data-negative", "true");
});

test("Settings density toggle sets data-density on <html> and persists it", async () => {
  fakeBackend([]);
  await renderAppAt("/settings");
  const compact = await screen.findByRole("button", { name: "Compact" });
  fireEvent.click(compact);
  expect(document.documentElement.dataset.density).toBe("compact");
  expect(localStorage.getItem("density")).toBe("compact");
  expect(compact).toHaveAttribute("aria-pressed", "true");
  expect(readDensity()).toBe("compact");
  fireEvent.click(screen.getByRole("button", { name: "Normal" }));
  expect(document.documentElement.dataset.density).toBe("normal");
  expect(readDensity()).toBe("normal");
});

test("density survives blocked storage: reads fall back to normal and saving does not throw", () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  });
  expect(readDensity()).toBe("normal");
  expect(() => saveDensity("compact")).not.toThrow();
  expect(document.documentElement.dataset.density).toBe("compact"); // applied for this page view
});

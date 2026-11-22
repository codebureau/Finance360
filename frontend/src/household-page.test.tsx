import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { HouseholdPage } from "@/components/household-page";

type M = { id: number; name: string; date_of_birth: string };
type Call = { method: string; path: string; body?: Record<string, unknown> };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** A tiny in-memory backend behind fetch, with the API's error shapes. */
function fakeBackend(seed: M[]) {
  let members = [...seed];
  let household = { name: "The Sample Household", time_zone: "Australia/Melbourne" };
  let nextId = 100;
  // The Household facts as stored; the fake resolves defaults the way the service does.
  const stored: { couple_status: string | null; homeowner: boolean | null; principal: number | null } = {
    couple_status: null,
    homeowner: null,
    principal: null,
  };
  const homes = [
    { id: 7, name: "Family Home" },
    { id: 8, name: "Sample Unit" },
  ];
  const label = (entered: boolean) => (entered ? "entered" : "default, not entered");
  const facts = () => {
    const homeId = stored.homeowner === false ? null : stored.principal;
    return {
      couple_status: {
        value: stored.couple_status ?? (members.length === 2 ? "couple" : "single"),
        source: stored.couple_status ? "entered" : "default",
        label: label(stored.couple_status !== null),
      },
      homeowner: {
        value: stored.homeowner ?? true,
        source: stored.homeowner === null ? "default" : "entered",
        label: label(stored.homeowner !== null),
      },
      principal_home_account_id: {
        value: homeId,
        source: homeId ? "entered" : "default",
        label: label(homeId !== null),
      },
      principal_home_name: homes.find((h) => h.id === homeId)?.name ?? null,
      stored_couple_status: stored.couple_status,
      stored_homeowner: stored.homeowner,
      stored_principal_home_account_id: stored.principal,
      state: stored.homeowner !== false && homeId === null ? "needs_home_choice" : "ok",
      reasons:
        stored.homeowner !== false && homeId === null
          ? ["Choose which Property Account is the principal home."]
          : [],
      property_accounts: homes,
    };
  };
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body =
        request.method === "GET" || request.method === "DELETE" ? undefined : await request.json();
      calls.push({ method: request.method, path, body });
      const id = Number(path.split("/").pop());
      if (path === "/api/household" && request.method === "GET")
        return json({ ...household, members, facts: facts() });
      if (path === "/api/household/facts" && request.method === "PUT") {
        if ("couple_status" in body) stored.couple_status = body.couple_status;
        if ("homeowner" in body) stored.homeowner = body.homeowner;
        if ("principal_home_account_id" in body) stored.principal = body.principal_home_account_id;
        return json({ ...household, members, facts: facts() });
      }
      if (path === "/api/household" && request.method === "PUT") {
        if (body.time_zone === "Nowhere/Land")
          return json({ errors: [{ field: "time_zone", message: "Not a known time zone." }] }, 422);
        household = { ...household, ...body };
        return json({ ...household, members, facts: facts() });
      }
      if (path === "/api/household/members" && request.method === "POST") {
        if (!body.name.trim())
          return json({ errors: [{ field: "name", message: "Name is required." }] }, 422);
        const m = { id: nextId++, ...body };
        members.push(m);
        return json(m, 201);
      }
      if (request.method === "PUT") {
        members = members.map((m) => (m.id === id ? { ...m, ...body } : m));
        return json(members.find((m) => m.id === id));
      }
      if (request.method === "DELETE") {
        if (id === 1) return json({ message: "Cannot delete Alex Sample: they own an Account." }, 409);
        members = members.filter((m) => m.id !== id);
        return new Response(null, { status: 204 });
      }
      return new Response("?", { status: 500 });
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HouseholdPage />
    </QueryClientProvider>,
  );
}

const SEED: M[] = [
  { id: 1, name: "Alex Sample", date_of_birth: "1984-03-12" },
  { id: 2, name: "Jordan Sample", date_of_birth: "1986-09-30" },
];

afterEach(() => vi.unstubAllGlobals());

async function openRowMenu(name: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for ${name}` });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

test("lists the Household's Members in a table with their dates of birth", async () => {
  fakeBackend(SEED);
  renderPage();
  const table = await screen.findByRole("table", { name: "Members" });
  expect(within(table).getByText("Alex Sample")).toBeInTheDocument();
  expect(within(table).getByText("Born 1984-03-12")).toBeInTheDocument();
  expect(screen.getByText("The Sample Household")).toBeInTheDocument();
  expect(screen.getByText("Australia/Melbourne")).toBeInTheDocument();
  // No inline forms: editing happens in dialogs only.
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("adds a Member from a Dialog and lists it", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  await screen.findByText("Alex Sample");
  fireEvent.click(screen.getByRole("button", { name: "Add Member" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Add Member" }));
  fireEvent.change(dialog.getByLabelText("Name"), { target: { value: "Casey Sample" } });
  fireEvent.change(dialog.getByLabelText("Date of birth"), { target: { value: "1995-02-03" } });
  fireEvent.click(dialog.getByRole("button", { name: "Add Member" }));
  expect(await screen.findByText("Casey Sample")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({
    name: "Casey Sample",
    date_of_birth: "1995-02-03",
  });
});

test("a validation error shows beside the field and the Dialog stays open", async () => {
  fakeBackend(SEED);
  renderPage();
  await screen.findByText("Alex Sample");
  fireEvent.click(screen.getByRole("button", { name: "Add Member" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Add Member" }));
  fireEvent.click(dialog.getByRole("button", { name: "Add Member" }));
  expect(await dialog.findByText("Name is required.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Add Member" })).toBeInTheDocument();
});

test("edits a Member's name in a Dialog opened from the row menu", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  await openRowMenu("Jordan Sample");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Edit Member" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Edit Jordan Sample" }));
  expect(dialog.getByLabelText("Name")).toHaveValue("Jordan Sample");
  fireEvent.change(dialog.getByLabelText("Name"), { target: { value: "Jordan Example" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Jordan Example")).toBeInTheDocument();
  expect(screen.queryByText("Jordan Sample")).not.toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT" && c.path.endsWith("/2"))?.body).toEqual({
    name: "Jordan Example",
    date_of_birth: "1986-09-30",
  });
});

test("renames the Household and changes its time zone in Dialogs", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  await screen.findByText("Alex Sample");
  fireEvent.click(screen.getByRole("button", { name: "Rename Household" }));
  let dialog = within(await screen.findByRole("dialog", { name: "Rename Household" }));
  fireEvent.change(dialog.getByLabelText(/^Household name/), { target: { value: "The Example Household" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("The Example Household")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Change time zone" }));
  dialog = within(await screen.findByRole("dialog", { name: "Change time zone" }));
  fireEvent.change(dialog.getByLabelText(/^Time zone/), { target: { value: "Nowhere/Land" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await dialog.findByText("Not a known time zone.")).toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText(/^Time zone/), { target: { value: "Australia/Perth" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Australia/Perth")).toBeInTheDocument();
  expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({
    name: "The Example Household",
    time_zone: "Australia/Perth",
  });
});

test("deleting asks in an AlertDialog; Cancel changes nothing, confirming deletes", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  const trigger = await openRowMenu("Jordan Sample");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Member" }));
  const alert = await screen.findByRole("alertdialog", { name: "Delete Jordan Sample?" });
  fireEvent.click(within(alert).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  await waitFor(() => expect(trigger).toHaveFocus());

  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Member" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete Member" }));
  await waitFor(() => expect(screen.queryByText("Jordan Sample")).not.toBeInTheDocument());
  expect(screen.getByText("Alex Sample")).toBeInTheDocument();
});

test("explains why a Member who owns an Account could not be deleted", async () => {
  fakeBackend(SEED);
  renderPage();
  await openRowMenu("Alex Sample");
  fireEvent.click(await screen.findByRole("menuitem", { name: "Delete Member" }));
  fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete Member" }));
  expect(await screen.findByText("Cannot delete Alex Sample: they own an Account.")).toBeInTheDocument();
  expect(screen.getByText("Alex Sample")).toBeInTheDocument();
});

test("shows each Household fact with its default label, and asks for a home when there are several", async () => {
  fakeBackend(SEED);
  renderPage();
  expect(await screen.findByText("Household facts")).toBeInTheDocument();
  expect(screen.getByText("Couple")).toBeInTheDocument();
  expect(screen.getByText("Yes")).toBeInTheDocument();
  expect(screen.getAllByText("(default, not entered)")).toHaveLength(3);
  expect(screen.getByText("Needs a choice of home")).toBeInTheDocument();
  expect(screen.getByText("Needs a choice")).toBeInTheDocument();
});

test("chooses the principal home in a Dialog, then resets it to the default", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  await screen.findByText("Household facts");
  fireEvent.click(screen.getByRole("button", { name: "Change principal home" }));
  let dialog = within(await screen.findByRole("dialog", { name: "Change principal home" }));
  fireEvent.change(dialog.getByLabelText("Principal home"), { target: { value: "8" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(await screen.findByText("Sample Unit")).toBeInTheDocument();
  expect(screen.queryByText("Needs a choice of home")).not.toBeInTheDocument();
  expect(calls.filter((c) => c.path === "/api/household/facts").at(-1)?.body).toEqual({
    principal_home_account_id: 8,
  });

  fireEvent.click(screen.getByRole("button", { name: "Change principal home" }));
  dialog = within(await screen.findByRole("dialog", { name: "Change principal home" }));
  expect(dialog.getByLabelText("Principal home")).toHaveValue("8");
  fireEvent.change(dialog.getByLabelText("Principal home"), { target: { value: "" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Needs a choice")).toBeInTheDocument();
  expect(calls.filter((c) => c.path === "/api/household/facts").at(-1)?.body).toEqual({
    principal_home_account_id: null,
  });
});

test("sets homeowner to No, which needs no home, and the facts say entered", async () => {
  const calls = fakeBackend(SEED);
  renderPage();
  await screen.findByText("Household facts");
  fireEvent.click(screen.getByRole("button", { name: "Change homeowner" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Change homeowner" }));
  fireEvent.change(dialog.getByLabelText("Homeowner"), { target: { value: "no" } });
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Not applicable")).toBeInTheDocument();
  expect(screen.getByText("No")).toBeInTheDocument();
  expect(screen.getByText("(entered)")).toBeInTheDocument();
  expect(screen.queryByText("Needs a choice of home")).not.toBeInTheDocument();
  expect(calls.filter((c) => c.path === "/api/household/facts").at(-1)?.body).toEqual({
    homeowner: false,
  });
});

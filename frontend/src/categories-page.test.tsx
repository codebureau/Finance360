import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { CategoriesPage } from "@/components/categories-page";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const type = (key: string, name: string) => ({ key, name, behaviour_key: "simple_balance" });
const CATALOGUE = {
  version: 1,
  classes: [
    {
      class: "asset",
      categories: [
        { key: "cash", name: "Cash", account_types: [type("savings", "Savings account")] },
        { key: "vehicles", name: "Vehicles", account_types: [type("vehicle", "Vehicle")] },
      ],
    },
    {
      class: "liability",
      categories: [
        { key: "loans_credit", name: "Loans & credit", account_types: [type("home_loan", "Home loan")] },
      ],
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CategoriesPage />
    </QueryClientProvider>,
  );
}

test("groups Categories under Asset and Liability with their Account types", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(CATALOGUE)));
  renderPage();
  const asset = await screen.findByRole("region", { name: "Asset" });
  expect(within(asset).getByText("Cash")).toBeInTheDocument();
  expect(within(asset).getByText(/Savings account/)).toBeInTheDocument();
  expect(within(asset).getByText("Vehicles")).toBeInTheDocument();
  const liability = screen.getByRole("region", { name: "Liability" });
  expect(within(liability).getByText("Loans & credit")).toBeInTheDocument();
  expect(within(liability).getByText(/Home loan/)).toBeInTheDocument();
  expect(within(liability).getByText(/simple balance/)).toBeInTheDocument();
});

test("shows an error when the catalogue cannot be loaded", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 500 })));
  renderPage();
  expect(await screen.findByText(/Could not load the Categories/)).toBeInTheDocument();
});

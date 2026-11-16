import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "@/App";
import { AuthGate } from "@/components/auth-gate";
import { createAppRouter } from "@/routes";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

test("signing in at a deep link lands on that same URL (AuthGate sits outside the router)", async () => {
  let authenticated = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/auth/status")
        return json({ setup_required: false, totp_required: false, authenticated });
      if (path === "/api/auth/login") {
        authenticated = true;
        return json({ setup_required: false, totp_required: false, authenticated });
      }
      if (path === "/api/catalogue") return json({ version: 1, classes: [] });
      if (path === "/health") return json({ status: "healthy", git_sha: "abc1234", schema_version: "0004" });
      return json({});
    }),
  );
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/categories"] }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AuthGate>
        <App router={router} />
      </AuthGate>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(screen.queryByTestId("content")).not.toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/categories");

  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "synthetic-test-passphrase" } });
  fireEvent.click(screen.getByRole("button", { name: /^Sign in$/ }));

  expect(await screen.findByRole("heading", { name: "Categories" })).toBeInTheDocument();
  await waitFor(() => expect(router.state.location.pathname).toBe("/categories"));
});

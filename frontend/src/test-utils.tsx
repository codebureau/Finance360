import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { fireEvent, render, screen } from "@testing-library/react";

import App from "@/App";
import { createAppRouter } from "@/routes";

/** Renders the routed app at `path` with an in-memory history; returns the router to inspect. */
export async function renderAppAt(
  path = "/position",
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createAppRouter(history);
  render(
    <QueryClientProvider client={client}>
      <App router={router} />
    </QueryClientProvider>,
  );
  // The router loads asynchronously; wait for the shell before a test starts querying.
  await screen.findByTestId("content");
  return { router, history };
}

/** Opens every closed "Show working" disclosure on the page, once the result has loaded. */
export async function openAllWorking() {
  const buttons = await screen.findAllByRole("button", { name: /^Show working/ });
  for (const button of buttons) fireEvent.click(button);
}

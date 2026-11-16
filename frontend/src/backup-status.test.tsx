import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { BackupStatus } from "./components/backup-status";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString().replace("Z", "");

const run = (over: Record<string, unknown>) => ({
  id: 1,
  reason: "nightly",
  started_at: minutesAgo(10),
  finished_at: minutesAgo(9),
  status: "success",
  snapshot_id: "abc",
  size_bytes: 12_345,
  shipped: true,
  error: null,
  ...over,
});

const status = (over: Record<string, unknown>) => ({
  configured: true,
  remote_configured: true,
  stale: false,
  stale_after_hours: 36,
  latest: null,
  last_success: null,
  maintenance: null,
  ...over,
});

function renderStatus(body: unknown, posts: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      if (request.method === "POST") {
        posts.push(new URL(request.url).pathname);
        return Promise.resolve(json(run({ reason: "manual", status: "running" }), 202));
      }
      return Promise.resolve(json(body));
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BackupStatus />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

test("shows the last success with its age, size and off-site copy", async () => {
  const ok = run({});
  renderStatus(status({ latest: ok, last_success: ok }));
  expect(
    await screen.findByText(/Last backup: success, 9 minutes ago, 12.3 kB, copied off-site/),
  ).toBeInTheDocument();
});

test("a failed latest attempt is shown with the reason, beside the last good backup", async () => {
  const ok = run({});
  const bad = run({ id: 2, status: "failed", error: "copy failed: remote unreachable", shipped: false });
  renderStatus(status({ latest: bad, last_success: ok }));
  expect(
    await screen.findByText(/Last successful backup 9 minutes ago. Latest attempt failed: copy failed: remote unreachable/),
  ).toBeInTheDocument();
});

test("a stale backup with no success says so", async () => {
  renderStatus(status({ stale: true }));
  expect(await screen.findByText(/No successful backup yet/)).toBeInTheDocument();
});

test("an instance without a key says backups are not set up and offers no button", async () => {
  renderStatus(status({ configured: false }));
  expect(await screen.findByText("Backups are not set up on this instance.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Back up now" })).not.toBeInTheDocument();
});

test("a refused Back up now shows the server's friendly reason", async () => {
  const ok = run({});
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) =>
      Promise.resolve(
        request.method === "POST"
          ? json({ message: "A backup or maintenance run is already running." }, 409)
          : json(status({ latest: ok, last_success: ok })),
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BackupStatus />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Back up now" }));
  expect(
    await screen.findByText("A backup or maintenance run is already running."),
  ).toBeInTheDocument();
});

test("Back up now starts a manual run", async () => {
  const posts: string[] = [];
  const ok = run({});
  renderStatus(status({ latest: ok, last_success: ok }), posts);
  fireEvent.click(await screen.findByRole("button", { name: "Back up now" }));
  await vi.waitFor(() => expect(posts).toEqual(["/api/backup/run"]));
});

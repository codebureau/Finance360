import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AccountMenu } from "@/components/account-menu";
import { AuthGate } from "@/components/auth-gate";
import { SecurityPage } from "@/components/security-page";

type Status = { setup_required: boolean; totp_required: boolean; authenticated: boolean };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Call = { method: string; path: string; body?: Record<string, unknown> };

/** A tiny backend behind fetch: `status` is what /api/auth/status returns, `routes` the rest. */
function backend(
  initial: Status,
  routes: Record<string, (body?: Record<string, unknown>) => Response> = {},
) {
  let status = initial;
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const text = await request.text();
      const body = text ? (JSON.parse(text) as Record<string, unknown>) : undefined;
      calls.push({ method: request.method, path, body });
      if (path === "/api/auth/status") return json(status);
      const key = `${request.method} ${path}`;
      if (routes[key]) return routes[key](body);
      return new Response("?", { status: 500 });
    }),
  );
  return {
    calls,
    setStatus: (next: Status) => {
      status = next;
    },
  };
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return client;
}

afterEach(() => vi.unstubAllGlobals());

const SIGNED_OUT: Status = { setup_required: false, totp_required: false, authenticated: false };
const SIGNED_IN: Status = { ...SIGNED_OUT, authenticated: true };

test("a signed-in visitor goes straight to the app", async () => {
  backend(SIGNED_IN);
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  expect(await screen.findByText("the app")).toBeInTheDocument();
});

test("a signed-out visitor sees the login page and never the app", async () => {
  backend(SIGNED_OUT);
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(screen.queryByText("the app")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Authenticator code")).not.toBeInTheDocument();
});

test("logging in sends the password and the trust-device choice, then shows the app", async () => {
  const api = backend(SIGNED_OUT, {
    "POST /api/auth/login": () => {
      api.setStatus(SIGNED_IN);
      return json(SIGNED_IN);
    },
  });
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  fireEvent.change(await screen.findByLabelText("Password"), {
    target: { value: "synthetic-test-passphrase" },
  });
  fireEvent.click(screen.getByLabelText("Keep me signed in on this device"));
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("the app")).toBeInTheDocument();
  const login = api.calls.find((c) => c.path === "/api/auth/login");
  expect(login?.body).toEqual({
    password: "synthetic-test-passphrase",
    totp: null,
    trust_device: true,
  });
});

test("the code field appears up front when an authenticator is on, and wrong credentials say so", async () => {
  backend({ ...SIGNED_OUT, totp_required: true }, {
    "POST /api/auth/login": () => json({ message: "Invalid credentials." }, 401),
  });
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("Invalid credentials.")).toBeInTheDocument();
  expect(screen.queryByText("the app")).not.toBeInTheDocument();
});

test("a locked login shows the wait message", async () => {
  backend(SIGNED_OUT, {
    "POST /api/auth/login": () =>
      json({ message: "Too many attempts. Try again in 60 seconds." }, 429),
  });
  renderWith(<AuthGate>x</AuthGate>);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText(/Try again in 60 seconds/)).toBeInTheDocument();
});

test("first run asks for the setup code and a matching password, then offers the authenticator", async () => {
  const api = backend(
    { setup_required: true, totp_required: false, authenticated: false },
    {
      "POST /api/auth/setup": () => {
        api.setStatus(SIGNED_IN);
        return json(SIGNED_IN);
      },
      "GET /api/auth/security": () => json({ totp_enabled: false }),
    },
  );
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  fireEvent.change(await screen.findByLabelText("Setup code"), { target: { value: "code-1" } });
  fireEvent.change(screen.getByLabelText(/^New password/), {
    target: { value: "synthetic-test-passphrase" },
  });
  fireEvent.change(screen.getByLabelText(/^Repeat the password/), {
    target: { value: "synthetic-test-different" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create password and sign in" }));
  expect(await screen.findByText("The two passwords differ.")).toBeInTheDocument();
  expect(api.calls.some((c) => c.path === "/api/auth/setup")).toBe(false);

  fireEvent.change(screen.getByLabelText(/^Repeat the password/), {
    target: { value: "synthetic-test-passphrase" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create password and sign in" }));
  expect(await screen.findByRole("heading", { name: "You are signed in" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Set up an authenticator app" })).toBeInTheDocument();
  expect(api.calls.find((c) => c.path === "/api/auth/setup")?.body).toEqual({
    setup_code: "code-1",
    password: "synthetic-test-passphrase",
    trust_device: false,
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue to Finance360" }));
  expect(await screen.findByText("the app")).toBeInTheDocument();
});

test("a wrong setup code is explained", async () => {
  backend(
    { setup_required: true, totp_required: false, authenticated: false },
    { "POST /api/auth/setup": () => json({ message: "Setup code not recognised." }, 403) },
  );
  renderWith(<AuthGate>x</AuthGate>);
  fireEvent.change(await screen.findByLabelText("Setup code"), { target: { value: "nope" } });
  fireEvent.change(screen.getByLabelText(/^New password/), { target: { value: "p".repeat(14) } });
  fireEvent.change(screen.getByLabelText(/^Repeat the password/), { target: { value: "p".repeat(14) } });
  fireEvent.click(screen.getByRole("button", { name: "Create password and sign in" }));
  expect(await screen.findByText("Setup code not recognised.")).toBeInTheDocument();
});

test("a request refused as signed out sends a signed-in screen back to login", async () => {
  const api = backend(SIGNED_IN);
  renderWith(
    <AuthGate>
      <p>the app</p>
    </AuthGate>,
  );
  expect(await screen.findByText("the app")).toBeInTheDocument();
  api.setStatus(SIGNED_OUT);
  window.dispatchEvent(new Event("finance360:signed-out"));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

test("signing out calls logout and returns to the login page", async () => {
  const api = backend(SIGNED_IN, {
    "POST /api/auth/logout": () => {
      api.setStatus(SIGNED_OUT);
      return new Response(null, { status: 204 });
    },
  });
  renderWith(
    <AuthGate>
      <AccountMenu />
    </AuthGate>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(api.calls.some((c) => c.path === "/api/auth/logout")).toBe(true);
});

test("enrolling an authenticator shows a QR code and the key, then confirms with a code", async () => {
  let enabled = false;
  const api = backend(SIGNED_IN, {
    "GET /api/auth/security": () => json({ totp_enabled: enabled }),
    "POST /api/auth/totp/begin": () =>
      json({ secret: "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP", uri: "otpauth://totp/Finance360:owner?secret=X" }),
    "POST /api/auth/totp/confirm": (body) => {
      if (body?.code !== "123456")
        return json({ errors: [{ field: "code", message: "That code is not right." }] }, 422);
      enabled = true;
      return new Response(null, { status: 204 });
    },
  });
  const { container } = (() => {
    renderWith(<SecurityPage />);
    return { container: document.body };
  })();
  fireEvent.click(await screen.findByRole("button", { name: "Set up an authenticator app" }));
  expect(await screen.findByText("ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Set up an authenticator app" })).toBeInTheDocument();
  expect(container.querySelector("svg")).not.toBeNull(); // the QR code
  fireEvent.change(screen.getByLabelText(/^Code from the app/), { target: { value: "000000" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm and turn on" }));
  expect(await screen.findByText("That code is not right.")).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Set up an authenticator app" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/^Code from the app/), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm and turn on" }));
  expect(await screen.findByRole("button", { name: "Turn off authenticator" })).toBeInTheDocument();
  expect(api.calls.filter((c) => c.path === "/api/auth/totp/confirm")).toHaveLength(2);
});

test("disabling the authenticator sends the password and a code", async () => {
  let enabled = true;
  const api = backend(SIGNED_IN, {
    "GET /api/auth/security": () => json({ totp_enabled: enabled }),
    "POST /api/auth/totp/disable": () => {
      enabled = false;
      return new Response(null, { status: 204 });
    },
  });
  renderWith(<SecurityPage />);
  // Destructive: asks first, naming what is turned off, and Cancel changes nothing.
  fireEvent.click(await screen.findByRole("button", { name: "Turn off authenticator" }));
  const alert = within(await screen.findByRole("alertdialog", { name: "Turn off the authenticator app?" }));
  fireEvent.click(alert.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(api.calls.some((c) => c.path === "/api/auth/totp/disable")).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Turn off authenticator" }));
  const confirm = within(await screen.findByRole("alertdialog"));
  fireEvent.change(confirm.getByLabelText("Current code"), { target: { value: "654321" } });
  fireEvent.change(confirm.getByLabelText("Password"), { target: { value: "synthetic-test-passphrase" } });
  fireEvent.click(confirm.getByRole("button", { name: "Turn off" }));
  expect(await screen.findByRole("button", { name: "Set up an authenticator app" })).toBeInTheDocument();
  expect(api.calls.find((c) => c.path === "/api/auth/totp/disable")?.body).toEqual({
    password: "synthetic-test-passphrase",
    code: "654321",
  });
});

test("changing the password checks the repeat, shows server field errors and confirms success", async () => {
  let ok = false;
  const api = backend(SIGNED_IN, {
    "GET /api/auth/security": () => json({ totp_enabled: false }),
    "POST /api/auth/password": (body) => {
      if (body?.current_password !== "synthetic-test-passphrase")
        return json(
          { errors: [{ field: "current_password", message: "That is not your current password." }] },
          422,
        );
      ok = true;
      return new Response(null, { status: 204 });
    },
  });
  renderWith(<SecurityPage />);
  const fill = (label: RegExp, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(await screen.findByRole("button", { name: "Change password" }));
  const submit = () =>
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Change password" }));

  fill(/^Current password/, "wrong-wrong-wrong");
  fill(/^New password/, "another-synthetic-passphrase");
  fill(/^Repeat the new password/, "another-synthetic-different");
  submit();
  expect(await screen.findByText("The two passwords differ.")).toBeInTheDocument();

  fill(/^Repeat the new password/, "another-synthetic-passphrase");
  submit();
  expect(await screen.findByText("That is not your current password.")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument(); // stays open on failure

  fill(/^Current password/, "synthetic-test-passphrase");
  submit();
  await waitFor(() => expect(ok).toBe(true));
  expect(await screen.findByText(/Your other devices have been signed out/)).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.calls.filter((c) => c.path === "/api/auth/password")).toHaveLength(2);
});

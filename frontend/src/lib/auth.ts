import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type AuthStatus = components["schemas"]["StatusOut"];
export type TotpBegin = components["schemas"]["TotpBeginOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const { data, error, response } = await client.GET("/api/auth/status");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function logIn(input: {
  password: string;
  totp: string;
  trustDevice: boolean;
}): Promise<AuthStatus> {
  const { data, error, response } = await client.POST("/api/auth/login", {
    body: {
      password: input.password,
      totp: input.totp.trim() || null,
      trust_device: input.trustDevice,
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function setUp(input: {
  setupCode: string;
  password: string;
  trustDevice: boolean;
}): Promise<AuthStatus> {
  const { data, error, response } = await client.POST("/api/auth/setup", {
    body: {
      setup_code: input.setupCode,
      password: input.password,
      trust_device: input.trustDevice,
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function logOut(): Promise<void> {
  const { error, response } = await client.POST("/api/auth/logout");
  if (!response.ok) throw toApiError(error, response.status);
}

export async function fetchSecurity(): Promise<{ totp_enabled: boolean }> {
  const { data, error, response } = await client.GET("/api/auth/security");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function changePassword(current: string, next: string): Promise<void> {
  const { error, response } = await client.POST("/api/auth/password", {
    body: { current_password: current, new_password: next },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

export async function beginTotp(): Promise<TotpBegin> {
  const { data, error, response } = await client.POST("/api/auth/totp/begin");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function confirmTotp(code: string): Promise<void> {
  const { error, response } = await client.POST("/api/auth/totp/confirm", { body: { code } });
  if (!response.ok) throw toApiError(error, response.status);
}

export async function disableTotp(password: string, code: string): Promise<void> {
  const { error, response } = await client.POST("/api/auth/totp/disable", {
    body: { password, code },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

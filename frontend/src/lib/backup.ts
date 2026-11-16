import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type BackupStatus = components["schemas"]["BackupStatusOut"];
export type BackupRun = components["schemas"]["BackupRunOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchBackupStatus(): Promise<BackupStatus> {
  const { data, error, response } = await client.GET("/api/backup/status");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function runBackup(): Promise<BackupRun> {
  const { data, error, response } = await client.POST("/api/backup/run");
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** "5 minutes ago", "3 hours ago": coarse on purpose, the exact time is in the title. */
export function ago(isoUtc: string, now: Date = new Date()): string {
  const seconds = Math.max(0, (now.getTime() - new Date(`${isoUtc}Z`).getTime()) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

/** Bytes as a short human string (decimal units). */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

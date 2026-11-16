import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type AiStatus = components["schemas"]["AiStatusOut"];
export type AiLogEntry = components["schemas"]["AiLogOut"];
export type AskSource = components["schemas"]["SourceOut"];
export type AskAnswer = components["schemas"]["AskOut"];
export type AskPassage = components["schemas"]["PassageOut"];
export type AskSearch = components["schemas"]["PassagesOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export const AI_STATUS_KEY = ["ai", "status"] as const;
export const RULE_CAPTURE_STATUS_KEY = [
  ...AI_STATUS_KEY,
  "rule_capture",
] as const;
export const AI_LOG_KEY = ["ai", "log"] as const;

/** Limits the server enforces (ADR-0021 section 8); the screen mirrors them so it never sends a refusal. */
export const QUESTION_MAX = 500;
export const EXCHANGES_KEPT = 3;

/** Whether a task may call the model now. The daily question limit stops Ask only. */
export async function fetchAiStatus(
  task: "ask" | "rule_capture" = "ask",
): Promise<AiStatus> {
  const { data, error, response } = await client.GET("/api/ai/status", {
    params: { query: { task } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchAiLog(limit = 100): Promise<AiLogEntry[]> {
  const { data, error, response } = await client.GET("/api/ai/log", {
    params: { query: { limit } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/**
 * One question and what it got: the Passages the search found (at once), and, when a model is set up,
 * the written answer that follows (ADR-0023 section 4). The unit the thread keeps, at most
 * `EXCHANGES_KEPT` of them.
 */
export type Exchange = {
  id: number;
  question: string;
  search: AskSearch;
  /** `none` at Search only; `pending` while the model writes; `failed` leaves the Passages in place. */
  writing: "none" | "pending" | "done" | "failed";
  answer: AskAnswer | null;
  /** Why a written answer did not come, in calm words. */
  note: string | null;
};

/** Where the person is, as the server wants it: the route pattern (never an id) and a Guide page slug. */
export type Whereabouts = { screen?: string; guide_slug?: string };

function body(question: string, thread: Exchange[], where: Whereabouts) {
  // Only exchanges with a written answer can be earlier messages: the server wants user/assistant pairs.
  const history = thread
    .slice(-EXCHANGES_KEPT)
    .filter((e) => e.answer !== null)
    .flatMap((e) => [
      { role: "user" as const, text: e.question },
      { role: "assistant" as const, text: e.answer?.answer ?? "" },
    ]);
  return {
    question,
    history,
    screen: where.screen ?? null,
    guide_slug: where.guide_slug ?? null,
  };
}

/** Step one: search the Guide. No model is involved, so this is always available. */
export async function searchGuide(
  question: string,
  thread: Exchange[],
  where: Whereabouts,
): Promise<AskSearch> {
  const { data, error, response } = await client.POST("/api/ask", {
    body: body(question, thread, where),
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Step two, only when a model is set up: the written answer. The server retrieves the Passages itself. */
export async function writeAnswer(
  question: string,
  thread: Exchange[],
  where: Whereabouts,
): Promise<AskAnswer> {
  const { data, error, response } = await client.POST("/api/ask/answer", {
    body: body(question, thread, where),
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The thread after one more exchange: the last `EXCHANGES_KEPT`, oldest first. */
export function appendExchange(
  thread: Exchange[],
  exchange: Exchange,
): Exchange[] {
  return [...thread, exchange].slice(-EXCHANGES_KEPT);
}

/** "6 December 2026" from a calendar date, whatever the browser's zone. */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-AU", {
    timeZone: "UTC",
    dateStyle: "long",
  }).format(new Date(`${iso}T00:00:00Z`));
}

/** Why a written answer is paused, in a sentence. Search always works. */
export function answerPausedText(status: AiStatus): string {
  if (status.reason === "monthly_cap")
    return `Written answers have reached this month's spending limit. They start again on ${status.resets_on ? formatDate(status.resets_on) : "the first of next month"}. Search still works.`;
  if (status.reason === "daily_limit")
    return `Written answers have reached today's question limit. They start again on ${status.resets_on ? formatDate(status.resets_on) : "tomorrow"}. Search still works.`;
  return "Written answers are not available right now. Search still works.";
}

/** Why "Read the page for me" used the word matcher though a model is set up; null when it is not. */
export function readerPausedText(status: AiStatus): string | null {
  if (!status.answering || status.enabled) return null;
  if (status.reason === "monthly_cap")
    return `AI has reached this month's spending limit, so the page is read by matching words until ${status.resets_on ? formatDate(status.resets_on) : "the first of next month"}.`;
  return "AI is not available right now, so the page is read by matching words.";
}

/** Millionths of a US dollar as text, in integers only (never a float): 1500 -> "US$0.0015". */
export function costText(micros: number): string {
  const whole = Math.trunc(micros / 1_000_000);
  const frac = String(micros % 1_000_000)
    .padStart(6, "0")
    .replace(/0+$/, "")
    .padEnd(2, "0");
  return `US$${whole}.${frac}`;
}

type Variant = "ok" | "info" | "flagged" | "closed";

/** One outcome, one badge (docs/ui-conventions.md). */
export function outcomeBadge(outcome: string): {
  variant: Variant;
  label: string;
} {
  switch (outcome) {
    case "ok":
      return { variant: "ok", label: "Completed" };
    case "unsupported":
      return { variant: "flagged", label: "Not in the Guide" };
    case "refused_cap":
      return { variant: "closed", label: "Refused: limit" };
    default:
      return { variant: "flagged", label: "Failed" };
  }
}

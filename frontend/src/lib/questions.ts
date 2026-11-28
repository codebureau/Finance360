import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  type LucideIcon,
} from "lucide-react";
import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type Question = components["schemas"]["QuestionOut"];
export type Questions = components["schemas"]["QuestionsOut"];
export type NextStep = components["schemas"]["NextStepOut"];
export type QuestionDetail = components["schemas"]["QuestionDetailOut"];
export type QuestionInputView = components["schemas"]["InputOut"];
export type QuestionReason = components["schemas"]["ReasonOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export const QUESTIONS_KEY = ["questions"] as const;

export async function fetchQuestions(): Promise<Questions> {
  const { data, error, response } = await client.GET("/api/questions");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export const questionKey = (key: string) => ["questions", key] as const;

export async function fetchQuestion(key: string): Promise<QuestionDetail> {
  const { data, error, response } = await client.GET("/api/questions/{key}", { params: { path: { key } } });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The marker a fix link from a Question carries (`/accounts/9?from=q.own-and-owe`). */
export function fromQuestion(key: string): string {
  return `q.${key}`;
}

/** The Question key inside a return marker, or `null` for any other marker. */
export function questionOfMarker(marker: string): string | null {
  return marker.startsWith("q.") ? marker.slice(2) : null;
}

/** The five Readiness states (ADR-0032 section 2). Never a number: a state, never a score. */
export type ReadinessState = "ready" | "partly_ready" | "not_ready" | "not_started" | "could_not_check";

/** The status tone a state wears (ADR-0031 section 5): colour is never the only cue, so each also
 * has an icon and its word. */
export type StatusTone = "success" | "warning" | "destructive" | "info" | "muted";

export const READINESS: Record<ReadinessState, { tone: StatusTone; icon: LucideIcon }> = {
  ready: { tone: "success", icon: CircleCheck },
  partly_ready: { tone: "warning", icon: CircleAlert },
  not_ready: { tone: "destructive", icon: CircleX },
  not_started: { tone: "info", icon: CircleDashed },
  could_not_check: { tone: "muted", icon: CircleHelp },
};

/** The look of a state, falling back to Could not check for a word this client does not know. */
export function readiness(state: string) {
  return READINESS[state as ReadinessState] ?? READINESS.could_not_check;
}

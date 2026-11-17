import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type RuleFigureYear = components["schemas"]["YearOut"];
export type RuleFigure = components["schemas"]["FigureOut"];
export type RuleFigureCapture = components["schemas"]["CaptureOut"];
export type NoLongerFits = components["schemas"]["NoLongerFitOut"];
export type RuleFigureProposal = components["schemas"]["ProposalOut"];
export type RuleFigureReading = components["schemas"]["ProposalsOut"];
export type RuleFigureInput = Omit<
  components["schemas"]["CaptureIn"],
  "financial_year" | "key" | "jurisdiction"
>;

const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

export async function fetchRuleFigures(
  financialYear: number,
): Promise<RuleFigureYear> {
  const { data, error, response } = await client.GET("/api/rule-figures", {
    params: { query: { financial_year: financialYear } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The most the server reads from a pasted page (ADR-0022); the screen mirrors it. */
export const PAGE_TEXT_MAX = 40_000;

/**
 * Read a pasted official page for these figures: by AI when a model is set up and may be used,
 * otherwise by matching words (ADR-0023). Stores nothing: each answer is a candidate that passed
 * the server's checks, or "not found; enter it" with the reason.
 */
export async function proposeRuleFigures(
  financialYear: number,
  keys: string[],
  pageText: string,
): Promise<RuleFigureReading> {
  const { data, error, response } = await client.POST(
    "/api/rule-figures/proposals",
    {
      body: {
        jurisdiction: "au",
        financial_year: financialYear,
        keys,
        page_text: pageText,
      },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function captureRuleFigure(
  financialYear: number,
  key: string,
  input: RuleFigureInput,
): Promise<RuleFigure> {
  const { data, error, response } = await client.POST("/api/rule-figures", {
    body: { ...input, jurisdiction: "au", financial_year: financialYear, key },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function correctRuleFigure(
  captureId: number,
  input: RuleFigureInput,
): Promise<RuleFigure> {
  const { data, error, response } = await client.PUT(
    "/api/rule-figures/{capture_id}",
    {
      params: { path: { capture_id: captureId } },
      body: input,
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function withdrawRuleFigure(
  captureId: number,
): Promise<RuleFigure> {
  const { data, error, response } = await client.DELETE(
    "/api/rule-figures/{capture_id}",
    {
      params: { path: { capture_id: captureId } },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** `/rule-figures?fy=2027&missing=1`: the year and the Missing-only filter are URL search params. */
export type RuleFiguresSearch = { fy?: number; missing?: true };

export function parseRuleFiguresSearch(
  search: Record<string, unknown>,
): RuleFiguresSearch {
  const out: RuleFiguresSearch = {};
  const fy = Number(search.fy);
  if (Number.isInteger(fy) && fy >= 1990 && fy <= 2200) out.fy = fy;
  if (
    search.missing === true ||
    search.missing === 1 ||
    search.missing === "1" ||
    search.missing === "true"
  )
    out.missing = true;
  return out;
}

/** The groups a figure screen lists, in order; a key joins the first group whose prefix it starts with. */
const GROUPS: { title: string; prefixes: string[] }[] = [
  {
    title: "Tax rates and offsets",
    prefixes: [
      "tax.resident_brackets",
      "tax.lito.",
      "tax.sbito.",
      "tax.foreign_offset.",
      "tax.cgt.",
      "tax.standard_work_deduction",
    ],
  },
  { title: "Medicare", prefixes: ["tax.medicare.", "tax.mls."] },
  { title: "HELP", prefixes: ["tax.help."] },
  { title: "Car", prefixes: ["tax.car."] },
  { title: "Home office", prefixes: ["tax.home_office."] },
  { title: "Depreciation", prefixes: ["tax.depreciation."] },
];

export function groupFigures(
  figures: readonly RuleFigure[],
): { title: string; figures: RuleFigure[] }[] {
  const groups = [
    ...GROUPS.map((g) => ({ title: g.title, figures: [] as RuleFigure[] })),
    { title: "Other", figures: [] as RuleFigure[] },
  ];
  for (const figure of figures) {
    const i = GROUPS.findIndex((g) =>
      g.prefixes.some((p) => figure.key.startsWith(p)),
    );
    groups[i === -1 ? GROUPS.length : i].figures.push(figure);
  }
  return groups.filter((g) => g.figures.length > 0);
}

/** "1234.5" as "1,234.5": text only, so nothing is ever rounded through a float. */
function grouped(value: string): string {
  const [whole, fraction] = value.split(".");
  const withCommas = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? withCommas : `${withCommas}.${fraction}`;
}

/** A figure's value in words: "$0.88 per km", "16.5%", "5,000". Brackets stay as typed. */
export function figureText(
  figure: Pick<RuleFigure, "kind" | "unit">,
  value: string,
): string {
  if (figure.kind === "money")
    return `$${grouped(value)}${figure.unit ? ` per ${figure.unit}` : ""}`;
  if (figure.kind === "int") return grouped(value);
  return value;
}

export type CarryPreview = components["schemas"]["CarryPreviewOut"];
export type CarryProposal = components["schemas"]["CarryProposalOut"];
export type PageConfirmation = components["schemas"]["ConfirmPageOut"];

/** A figure written by "Start from <FY>" that nobody has checked against the page yet. */
export function isCarried(figure: RuleFigure): boolean {
  return figure.capture?.method === "carried";
}

/** What "Start from <FY>" would write for a year. Writes nothing. */
export async function fetchCarryPreview(
  financialYear: number,
): Promise<CarryPreview> {
  const { data, error, response } = await client.GET(
    "/api/rule-figures/carry",
    { params: { query: { financial_year: financialYear } } },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Accept the preview: one provisional carried capture per proposed figure, all or none. */
export async function carryRuleFigures(
  financialYear: number,
): Promise<CarryPreview> {
  const { data, error, response } = await client.POST(
    "/api/rule-figures/carry",
    { body: { jurisdiction: "au", financial_year: financialYear } },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** One carried figure confirmed as unchanged, without re-typing it. */
export async function confirmCarriedFigure(
  captureId: number,
): Promise<RuleFigure> {
  const { data, error, response } = await client.POST(
    "/api/rule-figures/{capture_id}/confirm",
    { params: { path: { capture_id: captureId } } },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Every carried figure from one source page confirmed as unchanged, in one action. */
export async function confirmCarriedPage(
  financialYear: number,
  pageUrl: string,
): Promise<PageConfirmation> {
  const { data, error, response } = await client.POST(
    "/api/rule-figures/confirm-page",
    {
      body: {
        jurisdiction: "au",
        financial_year: financialYear,
        page_url: pageUrl,
      },
    },
  );
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** Today as `YYYY-MM-DD` in the browser's own calendar (the default for "retrieved on"). */
export function todayIso(now = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

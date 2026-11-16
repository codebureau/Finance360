import createClient from "openapi-fetch";

import type { components, paths } from "@/api/schema";
import { toApiError } from "@/lib/household";

export type LibraryDocument = components["schemas"]["LibraryDocumentOut"];
export type LibraryDocumentDetail = components["schemas"]["LibraryDocumentDetailOut"];
export type LibraryDocumentPage = components["schemas"]["LibraryDocumentPage"];
export type LibraryLink = components["schemas"]["LibraryLinkOut"];
export type LibraryLinkedDocument = components["schemas"]["LibraryLinkedDocumentOut"];
export type LibraryAdded = components["schemas"]["LibraryAddedOut"];
export type LibraryKind = components["schemas"]["LibraryKindOut"];
export type LibraryCheck = components["schemas"]["LibraryCheckOut"];

const client = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** What the server accepts, by file extension (a browser leaves `File.type` empty for some). */
const MEDIA_TYPES: Record<string, "application/pdf" | "image/png" | "image/jpeg" | "text/plain" | "text/csv"> = {
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  txt: "text/plain",
  csv: "text/csv",
};

/** The file types the Library takes, as the file picker's `accept` list. */
export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.txt,.csv,application/pdf,image/png,image/jpeg,text/plain,text/csv";

/** What each stored media type is called, in the order the Type filter lists them. */
export const MEDIA_TYPE_NAMES: { value: string; name: string }[] = [
  { value: "application/pdf", name: "PDF" },
  { value: "image/png", name: "PNG image" },
  { value: "image/jpeg", name: "JPEG image" },
  { value: "text/plain", name: "Text" },
  { value: "text/csv", name: "CSV" },
];

export const mediaTypeName = (mediaType: string) =>
  MEDIA_TYPE_NAMES.find((m) => m.value === mediaType)?.name ?? mediaType;

/** The file's media type: the browser's, else from its extension; `null` when it is not one we take. */
export function fileMediaType(file: File): string | null {
  const known = Object.values(MEDIA_TYPES) as string[];
  if (known.includes(file.type)) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return MEDIA_TYPES[extension] ?? null;
}

/** A size in words: bytes, KB or MB (1 KB is 1,000 bytes). */
export function fileSizeText(bytes: number): string {
  if (bytes < 1000) return `${bytes} bytes`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** What a document's text status means to the person, in words. */
export const TEXT_STATUS_TEXT: Record<string, string> = {
  extracted: "Text read, so it can be found by search",
  none: "No text to read (an image or a scan)",
  too_large: "Too large to read its text",
  failed: "Its text could not be read",
};

/** What a file whose health check failed is called, in words. */
export const PROBLEM_TEXT: Record<string, string> = { missing: "File missing", changed: "File changed" };

/** Where the file is served from, behind the sign-in. `download` saves it instead of showing it. */
export const libraryFileUrl = (documentId: number, download = false) =>
  `/api/library/documents/${documentId}/file${download ? "?download=true" : ""}`;

// --- the Library screen's state, kept in the URL ----------------------------------------------

export const PAGE_SIZE = 25;

/** `/library?q=&type=&kind=&unlinked=1&page=`. `kind` and `unlinked` are never both set. */
export type LibrarySearch = {
  q?: string;
  type?: string;
  kind?: string;
  unlinked?: true;
  page?: number;
};

/** Validate untrusted search params: anything malformed is dropped. Keys stay present as undefined so
 *  the router overwrites an invalid raw value rather than merging it back in. */
export function parseLibrarySearch(search: Record<string, unknown>): LibrarySearch {
  const text = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : undefined);
  const page = Number(search.page);
  const unlinked = search.unlinked === true || search.unlinked === "true" || search.unlinked === 1 || search.unlinked === "1";
  const type = text(search.type);
  return {
    q: text(search.q),
    type: type && MEDIA_TYPE_NAMES.some((m) => m.value === type) ? type : undefined,
    kind: unlinked ? undefined : text(search.kind),
    unlinked: unlinked ? true : undefined,
    page: Number.isInteger(page) && page > 1 ? page : undefined,
  };
}

// --- requests ---------------------------------------------------------------------------------

export async function fetchLibraryDocuments(
  search: LibrarySearch,
  limit = PAGE_SIZE,
): Promise<LibraryDocumentPage> {
  const { data, error, response } = await client.GET("/api/library/documents", {
    params: {
      query: {
        q: search.q,
        media_type: search.type,
        linked_kind: search.kind,
        unlinked: search.unlinked,
        limit,
        offset: ((search.page ?? 1) - 1) * limit,
      },
    },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchLibraryKinds(): Promise<LibraryKind[]> {
  const { data, error, response } = await client.GET("/api/library/kinds");
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function fetchLibraryDocument(id: number): Promise<LibraryDocumentDetail> {
  const { data, error, response } = await client.GET("/api/library/documents/{document_id}", {
    params: { path: { document_id: id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

/** The file travels as the raw request body, its Content-Type the media type (as the Imports). */
const rawBody = (body: unknown) => body as BodyInit;

/** Put a file in the Library. The same bytes again are the same document (`already_in_library`). */
export async function addLibraryDocument(file: File): Promise<LibraryAdded> {
  const mediaType = fileMediaType(file);
  if (!mediaType) {
    throw toApiError(
      { errors: [{ field: "file", message: "Add a PDF, PNG image, JPEG image, text or CSV file." }] },
      422,
    );
  }
  const { data, error, response } = await client.POST("/api/library/documents", {
    params: { query: { file_name: file.name } },
    body: file as unknown as string,
    bodySerializer: rawBody,
    headers: { "Content-Type": mediaType },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function deleteLibraryDocument(id: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/library/documents/{document_id}", {
    params: { path: { document_id: id } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** A record's documents, including those linked to an earlier or later version of it. */
export async function fetchLinkedDocuments(kind: string, id: number): Promise<LibraryLinkedDocument[]> {
  const { data, error, response } = await client.GET("/api/library/links", {
    params: { query: { kind, id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function linkLibraryDocument(
  documentId: number,
  kind: string,
  targetId: number,
): Promise<LibraryLink> {
  const { data, error, response } = await client.POST("/api/library/links", {
    body: { document_id: documentId, target_kind: kind, target_id: targetId },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

export async function unlinkLibraryDocument(linkId: number): Promise<void> {
  const { error, response } = await client.DELETE("/api/library/links/{link_id}", {
    params: { path: { link_id: linkId } },
  });
  if (!response.ok) throw toApiError(error, response.status);
}

/** Add a file to the Library and link it to a record: what a record's Add does. */
export async function addAndLink(file: File, kind: string, targetId: number): Promise<LibraryAdded> {
  const added = await addLibraryDocument(file);
  try {
    await linkLibraryDocument(added.document.id, kind, targetId);
  } catch (e) {
    const why = e instanceof Error ? e.message : "";
    throw new Error(`'${file.name}' is in the Library but could not be linked. ${why}`.trim());
  }
  return added;
}

/** The health check: every document, or one record's. */
export async function checkLibrary(record?: { kind: string; id: number }): Promise<LibraryCheck> {
  const { data, error, response } = await client.GET("/api/library/check", {
    params: { query: { kind: record?.kind, id: record?.id } },
  });
  if (!data) throw toApiError(error, response.status);
  return data;
}

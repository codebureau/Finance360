import { ApiError } from "@/lib/household";

export const inputClass =
  "min-h-[var(--control-h)] rounded-md border border-input bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

export function FieldMessage({ error, field }: { error: unknown; field: string }) {
  const message = error instanceof ApiError ? error.fields[field] : undefined;
  return message ? <p className="text-sm text-destructive">{message}</p> : null;
}

/** Errors with no field of their own (e.g. a Member that cannot be deleted). */
export function GeneralError({ error }: { error: unknown }) {
  if (!(error instanceof Error)) return null;
  if (error instanceof ApiError && Object.keys(error.fields).length > 0) return null;
  return <p className="text-sm text-destructive">{error.message}</p>;
}

import { cn } from "@/lib/utils";

/**
 * Whether the browser can show a PDF inline, and so jump to a page with `#page=N`. Browsers
 * without a built-in viewer (`navigator.pdfViewerEnabled === false`) offer the page as a label
 * only. Unknown (an old browser) is treated as able: the fragment is harmless where unsupported.
 */
export const canJumpToPage = (): boolean =>
  typeof navigator === "undefined" || navigator.pdfViewerEnabled !== false;

/**
 * A document shown by the browser's own viewer in an `<iframe>` (ADR-0026 section 5): no pdf.js.
 * `src` is the authenticated Library file route, or an object URL for a file not yet saved.
 * `page` opens a PDF at that page; `jump` changes with each click on a page label so asking for
 * the same page twice (after scrolling away) shows it again.
 */
export function DocumentFrame({
  src,
  title,
  page,
  jump,
  className,
}: {
  src: string;
  title: string;
  page?: number | null;
  jump?: number;
  className?: string;
}) {
  const url = page ? `${src}#page=${page}` : src;
  return (
    <iframe
      key={`${src}|${jump ?? 0}`}
      title={title}
      src={url}
      className={cn("h-full w-full rounded-md border bg-background", className)}
    />
  );
}
